"""Synthetic PostgreSQL fixtures only; no real candidate deletion or provider calls."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, select

from app.auth import require_recruiter
from app.cv_routes import router
from app.db.session import SessionFactory, engine
from app.models.user import User
from app.models.jd import JDConversation, JobDescription
from app.models.cv import Candidate, CandidateApplication, CVSource, CVDocument, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import ScreeningRun, CVAssessment
from app.models.assessments import FinalCandidateSelection, AssessmentRun, AssessmentResult
from app.models.interview_transcripts import InterviewTranscript, InterviewTranscriptTurn
from app.models.candidate_identity import CandidateIdentity
from app.models.interviews import InterviewInvitation, InvitationBatch, InterviewSession
from app.models.candidate_deletion import CandidateDeletion
from app.services.cv import deletion
from app.services.cv.sheet_import import import_response_rows


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_and_confirmation_required(self):
        app = FastAPI()
        app.include_router(router)
        url = f"/cv/jobs/{uuid4()}/applications/{uuid4()}/delete"
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual((await client.post(url, json={"confirmed": True})).status_code, 401)
            app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=uuid4())
            for payload in ({}, {"confirmed": False}):
                self.assertEqual((await client.post(url, json=payload)).status_code, 422)

    async def test_local_cleanup_rejects_escape_and_tolerates_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(deletion, "get_settings", return_value=SimpleNamespace(cv_storage_path=Path(directory))):
                target = Path(directory) / "synthetic.pdf"
                target.write_bytes(b"synthetic")
                deletion.remove_local_files([{"id": str(uuid4()), "path": target.name}])
                self.assertFalse(target.exists())
                deletion.remove_local_files([{"id": str(uuid4()), "path": target.name}])
                with self.assertRaises(ValueError):
                    deletion.remove_local_files([{"id": str(uuid4()), "path": "../outside.pdf"}])


@unittest.skipUnless(os.getenv("RUN_DELETION_DB_TESTS") == "1", "Opt-in PostgreSQL fixtures")
class DeletionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner, self.other = uuid4(), uuid4()
        self.addAsyncCleanup(self.cleanup_records)
        async with SessionFactory() as db:
            db.add(User(id=self.owner, email=f"deletion-{self.owner}@example.invalid", password_hash="fixture", role="recruiter"))
            await db.flush()
            conversation = JDConversation(recruiter_id=self.owner)
            db.add(conversation)
            await db.flush()
            job = JobDescription(conversation_id=conversation.id, title="Deletion fixture", content="Synthetic", requirements="Synthetic")
            db.add(job)
            await db.flush()
            source = CVSource(job_description_id=job.id, drive_folder_id=f"deletion-{uuid4()}")
            candidate = Candidate(recruiter_id=self.owner, full_name="Deletion fixture", email="synthetic@example.invalid")
            db.add_all([source, candidate])
            await db.flush()
            application = CandidateApplication(candidate_id=candidate.id, job_description_id=job.id, source_id=source.id, source_row_number=2)
            db.add(application)
            await db.flush()
            document = CVDocument(application_id=application.id, job_description_id=job.id, drive_file_id=f"file-{uuid4()}")
            db.add(document)
            await db.flush()
            version = CVFileVersion(cv_document_id=document.id, original_filename="fixture.pdf", mime_type="application/pdf")
            db.add(version)
            await db.flush()
            parsed = CVParseResult(file_version_id=version.id, processing_key="fixture", file_sha256="a" * 64, versions={}, text="Sensitive synthetic text")
            db.add(parsed)
            run = ScreeningRun(job_id=job.id, recruiter_id=self.owner, input_key="fixture", status="completed",
                inputs={"applications": [{"application_id": str(application.id), "sheet_identity": {"name": candidate.full_name}}]})
            db.add(run)
            await db.flush()
            session = InterviewSession(application_id=application.id, job_description_id=job.id,
                recruiter_id=self.owner, room_name=f"fixture-{uuid4()}", status="completed")
            db.add(session)
            await db.flush()
            transcript = InterviewTranscript(session_id=session.id, model="fixture", language="en", status="completed")
            db.add(transcript)
            await db.flush()
            analysis = AssessmentRun(recruiter_id=self.owner, job_id=job.id, application_id=application.id,
                transcript_id=transcript.id, kind="f5", label="Synthetic sensitive findings", input_key=str(uuid4()),
                inputs={"synthetic_text": "Fixture interview"}, status="completed")
            db.add(analysis)
            await db.flush()
            db.add(AssessmentResult(run_id=analysis.id, payload={"sensitive": "synthetic"}))
            db.add_all([
                CVAssessment(run_id=run.id, application_id=application.id, file_version_ids=[str(version.id)]),
                FinalCandidateSelection(job_id=job.id, recruiter_id=self.owner, candidates=[{"application_id": str(application.id), "name": candidate.full_name}]),
                CandidateIdentity(application_id=application.id, reference_file_id="synthetic-photo", expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
                InterviewInvitation(recruiter_id=self.owner, job_description_id=job.id, application_id=application.id,
                    recipient_email=candidate.email, token_hash=uuid4().hex * 2, expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
                InvitationBatch(recruiter_id=self.owner, job_description_id=job.id, mode="new", preview_key="old",
                    preview={"recipients": [{"application_id": str(application.id), "name": candidate.full_name}]}, expires_at=datetime.now(timezone.utc) + timedelta(days=1)),
            ])
            await db.commit()
            self.job, self.application, self.candidate = job.id, application.id, candidate.id
            self.document, self.version, self.parsed, self.run = document.id, version.id, parsed.id, run.id
            self.source, self.file_id = source.id, document.drive_file_id
            self.session, self.transcript, self.analysis = session.id, transcript.id, analysis.id

    async def cleanup_records(self):
        async with SessionFactory() as db:
            await db.execute(delete(User).where(User.id == self.owner))
            await db.commit()
        await engine.dispose()

    async def test_delete_cascades_scrubs_snapshots_and_prevents_reimport(self):
        with patch.object(deletion, "cleanup", AsyncMock(return_value=False)):
            result = await deletion.delete_application(self.job, self.application, self.owner)
        self.assertTrue(result["cleanup_pending"])
        async with SessionFactory() as db:
            for model, key in ((Candidate, self.candidate), (CandidateApplication, self.application),
                               (CVDocument, self.document), (CVFileVersion, self.version), (CVParseResult, self.parsed),
                               (CandidateIdentity, self.application), (InterviewSession, self.session),
                               (InterviewTranscript, self.transcript), (AssessmentRun, self.analysis), (AssessmentResult, self.analysis)):
                self.assertIsNone(await db.get(model, key), model.__name__)
            self.assertEqual((await db.get(ScreeningRun, self.run)).inputs["applications"], [])
            self.assertEqual((await db.get(FinalCandidateSelection, self.job)).candidates, [])
            batch = await db.scalar(select(InvitationBatch).where(InvitationBatch.job_description_id == self.job))
            self.assertEqual(batch.preview["recipients"], [])
            self.assertIn(deletion.file_key(self.file_id), await deletion.excluded_files(db, self.source))
            source = await db.get(CVSource, self.source)
            imported = await import_response_rows(db, source, self.owner, [{"drive_file_ids": [self.file_id]}], {self.file_id}, [])
            self.assertEqual(imported["rows_skipped"], 1)
            task = await db.scalar(select(CandidateDeletion).where(CandidateDeletion.application_id == self.application))
            task_id = task.id
        # Resume durable cleanup using a synthetic path-free manifest.
        with patch.object(deletion, "remove_local_files") as files:
            self.assertTrue(await deletion.cleanup(task_id))
            self.assertTrue(await deletion.cleanup(task_id))
            files.assert_called_once()
        async with SessionFactory() as db:
            self.assertEqual((await db.get(CandidateDeletion, task_id)).manifest, {})

    async def test_foreign_owner_cannot_delete(self):
        with self.assertRaises(HTTPException) as raised:
            await deletion.delete_application(self.job, self.application, self.other)
        self.assertEqual(raised.exception.status_code, 404)

    async def test_processing_blocks_deletion(self):
        async with SessionFactory() as db:
            (await db.get(ScreeningRun, self.run)).status = "running"
            await db.commit()
        with self.assertRaises(HTTPException) as raised:
            await deletion.delete_application(self.job, self.application, self.owner)
        self.assertEqual(raised.exception.status_code, 409)
        async with SessionFactory() as db:
            self.assertIsNotNone(await db.get(CandidateApplication, self.application))

    async def test_active_interview_blocks_deletion(self):
        async with SessionFactory() as db:
            (await db.get(InterviewSession, self.session)).status = "in_progress"
            await db.commit()
        with self.assertRaises(HTTPException) as raised:
            await deletion.delete_application(self.job, self.application, self.owner)
        self.assertEqual(raised.exception.status_code, 409)

    async def test_import_lock_blocks_deletion(self):
        from sqlalchemy import text
        from app.services.cv.sheet_import import source_lock_key
        async with engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": source_lock_key(self.source)})
            await connection.commit()
            try:
                with self.assertRaises(HTTPException) as raised:
                    await deletion.delete_application(self.job, self.application, self.owner)
                self.assertEqual(raised.exception.status_code, 409)
            finally:
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": source_lock_key(self.source)})
                await connection.commit()

    async def test_storage_failure_retains_manifest_for_periodic_retry(self):
        with patch.object(deletion, "remove_local_files", side_effect=OSError("fixture failure")):
            result = await deletion.delete_application(self.job, self.application, self.owner)
        self.assertTrue(result["cleanup_pending"])
        async with SessionFactory() as db:
            task = await db.scalar(select(CandidateDeletion).where(CandidateDeletion.application_id == self.application))
            self.assertEqual(task.status, "pending")
            self.assertTrue(task.manifest["files"])
            task_id = task.id
        with patch.object(deletion, "remove_local_files"):
            self.assertTrue(await deletion.cleanup(task_id))

    async def test_index_cleanup_targets_only_recorded_index(self):
        index_id = str(uuid4())
        async with SessionFactory() as db:
            task = CandidateDeletion(recruiter_id=self.owner, application_id=self.application,
                manifest={"files": [], "threads": [], "indexes": [{"id": index_id, "versions": {"fixture": "v1"}}]})
            db.add(task)
            await db.commit()
            task_id = task.id
        target = MagicMock()
        with patch("app.services.cv.indexing.collection", return_value=target) as collection:
            self.assertTrue(await deletion.cleanup(task_id))
            collection.assert_called_once_with({"fixture": "v1"})
            target.delete.assert_called_once_with(where={"index_id": index_id})

    async def test_candidate_profile_is_retained_for_another_application(self):
        async with SessionFactory() as db:
            conversation = JDConversation(recruiter_id=self.owner)
            db.add(conversation)
            await db.flush()
            job = JobDescription(conversation_id=conversation.id, title="Other fixture job", content="Synthetic", requirements="Synthetic")
            db.add(job)
            await db.flush()
            source = CVSource(job_description_id=job.id, drive_folder_id=f"other-{uuid4()}")
            db.add(source)
            await db.flush()
            other = CandidateApplication(candidate_id=self.candidate, job_description_id=job.id, source_id=source.id, source_row_number=2)
            db.add(other)
            await db.commit()
            other_id = other.id
        with patch.object(deletion, "cleanup", AsyncMock(return_value=True)):
            await deletion.delete_application(self.job, self.application, self.owner)
        async with SessionFactory() as db:
            self.assertIsNotNone(await db.get(Candidate, self.candidate))
            self.assertIsNotNone(await db.get(CandidateApplication, other_id))


if __name__ == "__main__":
    unittest.main()
