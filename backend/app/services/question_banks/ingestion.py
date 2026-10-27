"""Save question banks and reuse existing document extraction and OCR."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.models.question_bank import QuestionBank
from app.services.cv.text import isolated_extract, finalize_text
from app.services.documents.extraction import (
    DocumentExtractionError,
    MAX_FILE_BYTES,
)


def validate_upload(filename: str, data: bytes) -> str:
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()

    if (
        not filename
        or len(filename) > 255
        or any(ord(character) < 32 for character in filename)
    ):
        raise HTTPException(422, "Provide a valid filename.")

    if Path(filename).suffix.lower() not in {".pdf", ".docx"}:
        raise HTTPException(422, "Only PDF and DOCX files are supported.")

    if not data:
        raise HTTPException(422, "The uploaded file is empty.")

    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "Files must be at most 10 MB.")

    return filename


def save_and_extract(bank: QuestionBank, data: bytes) -> dict:
    """Extract from a temporary file; retain only text and source metadata."""
    settings = get_settings()
    extraction_options = {
        "language": settings.cv_ocr_language,
        "page_timeout": settings.cv_ocr_timeout_seconds,
        "document_timeout": settings.cv_document_timeout_seconds,
        "expected_sha256": bank.file_sha256,
    }

    with TemporaryDirectory(prefix="distill-question-bank-") as directory:
        path = Path(directory) / f"source.{bank.file_type}"
        path.write_bytes(data)

        extracted = isolated_extract(path, "native", **extraction_options)

        if any(source.get("ocr_needed") for source in extracted["sources"]):
            extracted = isolated_extract(
                path,
                "ocr",
                extracted=extracted,
                **extraction_options,
            )

        return finalize_text(extracted)


async def upload_question_bank(
    db: AsyncSession,
    recruiter_id: UUID,
    filename: str,
    data: bytes,
) -> QuestionBank:
    filename = validate_upload(filename, data)
    file_hash = sha256(data).hexdigest()

    bank = await db.scalar(
        select(QuestionBank).where(
            QuestionBank.recruiter_id == recruiter_id,
            QuestionBank.file_sha256 == file_hash,
        ).with_for_update()
    )
    if bank is not None:
        # Original files are temporary: re-upload retries failed extraction.
        if bank.is_archived or bank.status != "failed" or bank.error_code == "QUESTION_BANK_INDEX_FAILED":
            raise HTTPException(409, "You have already uploaded this document.")
        bank.status, bank.error_code = "processing", None
        bank.extracted_text, bank.sources, bank.warnings = None, [], []
        bank.chunk_count, bank.index_version = 0, None
    else:
        bank = QuestionBank(
            recruiter_id=recruiter_id,
            filename=filename,
            file_type=Path(filename).suffix.lower().lstrip("."),
            file_size_bytes=len(data),
            file_sha256=file_hash,
            status="processing",
        )
        db.add(bank)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        # Handle another upload of the same file arriving concurrently.
        duplicate = await db.scalar(
            select(QuestionBank.id).where(
                QuestionBank.recruiter_id == recruiter_id,
                QuestionBank.file_sha256 == file_hash,
            )
        )
        if duplicate:
            raise HTTPException(
                409, "You have already uploaded this document.",
            ) from None
        raise

    await db.refresh(bank)

    try:
        result = await run_in_threadpool(save_and_extract, bank, data)
    except DocumentExtractionError as exc:
        bank.status = "failed"
        bank.error_code = exc.code
    except OSError:
        bank.status = "failed"
        bank.error_code = "DOCUMENT_STORAGE_FAILED"
    except Exception:
        bank.status = "failed"
        bank.error_code = "DOCUMENT_EXTRACTION_FAILED"
    else:
        bank.extracted_text = result["text"]
        bank.sources = result["sources"]
        bank.warnings = result["warnings"]

        if result["usable"]:
            bank.status = "pending"
        else:
            bank.status = "failed"
            bank.error_code = "TEXT_UNUSABLE_OR_INCOMPLETE"

    await db.commit()
    await db.refresh(bank)
    return bank
