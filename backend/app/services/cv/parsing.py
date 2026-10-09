"""Recruiter-scoped shared parsing services; API/MCP wrappers supply trusted IDs."""
import json
import subprocess
from hashlib import sha256
from importlib.metadata import version as package_version
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory, engine
from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.models.jd import JDConversation, JobDescription
from app.services.cv.llm import PARSER_VERSION, PROMPT_VERSION, SYSTEM_PROMPT, model_version
from app.integrations.cv_model import model_name, provider, context_tokens, ADAPTER_VERSION
from app.services.cv.profile_schema import CandidateProfile, SCHEMA_VERSION
from app.services.documents.extraction import MAX_FILE_BYTES
from app.workflows.runtime import open_async_workflow_checkpointer

EXTRACTOR_VERSION = "cv-text-3"
OCR_VERSION = "cv-ocr-1"
# Serializes CPU/model work across API and MCP workers. Session-level: commits
# inside graph nodes must not release the lock. Process death releases it.
PROCESSING_LOCK = 73101984022


def owned_versions_statement(recruiter_id: UUID):
    return (select(CVFileVersion, Candidate)
        .join(CVDocument, CVFileVersion.cv_document_id == CVDocument.id)
        .join(CandidateApplication, CVDocument.application_id == CandidateApplication.id)
        .join(Candidate, CandidateApplication.candidate_id == Candidate.id)
        .join(JobDescription, CVDocument.job_description_id == JobDescription.id)
        .join(JDConversation, JobDescription.conversation_id == JDConversation.id)
        .where(JDConversation.recruiter_id == recruiter_id, Candidate.recruiter_id == recruiter_id,
               CandidateApplication.job_description_id == CVDocument.job_description_id))


async def owned_version(db, file_version_id: UUID, recruiter_id: UUID):
    row = (await db.execute(owned_versions_statement(recruiter_id).where(CVFileVersion.id == file_version_id))).first()
    if not row:
        raise HTTPException(404, "CV version not found.")
    return row


def stored_path(file_version: CVFileVersion) -> Path:
    root = get_settings().cv_storage_path.resolve()
    if not file_version.relative_path:
        raise ValueError("LOCAL_FILE_MISSING")
    path = (root / file_version.relative_path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in {".pdf", ".docx"}:
        raise ValueError("INVALID_STORED_PATH")
    return path


def verify_integrity(file_version: CVFileVersion) -> Path:
    if file_version.status != "downloaded":
        raise ValueError("CV_NOT_DOWNLOADED")
    path = stored_path(file_version)
    try:
        size = path.stat().st_size
    except OSError:
        raise ValueError("LOCAL_FILE_MISSING") from None
    if not 0 < size <= MAX_FILE_BYTES or size != file_version.file_size_bytes:
        raise ValueError("FILE_SIZE_MISMATCH")
    digest = sha256(path.read_bytes()).hexdigest()
    if digest != file_version.file_sha256 or (file_version.source_sha256 and digest != file_version.source_sha256):
        raise ValueError("FILE_HASH_MISMATCH")
    return path


def local_versions() -> dict:
    settings = get_settings()
    def binary_version(command):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
            return (result.stdout or result.stderr).splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            return "unavailable"
    traineddata = {}
    for language in settings.cv_ocr_language.split("+"):
        # Docker Debian's installed language data; no caller-controlled paths.
        data_path = Path("/usr/share/tesseract-ocr/5/tessdata") / f"{language}.traineddata"
        traineddata[language] = sha256(data_path.read_bytes()).hexdigest() if data_path.is_file() else "unavailable"
    return {"extractor": EXTRACTOR_VERSION, "ocr": OCR_VERSION, "ocr_language_data": traineddata,
            "tesseract": binary_version(["tesseract", "--version"]),
            "poppler": binary_version(["pdftoppm", "-v"]),
            "pypdf": package_version("pypdf"), "python_docx": package_version("python-docx"),
            "pillow": package_version("Pillow"), "ocr_language": settings.cv_ocr_language,
            "ocr_page_timeout": settings.cv_ocr_timeout_seconds,
            "document_timeout": settings.cv_document_timeout_seconds,
            "parser": PARSER_VERSION, "prompt": PROMPT_VERSION, "schema": SCHEMA_VERSION,
            "prompt_hash": sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "schema_hash": sha256(json.dumps(CandidateProfile.model_json_schema(), sort_keys=True).encode()).hexdigest(),
            "context_tokens": context_tokens(), "output_tokens": settings.cv_model_output_tokens}


def public_result(result: CVParseResult | None, file_version_id: UUID) -> dict:
    if not result:
        return {"file_version_id": str(file_version_id), "status": "not_started", "warnings": [], "ocr_used": False,
                "profile": None, "evidence": [], "sources": []}
    return {"id": str(result.id), "file_version_id": str(result.file_version_id), "status": result.status,
            "attempt": result.attempt, "file_sha256": result.file_sha256, "versions": result.versions,
            "extraction_method": result.extraction_method,
            "ocr_used": any(s["method"] == "ocr" and s["end"] > s["start"] for s in result.sources),
            "warnings": result.warnings, "profile": result.profile, "evidence": result.evidence,
            "sources": result.sources, "error_code": result.error_code,
            "updated_at": result.updated_at.isoformat() if result.updated_at else None}


async def get_cv_profile(file_version_id: UUID, recruiter_id: UUID) -> dict:
    async with SessionFactory() as db:
        await owned_version(db, file_version_id, recruiter_id)
        result = await db.scalar(select(CVParseResult).where(CVParseResult.file_version_id == file_version_id)
                                 .order_by(CVParseResult.updated_at.desc(), CVParseResult.id.desc()).limit(1))
        return public_result(result, file_version_id)


async def parse_cv(file_version_id: UUID, recruiter_id: UUID) -> dict:
    # Authorization precedes model calls, lock acquisition and every cache read.
    async with engine.connect() as connection:
        async with AsyncSession(bind=connection, expire_on_commit=False) as db:
            file_version, candidate = await owned_version(db, file_version_id, recruiter_id)
            locked = await db.scalar(sql_text("SELECT pg_try_advisory_lock(:key)"), {"key": PROCESSING_LOCK})
            await db.commit()
            if not locked:
                raise HTTPException(409, "CV parsing is busy. Retry after the current CV completes.")
            result = None
            try:
                versions = await run_in_threadpool(local_versions)
                try:
                    versions.update(await model_version())
                except Exception:
                    # Verification/extraction still work when Ollama is offline.
                    # Never claim a model version or send text to an absent model.
                    versions.update(model=model_name(), model_digest="unavailable", provider=provider(), model_adapter=ADAPTER_VERSION)
                key = sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()
                result = await db.scalar(select(CVParseResult).where(CVParseResult.file_version_id == file_version_id,
                                                                        CVParseResult.processing_key == key))
                if result is None:
                    result = CVParseResult(file_version_id=file_version_id, processing_key=key,
                                           file_sha256=file_version.file_sha256 or "", versions=versions)
                    db.add(result)
                    await db.commit()
                # Check disk on every request, including cached and interrupted runs.
                await run_in_threadpool(verify_integrity, file_version)
                if result.status == "completed":
                    return public_result(result, file_version_id)
                from app.workflows.cv_parsing import build_cv_parsing_graph
                async with open_async_workflow_checkpointer() as saver:
                    graph = build_cv_parsing_graph(saver, db, result, file_version, candidate)
                    config = {"configurable": {"thread_id": f"cv-parse:{result.id}:{result.attempt}"}, "recursion_limit": 20}
                    snapshot = await graph.aget_state(config)
                    if snapshot.next and result.status not in {"failed", "needs_review"}:
                        await graph.ainvoke(None, config)
                    else:
                        if result.error_code == "TEXT_UNUSABLE_OR_INCOMPLETE":
                            # Explicit recruiter retry must rerun incomplete OCR.
                            result.text, result.sources = None, []
                        result.attempt += 1
                        result.error_code = None
                        await db.commit()
                        config["configurable"]["thread_id"] = f"cv-parse:{result.id}:{result.attempt}"
                        await graph.ainvoke({"file_version_id": str(file_version_id)}, config)
                await db.refresh(result)
                return public_result(result, file_version_id)
            except HTTPException:
                raise
            except Exception as exc:
                if result is None:
                    raise HTTPException(503, "CV parsing is unavailable.") from None
                await db.rollback()
                # Safe codes only; never send model responses, prompts, paths or PII.
                known = {"MODEL_API_KEY_MISSING", "MODEL_AUTH_FAILED", "MODEL_NOT_AVAILABLE", "MODEL_RATE_LIMITED", "MODEL_REQUEST_INVALID", "MODEL_RESPONSE_BLOCKED", "MODEL_UNAVAILABLE", "MODEL_TIMEOUT", "FILE_HASH_MISMATCH", "FILE_SIZE_MISMATCH", "INVALID_STORED_PATH", "LOCAL_FILE_MISSING",
                         "CV_NOT_DOWNLOADED", "MODEL_SCHEMA_OR_EVIDENCE_INVALID", "MODEL_INPUT_TOO_LARGE",
                         "MODEL_OUTPUT_TOO_LARGE", "ENCRYPTED_PDF", "INVALID_DOCUMENT", "DOCUMENT_TIMEOUT",
                         "TOO_MANY_PAGES", "TOO_MANY_IMAGES", "TOO_MUCH_TEXT", "EXTRACTION_FAILED", "INVALID_FILE", "INVALID_SIZE"}
                code = getattr(exc, "code", None) or (str(exc) if isinstance(exc, ValueError) else "PARSING_FAILED")
                code = code if code in known else "PARSING_FAILED"
                # rollback expires ORM objects; reload instead of implicit async IO.
                result = await db.scalar(select(CVParseResult).where(CVParseResult.file_version_id == file_version_id,
                                                                        CVParseResult.processing_key == key))
                result.status = "needs_review" if code in {"MODEL_INPUT_TOO_LARGE", "MODEL_OUTPUT_TOO_LARGE", "ENCRYPTED_PDF", "INVALID_DOCUMENT", "INVALID_FILE", "TOO_MANY_PAGES", "TOO_MANY_IMAGES", "TOO_MUCH_TEXT", "FILE_HASH_MISMATCH", "FILE_SIZE_MISMATCH"} else "failed"
                result.error_code = code
                result.profile, result.evidence = None, []
                await db.commit()
                await db.refresh(result)
                return public_result(result, file_version_id)
            finally:
                await db.rollback()
                await connection.execute(sql_text("SELECT pg_advisory_unlock(:key)"), {"key": PROCESSING_LOCK})
                await connection.commit()


async def parse_job(job_id: UUID, recruiter_id: UUID, offset: int = 0, limit: int = 20) -> dict:
    async with SessionFactory() as db:
        owned = await db.scalar(select(JobDescription.id).join(JDConversation).where(JobDescription.id == job_id,
                                                                                  JDConversation.recruiter_id == recruiter_id))
        if not owned:
            raise HTTPException(404, "Job not found.")
        latest = (select(CVFileVersion.id).where(CVFileVersion.cv_document_id == CVDocument.id,
                   CVFileVersion.status == "downloaded").order_by(CVFileVersion.created_at.desc(), CVFileVersion.id.desc())
                   .limit(1).correlate(CVDocument).scalar_subquery())
        rows = (await db.execute(owned_versions_statement(recruiter_id).where(CVDocument.job_description_id == job_id,
                CVFileVersion.id == latest).order_by(CVFileVersion.id).offset(offset).limit(limit + 1))).all()
        ids = [row[0].id for row in rows[:limit]]
    results = []
    for file_id in ids:
        try:
            results.append(await parse_cv(file_id, recruiter_id))
        except Exception as exc:
            results.append({"file_version_id": str(file_id), "status": "failed",
                            "error_code": "PARSING_BUSY" if isinstance(exc, HTTPException) and exc.status_code == 409 else "PARSING_UNAVAILABLE"})
    return {"job_description_id": str(job_id), "results": results,
            "next_offset": offset + limit if len(rows) > limit else None}
