"""Check remote metadata without downloading or screening CVs."""
from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.session import engine
from app.integrations.google_sheets import read_response_sheet
from app.models.cv import CVDocument, CVFileVersion, CVSource, CVSourceCheck
from app.services.cv.sheet_import import list_source_cv_files, parse_response_rows, source_lock_key

CHECK_INTERVAL_SECONDS = 300
WORKER_LOCK = 73101984025
logger = logging.getLogger("distill_ai.cv_source_checks")


def pending_files(files, downloaded_ids, rows=None):
    ids = {item["id"] for item in files}
    if rows is not None:
        ids &= {file_id for row in rows for file_id in row["drive_file_ids"]}
    return sorted(ids - set(downloaded_ids))


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
    key = source_lock_key(source_id)
    async with engine.connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        await connection.commit()
        if not acquired:
            return False
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                source = await db.get(CVSource, source_id)
                if source is None:
                    return False
                check = await db.get(CVSourceCheck, source_id)
                if check is None:
                    check = CVSourceCheck(source_id=source_id, pending_file_ids=[])
                    db.add(check)
                try:
                    rows = None
                    if source.spreadsheet_id is not None:
                        sheet = await run_in_threadpool(read_response_sheet, source.spreadsheet_id, source.sheet_tab_id)
                        rows, _ = parse_response_rows(sheet)
                    files = await run_in_threadpool(list_source_cv_files, source.drive_folder_id, source.spreadsheet_id is not None)
                    check.pending_file_ids = pending_files(files, await downloaded_file_ids(db, source.job_description_id), rows)
                    check.last_success_at = datetime.now(timezone.utc)
                    check.error_code = None
                except Exception as error:
                    # Preserve the last good notification during a remote failure.
                    check.error_code = "CV_SOURCE_CHECK_FAILED"
                    logger.warning("CV source check failed | error_type=%s", type(error).__name__)
                check.last_checked_at = datetime.now(timezone.utc)
                await db.commit()
                return True
        finally:
            await connection.rollback()
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            await connection.commit()


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
