from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import engine
from app.models.cv import CVDocument, CVFileVersion, CVSource
from app.models.jd import JDConversation, JobDescription
from app.services.google_drive import (
    SUPPORTED_TYPES,
    download_cv,
    list_folder_files,
    validate_saved_document,
)
from app.services.document_extraction import MAX_FILE_BYTES


def verify_local_file(
    relative_path: str,
    source_sha256: str,
) -> tuple[int, str]:
    path = get_settings().cv_storage_path.resolve() / relative_path
    size = path.stat().st_size

    if not 0 < size <= MAX_FILE_BYTES:
        raise ValueError("Stored CV has an invalid size.")

    checksum = sha256(path.read_bytes()).hexdigest()
    if checksum != source_sha256:
        raise ValueError("Stored CV does not match the source checksum.")

    validate_saved_document(path, path.suffix.lower())
    return size, checksum


async def fetch_cv_source(source_id: UUID, recruiter_id: UUID) -> dict:
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}
    errors: list[dict] = []

    lock_key = int.from_bytes(
        sha256(source_id.bytes).digest()[:8],
        byteorder="big",
        signed=True,
    )

    async with engine.connect() as connection:
        owned_source = await connection.scalar(
            select(CVSource.id)
            .join(
                JobDescription,
                CVSource.job_description_id == JobDescription.id,
            )
            .join(
                JDConversation,
                JobDescription.conversation_id == JDConversation.id,
            )
            .where(
                CVSource.id == source_id,
                JDConversation.recruiter_id == recruiter_id,
            )
        )
        if owned_source is None:
            raise HTTPException(status_code=404, detail="CV source not found.")

        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": lock_key},
        )
        await connection.commit()

        if not locked:
            raise HTTPException(
                status_code=409,
                detail="This CV source is already being fetched.",
            )

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
            ) as db:
                source = await db.get(CVSource, source_id)
                if source is None:
                    raise HTTPException(
                        status_code=404,
                        detail="CV source not found.",
                    )

                files = await run_in_threadpool(
                    list_folder_files,
                    source.drive_folder_id,
                )

                for item in files:
                    suffix = Path(item["name"]).suffix.lower()
                    if (
                        item.get("trashed")
                        or suffix not in SUPPORTED_TYPES
                        or item["mimeType"] != SUPPORTED_TYPES[suffix]
                    ):
                        continue

                    document = await db.scalar(
                        select(CVDocument).where(
                            CVDocument.job_description_id
                            == source.job_description_id,
                            CVDocument.drive_file_id == item["id"],
                        )
                    )
                    if document is None:
                        document = CVDocument(
                            job_description_id=source.job_description_id,
                            drive_file_id=item["id"],
                        )
                        db.add(document)
                        await db.flush()

                    latest = await db.scalar(
                        select(CVFileVersion)
                        .where(CVFileVersion.cv_document_id == document.id)
                        .order_by(
                            CVFileVersion.created_at.desc(),
                            CVFileVersion.id.desc(),
                        )
                        .limit(1)
                    )
                    source_hash = item.get("sha256Checksum")
                    root = get_settings().cv_storage_path.resolve()

                    if (
                        source_hash
                        and latest is not None
                        and latest.status == "downloaded"
                        and latest.file_sha256 == source_hash
                        and latest.relative_path
                        and (root / latest.relative_path).is_file()
                    ):
                        counts["skipped"] += 1
                        continue

                    retry_latest = (
                        latest is not None
                        and latest.status in {"pending", "downloading", "failed"}
                        and latest.source_sha256 == source_hash
                        and latest.original_filename == item["name"]
                        and latest.mime_type == item["mimeType"]
                    )

                    if retry_latest:
                        version = latest
                    else:
                        version = CVFileVersion(
                            id=uuid4(),
                            cv_document_id=document.id,
                            original_filename=item["name"],
                            mime_type=item["mimeType"],
                            source_sha256=source_hash,
                        )
                        db.add(version)

                    version.relative_path = f"{version.id}{suffix}"
                    version.status = "downloading"
                    version.error_code = None
                    await db.commit()

                    try:
                        if not source_hash:
                            raise ValueError(
                                "Drive source checksum is unavailable."
                            )
                        for partial_path in root.glob(f"{version.id}-*.part"):
                            partial_path.unlink(missing_ok=True)
                        local_path = root / version.relative_path
                        if local_path.is_file():
                            size, checksum = await run_in_threadpool(
                                verify_local_file,
                                version.relative_path,
                                source_hash,
                            )
                            result = {
                                "file_size_bytes": size,
                                "file_sha256": checksum,
                                "source_sha256": source_hash,
                            }
                        else:
                            result = await run_in_threadpool(
                                download_cv,
                                item["id"],
                                version.id,
                                source_hash,
                            )

                    except Exception as exc:
                        version.status = "failed"
                        version.error_code = (
                            "SOURCE_CHECKSUM_UNAVAILABLE"
                            if not source_hash
                            else "CV_DOWNLOAD_FAILED"
                        )
                        errors.append({
                            "drive_file_id": item["id"],
                            "filename": item["name"],
                            "error_code": version.error_code,
                            "error_type": type(exc).__name__,
                        })
                        counts["failed"] += 1
                    else:
                        version.file_size_bytes = result["file_size_bytes"]
                        version.file_sha256 = result["file_sha256"]
                        version.source_sha256 = result["source_sha256"]
                        version.status = "downloaded"
                        counts["downloaded"] += 1

                    await db.commit()

        finally:
            await connection.rollback()
            await connection.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": lock_key},
            )
            await connection.commit()

    return {
        "source_id": str(source_id),
        **counts,
        "errors": errors,
    }