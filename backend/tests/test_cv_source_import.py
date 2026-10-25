"""Automatic source imports: fixed remote results, no real Google/model calls."""
import unittest
import os
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.cv import CVSource, CVSourceCheck
from app.services.cv import source_checks as checks


class AutomaticImportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source_id, self.owner = uuid4(), uuid4()
        self.previous = datetime.now(timezone.utc) - timedelta(hours=1)
        self.check = SimpleNamespace(pending_file_ids=["previous-file"],
            last_checked_at=self.previous, last_success_at=self.previous, error_code=None)
        self.reader = MagicMock()
        self.reader.scalar = AsyncMock(return_value=self.owner)
        self.writer = MagicMock()
        self.writer.get = AsyncMock(side_effect=lambda model, _: self.check if model is CVSourceCheck else SimpleNamespace())
        self.writer.commit = AsyncMock()
        self.sessions = []
        for db in (self.reader, self.writer):
            context = MagicMock()
            context.__aenter__ = AsyncMock(return_value=db)
            context.__aexit__ = AsyncMock(return_value=False)
            self.sessions.append(context)
        self.result = {"downloaded": 1, "skipped": 0, "failed": 0, "errors": [],
                       "sheet": {"rows_imported": 1, "rows_failed": 0}}

    async def invoke(self, *, result=None, error=None):
        with patch.object(checks, "AsyncSession", side_effect=self.sessions), \
             patch("app.services.cv.ingestion.fetch_cv_source", new_callable=AsyncMock) as fetch:
            fetch.return_value = self.result if result is None else result
            fetch.side_effect = error
            outcome = await checks.check_source(self.source_id)
        return outcome, fetch

    async def test_success_imports_as_source_owner_and_clears_pending_files(self):
        outcome, fetch = await self.invoke()
        self.assertTrue(outcome)
        fetch.assert_awaited_once_with(self.source_id, self.owner)
        self.assertEqual(self.check.pending_file_ids, [])
        self.assertIsNone(self.check.error_code)
        self.assertGreater(self.check.last_success_at, self.previous)
        self.writer.commit.assert_awaited_once()

    async def test_partial_import_is_visible_and_preserves_last_success(self):
        self.result.update(failed=1, errors=[{"drive_file_id": "failed-file"}])
        await self.invoke()
        self.assertEqual(self.check.error_code, "CV_SOURCE_IMPORT_INCOMPLETE")
        self.assertEqual(self.check.pending_file_ids, ["failed-file"])
        self.assertEqual(self.check.last_success_at, self.previous)

    async def test_sheet_row_failure_is_not_reported_as_success(self):
        self.result["sheet"]["rows_failed"] = 1
        await self.invoke()
        self.assertEqual(self.check.error_code, "CV_SOURCE_IMPORT_INCOMPLETE")
        self.assertEqual(self.check.last_success_at, self.previous)

    async def test_provider_failure_preserves_previous_state_for_next_pass(self):
        await self.invoke(error=HTTPException(502, "Unavailable"))
        self.assertEqual(self.check.error_code, "CV_SOURCE_CHECK_FAILED")
        self.assertEqual(self.check.pending_file_ids, ["previous-file"])
        self.assertEqual(self.check.last_success_at, self.previous)
        self.assertGreater(self.check.last_checked_at, self.previous)

    async def test_busy_source_stays_due_without_overwriting_status(self):
        outcome, _ = await self.invoke(error=HTTPException(409, "Busy"))
        self.assertFalse(outcome)
        self.assertEqual(self.check.last_checked_at, self.previous)
        self.writer.commit.assert_not_awaited()

    async def test_invalid_source_setup_has_a_distinct_error(self):
        await self.invoke(error=HTTPException(422, "Invalid response headers"))
        self.assertEqual(self.check.error_code, "CV_SOURCE_CONFIGURATION_INVALID")
        self.assertEqual(self.check.last_success_at, self.previous)

    async def test_missing_source_never_calls_importer(self):
        self.reader.scalar.return_value = None
        outcome, fetch = await self.invoke()
        self.assertFalse(outcome)
        fetch.assert_not_awaited()

    async def test_first_import_creates_persisted_check(self):
        self.writer.get.side_effect = lambda model, _: None if model is CVSourceCheck else SimpleNamespace()
        await self.invoke()
        saved = self.writer.add.call_args.args[0]
        self.assertEqual(saved.source_id, self.source_id)
        self.assertEqual(saved.pending_file_ids, [])
        self.assertIsNotNone(saved.last_success_at)


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_due_query_uses_one_minute_and_serializes_worker_passes(self):
        source_id = uuid4()
        connection = MagicMock()
        connection.scalar = AsyncMock(return_value=True)
        connection.scalars = AsyncMock(return_value=[source_id])
        connection.commit = AsyncMock()
        connection.rollback = AsyncMock()
        connection.execute = AsyncMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=connection)
        context.__aexit__ = AsyncMock(return_value=False)
        before = datetime.now(timezone.utc)
        with ExitStack() as stack:
            stack.enter_context(patch.object(checks, "engine", SimpleNamespace(connect=lambda: context)))
            stack.enter_context(patch("app.services.interviews.identification.purge_expired", new=AsyncMock()))
            run = stack.enter_context(patch.object(checks, "check_source", new=AsyncMock(return_value=True)))
            self.assertEqual(await checks.run_due_checks(), 1)
            run.assert_awaited_once_with(source_id)
            query = connection.scalars.call_args.args[0].compile()
            due = next(value for value in query.params.values() if isinstance(value, datetime))
            self.assertGreaterEqual(due, before - timedelta(seconds=60))
            self.assertLessEqual(due, datetime.now(timezone.utc) - timedelta(seconds=60))
            connection.scalar.return_value = False
            run.reset_mock()
            self.assertEqual(await checks.run_due_checks(), 0)
            run.assert_not_awaited()


class WorkerBootstrapTests(unittest.TestCase):
    def test_fresh_worker_registers_every_foreign_key_target(self):
        # Other suites import User themselves, which masked the standalone bug.
        import subprocess
        import sys
        script = """
from app.scripts import cv_source_check_worker
from app.services.interviews.identification import purge_expired
from app.services.cv.ingestion import fetch_cv_source
from app.db.base import Base
for table in Base.metadata.tables.values():
    for foreign_key in table.foreign_keys:
        assert foreign_key.column is not None
print('Standalone worker foreign keys resolved')
"""
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("foreign keys resolved", completed.stdout)


@unittest.skipUnless(os.environ.get("RUN_CV_IMPORT_DB_TESTS") == "1", "Opt in to disposable-schema import checks")
class ImportPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_imports_reuse_files_and_pick_up_new_submissions(self):
        from hashlib import sha256
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool
        from sqlalchemy.schema import CreateSchema, DropSchema
        from app.core.config import get_settings
        from app.db.base import Base
        from app.models.user import User
        from app.models.jd import JDConversation, JobDescription
        from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion
        from app.models.candidate_identity import CandidateIdentity
        from app.services.cv import ingestion

        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        schema = "cv_auto_import_" + uuid4().hex
        scoped = engine.execution_options(schema_translate_map={None: schema})
        factory = async_sessionmaker(scoped, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        try:
            tables = [model.__table__ for model in (User, JDConversation, JobDescription, CVSource,
                CVSourceCheck, Candidate, CandidateApplication, CVDocument, CVFileVersion, CandidateIdentity)]
            async with scoped.begin() as connection:
                await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
            async with factory() as db:
                user = User(email="import-fixture@example.com", password_hash="unused", role="recruiter")
                db.add(user)
                await db.flush()
                jd = JDConversation(recruiter_id=user.id)
                db.add(jd)
                await db.flush()
                job = JobDescription(conversation_id=jd.id, title="Invented role", content="Fixture", requirements="Fixture")
                db.add(job)
                await db.flush()
                source = CVSource(job_description_id=job.id, drive_folder_id="fixture_folder_1234567890",
                    spreadsheet_id="fixture_sheet_1234567890", sheet_tab_id=0)
                db.add(source)
                await db.commit()
                source_id = source.id
            with TemporaryDirectory(prefix="cv-auto-import-") as directory, ExitStack() as stack:
                root = Path(directory)
                files, rows = [], []
                content = b"%PDF-1.4\nInvented test download only\n%%EOF"
                checksum = sha256(content).hexdigest()
                def submit(number):
                    file_id = f"fixture_cv_file_{number}_1234567890"
                    files.append(dict(id=file_id, name=f"fixture-{number}.pdf", mimeType="application/pdf", sha256Checksum=checksum))
                    rows.append(dict(row_number=number + 1, values=[f"Candidate {number}", f"candidate{number}@example.com",
                        f"https://drive.google.com/file/d/{file_id}/view", "https://drive.google.com/file/d/fixture_photo_1234567890/view"]))
                def download(file_id, version_id, source_hash):
                    (root / f"{version_id}.pdf").write_bytes(content)
                    return dict(file_size_bytes=len(content), file_sha256=checksum, source_sha256=source_hash)
                stack.enter_context(patch.object(checks, "engine", scoped))
                stack.enter_context(patch.object(ingestion, "engine", scoped))
                stack.enter_context(patch.object(ingestion, "get_settings", return_value=SimpleNamespace(cv_storage_path=root)))
                stack.enter_context(patch.object(ingestion, "read_response_sheet", side_effect=lambda *_: dict(headers=["Name", "Email", "CV File", "Image"], rows=list(rows))))
                stack.enter_context(patch.object(ingestion, "list_source_cv_files", side_effect=lambda *_: list(files)))
                downloaded = stack.enter_context(patch.object(ingestion, "download_cv", side_effect=download))
                submit(1)
                self.assertTrue(await checks.check_source(source_id))
                self.assertTrue(await checks.check_source(source_id))
                self.assertEqual(downloaded.call_count, 1)
                submit(2)
                self.assertTrue(await checks.check_source(source_id))
                self.assertEqual(downloaded.call_count, 2)
                async with factory() as db:
                    for model in (Candidate, CandidateApplication, CVDocument, CVFileVersion):
                        self.assertEqual(await db.scalar(select(func.count()).select_from(model)), 2)
                    saved = await db.get(CVSourceCheck, source_id)
                    self.assertIsNone(saved.error_code)
                    self.assertEqual(saved.pending_file_ids, [])
                    self.assertIsNotNone(saved.last_success_at)
        finally:
            async with engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
