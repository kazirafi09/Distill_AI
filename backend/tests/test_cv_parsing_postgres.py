"""Opt-in real PostgreSQL/checkpoint/API/MCP tests with disposable synthetic rows."""
import asyncio
import os
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import delete, select, text

from app.core.config import get_settings
from app.core.security import create_access_token
from app.db.session import SessionFactory, engine
from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion, CVSource
from app.models.cv_parsing import CVParseResult
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.services.cv_parsing import PROCESSING_LOCK, get_cv_profile, parse_cv, parse_job
from app.services.cv_profile_schema import validate_profile
from test_cv_parsing import claim, readable_pdf


@unittest.skipUnless(os.environ.get("RUN_CV_DB_TESTS") == "1", "Set RUN_CV_DB_TESTS=1")
class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.paths, self.users = [], []
        async with SessionFactory() as db:
            recruiter = User(email=f"cv-stage-{uuid4()}@example.invalid", password_hash="test-only", role="recruiter")
            other = User(email=f"cv-stage-{uuid4()}@example.invalid", password_hash="test-only", role="recruiter")
            viewer = User(email=f"cv-stage-{uuid4()}@example.invalid", password_hash="test-only", role="interviewer")
            db.add_all([recruiter, other, viewer]); await db.flush()
            self.owner, self.other, self.viewer = recruiter.id, other.id, viewer.id
            self.users = [self.owner, self.other, self.viewer]
            conversation = JDConversation(recruiter_id=self.owner); db.add(conversation); await db.flush()
            job = JobDescription(conversation_id=conversation.id, title="CV test", content="Synthetic", requirements="Synthetic", screening_profile={"criteria": []})
            db.add(job); await db.flush(); self.job_id = job.id
            source = CVSource(job_description_id=job.id, drive_folder_id=f"test-{uuid4()}"); db.add(source); await db.flush()
            candidate = Candidate(recruiter_id=self.owner, full_name="Sheet Different", email="sheet@example.invalid")
            db.add(candidate); await db.flush()
            application = CandidateApplication(candidate_id=candidate.id, job_description_id=job.id, source_id=source.id, source_row_number=2)
            db.add(application); await db.flush()
            self.ids = []
            for data in [readable_pdf(), b"corrupted document"]:
                doc = CVDocument(job_description_id=job.id, drive_file_id=f"test-{uuid4()}", application_id=application.id)
                db.add(doc); await db.flush()
                identifier = uuid4(); path = get_settings().cv_storage_path / f"{identifier}.pdf"
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); self.paths.append(path)
                digest = sha256(data).hexdigest()
                version = CVFileVersion(id=identifier, cv_document_id=doc.id, original_filename="synthetic.pdf", mime_type="application/pdf",
                        relative_path=path.name, file_sha256=digest, source_sha256=digest, file_size_bytes=len(data), status="downloaded")
                db.add(version); self.ids.append(identifier)
            await db.commit()

    async def asyncTearDown(self):
        async with SessionFactory() as db:
            result_ids = list(await db.scalars(select(CVParseResult.id).where(CVParseResult.file_version_id.in_(self.ids))))
            # Remove checkpoints only for the fixture's exact parsing IDs.
            for result_id in result_ids:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await db.execute(text(f"DELETE FROM {table} WHERE thread_id LIKE :prefix"), {"prefix": f"cv-parse:{result_id}:%"})
            await db.execute(delete(User).where(User.id.in_(self.users))); await db.commit()
        for path in self.paths:
            path.unlink(missing_ok=True)
        await engine.dispose()

    async def fake_extract(self, text, sources, **kwargs):
        # Assert text committed in a separate DB session before the model call.
        async with SessionFactory() as db:
            saved = await db.scalar(select(CVParseResult).where(CVParseResult.file_version_id == self.ids[0]))
            self.assertEqual(saved.text, text)
        return validate_profile({"contact": {"name": claim("Alice Example")}, "skills": [claim("Python")]}, text, sources)

    def mocks(self):
        return patch("app.services.cv_parsing.model_version", AsyncMock(return_value={"model": "test-model", "model_digest": "synthetic"}))

    async def test_persistence_cache_integrity_scope_duplicates_and_batch(self):
        with self.mocks(), patch("app.workflows.cv_parsing.extract_profile", side_effect=self.fake_extract) as model:
            result = await parse_cv(self.ids[0], self.owner)
            self.assertEqual(result["status"], "completed", result)
            self.assertIn("CV_SHEET_NAME_DISCREPANCY", result["warnings"])
            again = await parse_cv(self.ids[0], self.owner)
            self.assertEqual(again["id"], result["id"])
            self.assertEqual(model.call_count, 1)
            for action in (parse_cv, get_cv_profile):
                with self.assertRaises(HTTPException) as denied:
                    await action(self.ids[0], self.other)
                self.assertEqual(denied.exception.status_code, 404)
            async with engine.connect() as connection:
                await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": PROCESSING_LOCK})
                try:
                    with self.assertRaises(HTTPException) as busy:
                        await parse_cv(self.ids[0], self.owner)
                    self.assertEqual(busy.exception.status_code, 409)
                finally:
                    await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": PROCESSING_LOCK}); await connection.commit()
            batch = await parse_job(self.job_id, self.owner)
            self.assertEqual({r["status"] for r in batch["results"]}, {"completed", "needs_review"})
            data = self.paths[0].read_bytes()
            self.paths[0].write_bytes(data[:-1] + b"x")
            changed = await parse_cv(self.ids[0], self.owner)
            self.assertEqual(changed["status"], "needs_review")
            self.assertEqual(changed["error_code"], "FILE_HASH_MISMATCH")
            self.assertIsNone(changed["profile"])

    async def test_interrupted_checkpoint_resume_without_reextracting(self):
        entered = asyncio.Event()
        async def interrupt(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        with self.mocks(), patch("app.workflows.cv_parsing.extract_profile", side_effect=interrupt):
            task = asyncio.create_task(parse_cv(self.ids[0], self.owner))
            await asyncio.wait_for(entered.wait(), timeout=30)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        saved = await get_cv_profile(self.ids[0], self.owner)
        self.assertEqual(saved["status"], "parsing")
        with self.mocks(), patch("app.workflows.cv_parsing.extract_profile", side_effect=self.fake_extract), \
             patch("app.workflows.cv_parsing.isolated_extract", side_effect=AssertionError("Must reuse saved text/checkpoint")):
            result = await parse_cv(self.ids[0], self.owner)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["attempt"], saved["attempt"])

    async def test_failed_model_retry_reuses_saved_text(self):
        with self.mocks(), patch("app.workflows.cv_parsing.extract_profile", side_effect=ValueError("MODEL_SCHEMA_OR_EVIDENCE_INVALID")):
            failed = await parse_cv(self.ids[0], self.owner)
        self.assertEqual(failed["status"], "failed")
        with self.mocks(), patch("app.workflows.cv_parsing.extract_profile", side_effect=self.fake_extract), \
             patch("app.workflows.cv_parsing.isolated_extract", side_effect=AssertionError("Must reuse saved text")):
            result = await parse_cv(self.ids[0], self.owner)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["attempt"], failed["attempt"] + 1)

    async def test_model_timeout_retains_text(self):
        with self.mocks(), patch("app.workflows.cv_parsing.extract_profile", side_effect=TimeoutError()):
            result = await parse_cv(self.ids[0], self.owner)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "MODEL_TIMEOUT")
        async with SessionFactory() as db:
            saved = await db.scalar(select(CVParseResult).where(CVParseResult.id == UUID(result["id"])))
            self.assertIn("Alice Example", saved.text)

    async def test_actual_api_and_mcp_http_authorization_and_client(self):
        from datetime import timedelta
        url = os.environ.get("CV_TEST_API_URL", "http://127.0.0.1:8000")
        owner_headers = {"Authorization": f"Bearer {create_access_token(self.owner)}"}
        async with httpx.AsyncClient() as client:
            for suffix in (f"/cv/versions/{self.ids[0]}/profile", f"/cv/versions/{self.ids[0]}/parse", f"/cv/jobs/{self.job_id}/parse"):
                method = client.get if suffix.endswith("profile") else client.post
                self.assertEqual((await method(url + suffix)).status_code, 401)
                self.assertEqual((await method(url + suffix, headers={"Authorization": f"Bearer {create_access_token(self.other)}"})).status_code, 404)
                self.assertEqual((await method(url + suffix, headers={"Authorization": f"Bearer {create_access_token(self.viewer)}"})).status_code, 403)
            self.assertEqual((await client.post(url + "/mcp/", json={})).status_code, 401)
        # Real streamable HTTP, initialize, list, call; parsing runs server-side.
        async with streamablehttp_client(url + "/mcp/", headers=owner_headers, timeout=timedelta(seconds=900),
                                         sse_read_timeout=timedelta(seconds=900)) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=900)) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                self.assertTrue({"parse_cv", "get_cv_profile"}.issubset(names))
                result = await session.call_tool("get_cv_profile", {"file_version_id": str(self.ids[0])})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["status"], "not_started")
                denied = await session.call_tool("get_cv_profile", {"file_version_id": str(uuid4())})
                self.assertTrue(denied.isError)
                corrupt = await session.call_tool("parse_cv", {"file_version_id": str(self.ids[1])})
                self.assertFalse(corrupt.isError, corrupt)
                if os.environ.get("RUN_CV_MODEL_TESTS") == "1":
                    parsed = await session.call_tool("parse_cv", {"file_version_id": str(self.ids[0])})
                    self.assertFalse(parsed.isError, parsed)
                    payload = parsed.structuredContent
                    self.assertEqual(payload["status"], "completed", payload)
                    from app.services.cv_model import model_name
                    self.assertEqual(payload["versions"]["model"], model_name())
                    async with SessionFactory() as db:
                        saved = await db.get(CVParseResult, UUID(payload["id"]))
                        self.assertEqual(saved.profile, payload["profile"])
                        self.assertEqual(saved.evidence, payload["evidence"])
                        validate_profile(saved.profile, saved.text, saved.sources)
                        candidate = await db.scalar(select(Candidate).where(Candidate.recruiter_id == self.owner))
                        self.assertEqual(candidate.full_name, "Sheet Different")
                        self.assertEqual(candidate.email, "sheet@example.invalid")
                    reread = await session.call_tool("get_cv_profile", {"file_version_id": str(self.ids[0])})
                    self.assertEqual(reread.structuredContent["id"], payload["id"])
        other_headers = {"Authorization": f"Bearer {create_access_token(self.other)}"}
        async with streamablehttp_client(url + "/mcp/", headers=other_headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for tool in ("get_cv_profile", "parse_cv"):
                    denied = await session.call_tool(tool, {"file_version_id": str(self.ids[0])})
                    self.assertTrue(denied.isError)


if __name__ == "__main__":
    unittest.main()
