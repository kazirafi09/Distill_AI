"""Import connected application sources every minute, independently of chat/UI."""
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.db.session import engine
from app.models.cv import CVDocument, CVFileVersion, CVSource, CVSourceCheck
from app.models.jd import JDConversation, JobDescription

CHECK_INTERVAL_SECONDS = 60
WORKER_LOCK = 73101984025
logger = logging.getLogger("distill_ai.cv_source_checks")


async def downloaded_file_ids(db, job_id):
    return set(await db.scalars(select(CVDocument.drive_file_id)
        .join(CVFileVersion, CVFileVersion.cv_document_id == CVDocument.id)
        .where(CVDocument.job_description_id == job_id, CVFileVersion.status == "downloaded").distinct()))


async def reconcile_source_check(db, source):
    check = await db.get(CVSourceCheck, source.id)
    if check is not None:
        downloaded = await downloaded_file_ids(db, source.job_description_id)
        check.pending_file_ids = sorted(set(check.pending_file_ids) - downloaded)
        await db.commit()


async def check_source(source_id):
    # Import lazily: ingestion also uses reconcile_source_check after importing.
    from app.services.cv.ingestion import fetch_cv_source

    async with AsyncSession(engine, expire_on_commit=False) as db:
        recruiter_id = await db.scalar(select(JDConversation.recruiter_id)
            .join(JobDescription, JobDescription.conversation_id == JDConversation.id)
            .join(CVSource, CVSource.job_description_id == JobDescription.id)
            .where(CVSource.id == source_id))
    if recruiter_id is None:
        return False

    result = None
    error_code = None
    try:
        # The shared importer owns the source lock, checksum/version checks and
        # idempotent Sheet row linking. Do not take its lock on another connection.
        result = await fetch_cv_source(source_id, recruiter_id)
        if result["failed"] or result["errors"] or (result.get("sheet") or {}).get("rows_failed"):
            error_code = "CV_SOURCE_IMPORT_INCOMPLETE"
    except HTTPException as error:
        if error.status_code in {404, 409}:
            # A concurrent import or source setup owns this source. Leave it due.
            return False
        error_code = "CV_SOURCE_CONFIGURATION_INVALID" if error.status_code == 422 else "CV_SOURCE_CHECK_FAILED"
        logger.warning("CV source import failed | status=%s", error.status_code)
    except Exception as error:
        error_code = "CV_SOURCE_CHECK_FAILED"
        logger.warning("CV source import failed | error_type=%s", type(error).__name__)

    async with AsyncSession(engine, expire_on_commit=False) as db:
        if await db.get(CVSource, source_id) is None:
            return False
        check = await db.get(CVSourceCheck, source_id)
        if check is None:
            check = CVSourceCheck(source_id=source_id, pending_file_ids=[])
            db.add(check)
        now = datetime.now(timezone.utc)
        check.last_checked_at = now
        check.error_code = error_code
        if result is not None:
            check.pending_file_ids = sorted({error["drive_file_id"] for error in result["errors"] if error.get("drive_file_id")})
        if error_code is None:
            check.last_success_at = now
        await db.commit()
    return True


async def run_due_checks():
    from app.services.interviews.identification import purge_expired
    await purge_expired()
    async with engine.connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
        await connection.commit()
        if not acquired:
            return 0
        try:
            due = datetime.now(timezone.utc) - timedelta(seconds=CHECK_INTERVAL_SECONDS)
            source_ids = list(await connection.scalars(select(CVSource.id)
                .outerjoin(CVSourceCheck, CVSourceCheck.source_id == CVSource.id)
                .where((CVSourceCheck.last_checked_at.is_(None)) | (CVSourceCheck.last_checked_at <= due))
                .order_by(CVSourceCheck.last_checked_at.asc().nullsfirst(), CVSource.created_at).limit(20)))
            await connection.commit()
            checked = 0
            for source_id in source_ids:
                checked += bool(await check_source(source_id))
            return checked
        finally:
            await connection.rollback()
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
            await connection.commit()
