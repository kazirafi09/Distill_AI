"""Delete one owned job application; retry external cleanup without restoring PII."""
import logging
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import delete, select, text
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.candidate_deletion import CandidateDeletion, CVImportExclusion
from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion, CVSourceCheck
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVIndex, ScreeningRun
from app.models.assessments import AssessmentRun, FinalCandidateSelection
from app.models.interviews import InterviewInvitation, InvitationEmailJob, InvitationBatch, InterviewSession, InterviewShortlist
from app.services.recruitment_access import owned_application, lock_job
from app.services.cv.sheet_import import source_lock_key
from app.services.cv.parsing import PROCESSING_LOCK

logger = logging.getLogger("distill_ai.candidate_deletion")


def file_key(file_id):
    return sha256(file_id.encode()).hexdigest()


async def excluded_files(db, source_id):
    return set(await db.scalars(select(CVImportExclusion.file_key).where(CVImportExclusion.source_id == source_id)))


def remove_local_files(files):
    root = get_settings().cv_storage_path.resolve()
    for item in files:
        if item["path"]:
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root) or path == root:
                raise ValueError("INVALID_CLEANUP_PATH")
            path.unlink(missing_ok=True)
        for path in root.glob(f'{item["id"]}-*.part'):
            path.unlink(missing_ok=True)


async def cleanup(deletion_id):
    # Row lock serializes API and periodic-worker retries. Operations are idempotent.
    async with SessionFactory() as db:
        task = await db.scalar(select(CandidateDeletion).where(CandidateDeletion.id == deletion_id).with_for_update())
        if task is None or task.status == "completed":
            return True
        try:
            from app.services.cv.indexing import collection
            for index in task.manifest["indexes"]:
                await run_in_threadpool(lambda entry=index: collection(entry["versions"]).delete(where={"index_id": entry["id"]}))
            await run_in_threadpool(remove_local_files, task.manifest["files"])
            from app.workflows.runtime import open_async_workflow_checkpointer
            if task.manifest["threads"]:
                async with open_async_workflow_checkpointer() as saver:
                    for thread_id in task.manifest["threads"]:
                        await saver.adelete_thread(thread_id)
            task.status, task.manifest = "completed", {}
            await db.commit()
            return True
        except Exception as error:
            logger.warning("Candidate cleanup pending | task=%s | error_type=%s", deletion_id, type(error).__name__)
            return False


async def retry_pending():
    async with SessionFactory() as db:
        ids = list(await db.scalars(select(CandidateDeletion.id).where(CandidateDeletion.status == "pending")
                                   .order_by(CandidateDeletion.created_at).limit(10)))
    for deletion_id in ids:
        await cleanup(deletion_id)


async def delete_application(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        await lock_job(db, job_id)
        application = await owned_application(db, job_id, application_id, recruiter_id)
        # Source imports use a session lock; parsing uses a global session lock.
        # Transaction variants conflict with them and release automatically.
        for key in (source_lock_key(application.source_id), PROCESSING_LOCK):
            if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
                raise HTTPException(409, "An import or CV parse is active. Wait for it to finish, then delete again.")
        active_screening = await db.scalar(select(ScreeningRun.id).where(ScreeningRun.job_id == job_id,
            ScreeningRun.status.in_(["queued", "running"])).limit(1))
        # Include diagnostic runs that pin an application in JSON rather than a FK.
        active_analysis = await db.scalar(select(AssessmentRun.id).where(AssessmentRun.job_id == job_id,
            AssessmentRun.status.in_(["queued", "running"])).limit(1))
        session = await db.scalar(select(InterviewSession).where(InterviewSession.application_id == application_id))
        sending = await db.scalar(select(InvitationEmailJob.id).join(InterviewInvitation).where(
            InterviewInvitation.application_id == application_id, InvitationEmailJob.status == "sending"))
        if active_screening or active_analysis or sending or (session and session.status in {"in_progress", "closing"}):
            raise HTTPException(409, "Wait for this job's processing, active interview or email send to finish before deleting.")

        documents = list(await db.scalars(select(CVDocument).where(CVDocument.application_id == application_id)))
        doc_ids = [item.id for item in documents]
        versions = list(await db.scalars(select(CVFileVersion).where(CVFileVersion.cv_document_id.in_(doc_ids))))
        parses = list(await db.scalars(select(CVParseResult).where(CVParseResult.file_version_id.in_([item.id for item in versions]))))
        indexes = list(await db.scalars(select(CVIndex).where(CVIndex.parse_result_id.in_([item.id for item in parses]))))
        task = CandidateDeletion(recruiter_id=recruiter_id, application_id=application_id, manifest={
            "files": [{"id": str(item.id), "path": item.relative_path} for item in versions],
            "indexes": [{"id": str(item.id), "versions": item.versions} for item in indexes],
            "threads": [f"cv-parse:{item.id}:{attempt}" for item in parses for attempt in range(item.attempt + 1)],
        })
        db.add(task)
        existing = await excluded_files(db, application.source_id)
        for document in documents:
            key = file_key(document.drive_file_id)
            if key not in existing:
                db.add(CVImportExclusion(source_id=application.source_id, file_key=key))
                existing.add(key)
        identifier = str(application_id)
        # Remove embedded PII that FK cascades cannot reach.
        for run in await db.scalars(select(ScreeningRun).where(ScreeningRun.job_id == job_id)):
            run.inputs = {**run.inputs, "applications": [item for item in run.inputs.get("applications", [])
                                                       if item["application_id"] != identifier]}
            run.status = "outdated"
        for batch in await db.scalars(select(InvitationBatch).where(InvitationBatch.job_description_id == job_id)):
            batch.preview = {**batch.preview, "recipients": [item for item in batch.preview.get("recipients", [])
                                                           if item["application_id"] != identifier]}
            # Invalidates any open preview, including its recipient snapshots.
            batch.preview_key = "deleted-application"
        shortlist = await db.scalar(select(InterviewShortlist).where(InterviewShortlist.job_description_id == job_id))
        if shortlist:
            shortlist.proposed_application_ids = [item for item in shortlist.proposed_application_ids if item != identifier]
        selection = await db.get(FinalCandidateSelection, job_id)
        if selection:
            selection.candidates = [item for item in selection.candidates if item["application_id"] != identifier]
        check = await db.get(CVSourceCheck, application.source_id)
        if check:
            removed = {item.drive_file_id for item in documents}
            check.pending_file_ids = [item for item in check.pending_file_ids if item not in removed]
        await db.execute(delete(AssessmentRun).where(AssessmentRun.job_id == job_id,
            AssessmentRun.inputs["test_application_id"].astext == identifier))
        # Explicitly delete documents: application FK intentionally uses SET NULL.
        await db.execute(delete(CVDocument).where(CVDocument.id.in_(doc_ids)))
        candidate_id = application.candidate_id
        await db.delete(application)
        await db.flush()
        if not await db.scalar(select(CandidateApplication.id).where(CandidateApplication.candidate_id == candidate_id).limit(1)):
            await db.execute(delete(Candidate).where(Candidate.id == candidate_id))
        await db.commit()
        deletion_id = task.id
    completed = await cleanup(deletion_id)
    return {"deleted": True, "cleanup_pending": not completed}
