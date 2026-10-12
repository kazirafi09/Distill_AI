"""Real result/save/worker graph paths; only model-version discovery is fixed.

Run with RUN_INTERVIEW_DB_TESTS=1 and Windows Selector event loop on Windows.
No screening-results or database operations are mocked.
"""
import asyncio
import os
import unittest
from contextvars import ContextVar
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

import test_interview_selections as fixtures
from test_cv_scoring import profile
from app.db.session import SessionFactory
from app.models.cv import Candidate, CandidateApplication, CVSource
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.models.jd import JobDescription
from app.services.cv import screening
from app.services.interviews import selections
from app.services.interviews.schemas import SaveShortlistRequest
from app.services.interviews.shortlisting import preview_shortlist
from app.workflows.cv_screening import build_screening_graph
from app.workflows import cv_screening
from langgraph.checkpoint.memory import InMemorySaver


@unittest.skipUnless(os.environ.get("RUN_INTERVIEW_DB_TESTS") == "1", "Set RUN_INTERVIEW_DB_TESTS=1")
class LiveShortlistDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup_records = fixtures.SelectionDatabaseTests.cleanup_records
    member_ids = fixtures.SelectionDatabaseTests.member_ids
    request = fixtures.SelectionDatabaseTests.request

    async def asyncSetUp(self):
        await fixtures.SelectionDatabaseTests.asyncSetUp(self)
        for mocked in self.screening_patches:
            mocked.stop()
        versions = patch.object(screening, "processing_versions", AsyncMock(return_value={"synthetic": "v1"}))
        versions.start()
        self.addCleanup(versions.stop)
        async with SessionFactory() as db:
            job = await db.get(JobDescription, self.job_id)
            job.screening_profile, job.profile_version = profile(), 1
            await db.flush()
            run = await db.get(ScreeningRun, UUID(self.results["run"]["id"]))
            run.inputs = await screening.inputs_for(db, job, self.owner)
            run.input_key = screening.digest(run.inputs)
            self.run_id = run.id
            await db.commit()

    async def worker(self):
        # Runs the actual batch worker workflow, including ranking and commit.
        return await build_screening_graph(None).ainvoke({"run_id": str(self.run_id)})

    async def test_worker_failure_is_saved_after_rollback(self):
        async with SessionFactory() as db:
            assessment = await db.scalar(select(CVAssessment).where(
                CVAssessment.run_id == self.run_id))
            assessment.status = "pending"
            assessment_id = assessment.id
            await db.commit()

        @asynccontextmanager
        async def memory_checkpointer():
            yield InMemorySaver()

        with patch.object(cv_screening, "open_async_workflow_checkpointer", memory_checkpointer):
            await cv_screening.process_candidate(self.run_id, assessment_id)
        async with SessionFactory() as db:
            assessment = await db.get(CVAssessment, assessment_id)
            self.assertEqual(assessment.status, "needs_review")
            self.assertEqual(assessment.error_code, "NO_DOWNLOADED_CV")

    async def test_actual_worker_change_before_save_rejects_preview(self):
        request = await self.request(self.application_ids[:1])
        await self.worker()
        with self.assertRaises(HTTPException) as denied:
            await selections.save_shortlist(self.job_id, self.owner, request)
        self.assertEqual(denied.exception.status_code, 409)
        self.assertEqual(await self.member_ids(), {})

    async def test_actual_worker_write_waits_for_save_and_marks_snapshot_outdated(self):
        request = await self.request(self.application_ids[:1])
        final_check = asyncio.Event()
        release_save = asyncio.Event()
        worker_attempted = asyncio.Event()
        worker_acquired = asyncio.Event()
        calls = 0
        worker_task = None
        worker_context = ContextVar("shortlist_test_worker", default=False)
        actual_preview = selections.preview_shortlist
        actual_lock = screening.lock_screening_job

        async def paused_preview(*args, **kwargs):
            nonlocal calls
            result = await actual_preview(*args, **kwargs)
            calls += 1
            if calls == 2:
                final_check.set()
                await release_save.wait()
            return result

        async def observed_lock(*args, **kwargs):
            is_worker = worker_context.get()
            if is_worker:
                worker_attempted.set()
            await actual_lock(*args, **kwargs)
            if is_worker:
                worker_acquired.set()

        async def observed_worker():
            worker_context.set(True)
            return await self.worker()

        with patch.object(selections, "preview_shortlist", paused_preview), \
             patch.object(screening, "lock_screening_job", observed_lock):
            save_task = asyncio.create_task(selections.save_shortlist(self.job_id, self.owner, request))
            try:
                await asyncio.wait_for(final_check.wait(), 10)
                worker_task = asyncio.create_task(observed_worker())
                await asyncio.wait_for(worker_attempted.wait(), 10)
                self.assertFalse(worker_acquired.is_set())
                self.assertFalse(worker_task.done())
                release_save.set()
                await asyncio.wait_for(asyncio.gather(save_task, worker_task), 15)
                self.assertTrue(worker_acquired.is_set())
            finally:
                release_save.set()
                for task in (save_task, worker_task):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*[task for task in (save_task, worker_task) if task], return_exceptions=True)
        loaded = await selections.load_shortlist(self.job_id, self.owner)
        self.assertEqual(loaded["selected_count"], 1)
        self.assertTrue(loaded["outdated"])

    async def test_missing_scores_and_never_assessed_are_ineligible(self):
        async with SessionFactory() as db:
            assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == self.run_id)))
            assessments[0].score = None
            assessments[1].score = {"aggregate": "NaN"}
            source = await db.scalar(select(CVSource).where(CVSource.job_description_id == self.job_id))
            candidate = Candidate(recruiter_id=self.owner, full_name="Never assessed", email="never@example.invalid")
            db.add(candidate)
            await db.flush()
            application = CandidateApplication(candidate_id=candidate.id, job_description_id=self.job_id,
                                               source_id=source.id, source_row_number=10)
            db.add(application)
            await db.flush()
            # Keep this fixture's inputs current to isolate the exclusion reasons.
            job = await db.get(JobDescription, self.job_id)
            run = await db.get(ScreeningRun, self.run_id)
            run.inputs = await screening.inputs_for(db, job, self.owner)
            run.input_key = screening.digest(run.inputs)
            unassessed_id = application.id
            await db.commit()
        response = await preview_shortlist(self.job_id, self.owner, self.filters)
        reasons = {item["application_id"]: item["exclusion_reason"] for item in response["excluded"]}
        self.assertIn("score_missing", reasons.values())
        self.assertIn("score_invalid", reasons.values())
        self.assertEqual(reasons[str(unassessed_id)], "assessment_missing")
        self.assertNotIn(str(self.foreign_application_id), reasons)
        for identifier in (unassessed_id, assessments[0].application_id, assessments[1].application_id):
            with self.assertRaises(HTTPException) as denied:
                await selections.save_shortlist(self.job_id, self.owner, SaveShortlistRequest(
                    filters=self.filters, preview_key=response["preview_key"], selected_application_ids=[identifier]))
            self.assertEqual(denied.exception.status_code, 422)

    async def test_real_results_save_reload_and_cross_recruiter_isolation(self):
        saved = await selections.save_shortlist(self.job_id, self.owner,
                                               await self.request(self.application_ids[:2]))
        self.assertEqual(saved, await selections.load_shortlist(self.job_id, self.owner))
        self.assertFalse(saved["outdated"])
        with self.assertRaises(HTTPException) as denied:
            await preview_shortlist(self.job_id, self.other, self.filters)
        self.assertEqual(denied.exception.status_code, 404)
