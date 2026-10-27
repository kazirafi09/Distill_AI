"""Real PostgreSQL lifecycle/ownership/recovery checks; only model HTTP is mocked."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, select, func

from app.auth import require_recruiter, get_current_user
from app.assessment_routes import router
from app.db.session import SessionFactory, engine
from app.models.assessments import AssessmentRun, AssessmentAnswer, AssessmentResult, AssessmentStage
from app.models.cv import Candidate, CandidateApplication, CVSource
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.models.interviews import InterviewSession
from app.models.interview_transcripts import InterviewTranscript, InterviewTranscriptTurn
from app.scripts import assessment_worker as worker
from app.services.interviews import assessment_runs as runs, assessment_store as store, analysis
from app.services.interviews.transcript_preparation import AlignedTurn
import test_interview_analysis as model_fixtures


class AssessmentInputTests(unittest.TestCase):
    def test_client_cannot_attach_sample_to_candidate_or_supply_raw_input(self):
        for payload in ({"source": "synthetic_demo", "application_id": str(uuid4())},
                        {"source": "interview"}, {"source": "synthetic_demo", "prepared": {}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                runs.StartAssessment.model_validate(payload)


@unittest.skipUnless(os.environ.get("RUN_ASSESSMENT_DB_TESTS") == "1", "Set RUN_ASSESSMENT_DB_TESTS=1")
class AssessmentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.model = model_fixtures.InterviewAnalysisTests()
        await self.model.asyncSetUp()
        self.addCleanup(self.model.tmp.cleanup)
        self.owner, self.other = uuid4(), uuid4()
        self.addAsyncCleanup(self.cleanup)
        async with SessionFactory() as db:
            for user_id in (self.owner, self.other):
                db.add(User(id=user_id, email=f"assessment-{user_id}@example.invalid", password_hash="synthetic-test-only", role="recruiter"))
            await db.flush()
            conversation = JDConversation(recruiter_id=self.owner)
            db.add(conversation)
            await db.flush()
            job = JobDescription(conversation_id=conversation.id, title="Synthetic assessment test", content="Synthetic",
                requirements="Synthetic", screening_profile={"criteria": self.model.input["criteria"]}, profile_version=1)
            db.add(job)
            await db.flush()
            self.job = job.id
            source = CVSource(job_description_id=job.id, drive_folder_id=f"assessment-{uuid4()}")
            candidate = Candidate(recruiter_id=self.owner, full_name="Synthetic test candidate", email=f"{uuid4()}@example.invalid")
            db.add_all([source, candidate])
            await db.flush()
            application = CandidateApplication(candidate_id=candidate.id, job_description_id=job.id,
                source_id=source.id, source_row_number=2)
            db.add(application)
            await db.flush()
            self.application = application.id
            await db.commit()
        self.generator = patch.object(analysis, "generate_json", side_effect=self.model.generate)
        self.version = patch.object(worker, "model_version", AsyncMock(return_value={"model": "test-model", "provider": "gemini", "model_digest": "test-fixed"}))
        self.generator.start()
        self.version_mock = self.version.start()
        self.addCleanup(self.generator.stop)
        self.addCleanup(self.version.stop)

    async def cleanup(self):
        async with SessionFactory() as db:
            await db.execute(delete(User).where(User.id.in_([self.owner, self.other])))
            await db.commit()
        await engine.dispose()

    async def start(self):
        return await runs.start_run(self.job, self.owner, runs.StartAssessment(source="synthetic_demo"))

    async def test_duplicate_starts_are_serialized_and_results_are_owned(self):
        first, second = await asyncio.gather(self.start(), self.start())
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(await worker.run_once(UUID(first["id"])))
        result = await runs.run_result(UUID(first["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(len(result["answers"]), 7)
        self.assertFalse(result["result"]["f6_ready"])
        self.assertIsNone(result["run"]["application_id"])
        self.assertTrue(result["run"]["synthetic"])
        self.assertEqual(result["answers"][-1]["evaluations"]["completeness"]["score_100"], 0)
        self.assertIsNone(result["answers"][-1]["evaluations"]["accuracy"]["score_100"])
        for operation in (runs.run_result, runs.run_status, runs.retry_run):
            with self.assertRaises(HTTPException) as raised:
                await operation(UUID(first["id"]), self.other)
            self.assertEqual(raised.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            await runs.workspace(self.job, self.other)
        self.assertFalse(await worker.run_once(UUID(first["id"])))
        self.assertEqual(len(self.model.calls), 26)

    async def test_expired_lease_recovers_and_fences_old_worker_writes(self):
        run = await self.start()
        old = await store.claim(UUID(run["id"]))
        self.assertIsNone(await store.claim(UUID(run["id"])))
        async with SessionFactory() as db:
            row = await db.get(AssessmentRun, UUID(run["id"]))
            row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        new = await store.claim(UUID(run["id"]))
        self.assertNotEqual(new["storage_token"], old["storage_token"])
        self.assertFalse(await store.heartbeat(old))
        with self.assertRaises(store.LeaseLost):
            await store.write_stage(old, "grouping", {"groups": []})
        with self.assertRaises(store.LeaseLost):
            await store.fail(old, "STALE_WORKER")
        await worker.process(new)
        self.assertEqual((await runs.run_status(UUID(run["id"]), self.owner))["status"], "completed")

    async def test_crash_after_answer_commit_replays_without_duplicate_model_calls(self):
        run = await self.start()
        original = store.save_answer
        committed = asyncio.Event()
        stopped = asyncio.Event()

        async def crash_after_commit(state, result):
            await original(state, result)
            committed.set()
            await stopped.wait()

        with patch.object(store, "save_answer", side_effect=crash_after_commit):
            task = asyncio.create_task(worker.run_once(UUID(run["id"])))
            try:
                await asyncio.wait_for(committed.wait(), 15)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        async with SessionFactory() as db:
            row = await db.get(AssessmentRun, UUID(run["id"]))
            self.assertEqual(row.status, "running")
            self.assertEqual(row.completed_groups, 1)
            row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        await worker.run_once(UUID(run["id"]))
        result = await runs.run_result(UUID(run["id"]), self.owner)
        self.assertEqual(result["run"]["completed_groups"], 7)
        self.assertEqual(len(self.model.calls), 26)
        self.version_mock.assert_awaited_once()
        async with SessionFactory() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(AssessmentAnswer).where(AssessmentAnswer.run_id == UUID(run["id"]))), 7)

    async def test_transient_failure_retries_only_unfinished_stage(self):
        run = await self.start()
        failed_once = False

        async def unavailable(system, payload, schema, *args, **kwargs):
            nonlocal failed_once
            if '"id":"g2"' in payload and "Evaluate ONLY accuracy" in system and not failed_once:
                failed_once = True
                raise ValueError("MODEL_NOT_AVAILABLE")
            return await self.model.generate(system, payload, schema, *args, **kwargs)

        with patch.object(analysis, "generate_json", side_effect=unavailable):
            await worker.run_once(UUID(run["id"]))
        status = await runs.run_status(UUID(run["id"]), self.owner)
        self.assertEqual(status["status"], "queued")
        self.assertEqual(status["stage"], "retry_wait")
        async with SessionFactory() as db:
            row = await db.get(AssessmentRun, UUID(run["id"]))
            row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            attempt = await db.get(AssessmentStage, (row.id, "evaluation-g2-accuracy-attempts"))
            self.assertEqual(attempt.payload["count"], 0)
            await db.commit()
        await worker.run_once(UUID(run["id"]))
        self.assertEqual((await runs.run_status(UUID(run["id"]), self.owner))["status"], "completed")
        self.assertEqual(len(self.model.calls), 26)  # Successful calls, including cached first-group work only once.

    async def test_retry_limits_and_version_changes(self):
        run = await self.start()
        async with SessionFactory() as db:
            row = await db.get(AssessmentRun, UUID(run["id"]))
            row.attempt, row.status = 6, "failed"
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.retry_run(UUID(run["id"]), self.owner)
        async with SessionFactory() as db:
            row = await db.get(AssessmentRun, UUID(run["id"]))
            row.attempt, row.status = 0, "queued"
            row.inputs = {**row.inputs, "prompt_version": "old-version"}
            await db.commit()
        await worker.run_once(UUID(run["id"]))
        self.assertEqual((await runs.run_status(UUID(run["id"]), self.owner))["error_code"], "ASSESSMENT_VERSION_CHANGED")
        self.assertEqual(self.model.calls, [])

    async def test_manual_retry_after_configuration_failure_reuses_saved_work(self):
        run = await self.start()

        async def auth_failure(system, payload, schema, *args, **kwargs):
            if '"id":"g2"' in payload and "Evaluate ONLY accuracy" in system:
                raise ValueError("MODEL_AUTH_FAILED")
            return await self.model.generate(system, payload, schema, *args, **kwargs)

        with patch.object(analysis, "generate_json", side_effect=auth_failure):
            await worker.run_once(UUID(run["id"]))
        status = await runs.run_status(UUID(run["id"]), self.owner)
        self.assertEqual(status["status"], "failed")
        self.assertTrue(status["can_retry"])
        self.assertEqual((await runs.retry_run(UUID(run["id"]), self.owner))["status"], "queued")
        await worker.run_once(UUID(run["id"]))
        self.assertEqual((await runs.run_status(UUID(run["id"]), self.owner))["status"], "completed")
        self.assertEqual(len(self.model.calls), 26)

    async def test_invalid_model_output_exhausts_persisted_repairs_and_requires_review(self):
        run = await self.start()
        with patch.object(analysis, "generate_json", AsyncMock(return_value="{}")) as generate:
            await worker.run_once(UUID(run["id"]))
        self.assertEqual(generate.await_count, 2)
        status = await runs.run_status(UUID(run["id"]), self.owner)
        self.assertEqual(status["status"], "needs_review")
        self.assertFalse(status["can_retry"])
        self.assertFalse(await worker.run_once(UUID(run["id"])))
        with self.assertRaises(HTTPException):
            await runs.retry_run(UUID(run["id"]), self.owner)
        async with SessionFactory() as db:
            stage = await db.get(AssessmentStage, (UUID(run["id"]), "grouping-attempts"))
            self.assertEqual(stage.payload["count"], 2)

    async def test_api_authentication_ownership_and_no_store(self):
        run = await self.start()
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertIn((await client.get(f'/assessments/runs/{run["id"]}')).status_code, (401, 403))
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="interviewer")
            self.assertEqual((await client.get(f'/assessments/runs/{run["id"]}')).status_code, 403)
            app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=self.other)
            self.assertEqual((await client.get(f'/assessments/runs/{run["id"]}/result')).status_code, 404)
            app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=self.owner)
            response = await client.get(f'/assessments/runs/{run["id"]}/result')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            response = await client.post(f"/assessments/jobs/{self.job}/f5", json={"source": "synthetic_demo", "application_id": str(self.application)})
            self.assertEqual(response.status_code, 422)

    async def mark_synthetic_transcript(self):
        async with SessionFactory() as db:
            session = await db.scalar(select(InterviewSession).where(InterviewSession.application_id == self.application))
            transcript = await db.scalar(select(InterviewTranscript).where(InterviewTranscript.session_id == session.id))
            transcript.provider, transcript.model = "synthetic_fixture", "owned-synthetic-test-v1"
            transcript.quality_flags = ["SYNTHETIC_DEVELOPMENT_ONLY"]
            rows = list(await db.scalars(select(InterviewTranscriptTurn).where(InterviewTranscriptTurn.transcript_id == transcript.id)))
            aligned = []
            for row in rows:
                data = {name: getattr(row, name) for name in ("id", "transcript_id", "stream_id", "source_event_key",
                    "speaker_role", "participant_identity", "text", "stream_start_ms", "stream_end_ms",
                    "session_start_ms", "session_end_ms", "quality_flags", "created_at")}
                aligned.append(AlignedTurn.model_validate({**data, "timing_basis": "synthetic_authoring_ground_truth"}).model_dump(mode="json"))
            aligned.sort(key=lambda t: (t["session_start_ms"], t["session_end_ms"], t["id"]))
            transcript.content_sha256 = analysis.digest(aligned)
            await db.commit()

    async def test_named_synthetic_interview_is_ready_but_not_a_real_candidate_result(self):
        await self.add_real_transcript()
        await self.mark_synthetic_transcript()
        workspace = await runs.workspace(self.job, self.owner)
        candidate = next(c for c in workspace["candidates"] if c["id"] == str(self.application))
        self.assertTrue(candidate["ready"])
        self.assertIn("synthetic test", candidate["name"])
        started = await runs.start_run(self.job, self.owner, runs.StartAssessment(application_id=self.application))
        self.assertTrue(started["synthetic"])
        self.assertIsNone(started["application_id"])
        async with SessionFactory() as db:
            saved = await db.get(AssessmentRun, UUID(started["id"]))
            self.assertIsNone(saved.transcript_id)
            self.assertEqual(saved.inputs["test_application_id"], str(self.application))
        await worker.run_once(UUID(started["id"]))
        result = await runs.run_result(UUID(started["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["result"]["status"], "completed_synthetic")
        self.assertFalse(result["result"]["publishable"])

    async def add_real_transcript(self):
        async with SessionFactory() as db:
            session = InterviewSession(recruiter_id=self.owner, job_description_id=self.job,
                application_id=self.application, room_name=f"assessment-{uuid4()}", status="completed")
            db.add(session)
            await db.flush()
            transcript = InterviewTranscript(session_id=session.id, version=1, status="completed", provider="deepgram",
                model="nova-3", language="en", quality_flags=[], finalized_at=datetime.now(timezone.utc))
            db.add(transcript)
            await db.flush()
            rows, aligned = [], []
            for source in self.model.prepared["turns"]:
                item = deepcopy(source)
                item["id"], item["transcript_id"] = str(uuid4()), str(transcript.id)
                item["participant_identity"] = f"recruiter-{self.owner}" if item["speaker_role"] == "recruiter" else f"candidate-{self.application}"
                item["quality_flags"] = []
                item["timing_basis"] = "finalized_session_timeline"
                aligned.append(AlignedTurn.model_validate(item).model_dump(mode="json"))
                row = {key: value for key, value in item.items() if key != "timing_basis"}
                for key in ("id", "transcript_id", "stream_id"):
                    row[key] = UUID(row[key])
                row["created_at"] = datetime.fromisoformat(row["created_at"])
                rows.append(InterviewTranscriptTurn(**row))
            db.add_all(rows)
            aligned.sort(key=lambda t: (t["session_start_ms"], t["session_end_ms"], t["id"]))
            transcript.content_sha256 = analysis.digest(aligned)
            await db.commit()
            return transcript.id

    async def test_real_transcript_readiness_digest_and_saved_input_isolation(self):
        request = runs.StartAssessment(application_id=self.application)
        with self.assertRaises(HTTPException):
            await runs.start_run(self.job, self.owner, request)
        transcript_id = await self.add_real_transcript()
        real = await runs.start_run(self.job, self.owner, request)
        self.assertFalse(real["synthetic"])
        async with SessionFactory() as db:
            row = await db.get(InterviewTranscript, transcript_id)
            row.quality_flags = ["FINALIZATION_PENDING"]
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.start_run(self.job, self.owner, request)
        async with SessionFactory() as db:
            row = await db.get(InterviewTranscript, transcript_id)
            row.quality_flags, row.content_sha256 = [], "0" * 64
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.start_run(self.job, self.owner, request)
        await worker.run_once(UUID(real["id"]))
        self.assertEqual(self.model.calls, [])  # Source changed after enqueue; no model call.

    async def test_finalized_real_input_uses_same_graph_and_stored_results(self):
        await self.add_real_transcript()
        real = await runs.start_run(self.job, self.owner, runs.StartAssessment(application_id=self.application))
        await worker.run_once(UUID(real["id"]))
        result = await runs.run_result(UUID(real["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertFalse(result["result"]["synthetic"])
        self.assertEqual(result["result"]["status"], "completed")
        self.assertEqual(len(result["answers"]), 7)
        self.assertTrue(all(turn["timing_basis"] == "finalized_session_timeline" for turn in result["source_turns"]))

    async def test_saved_profile_versions_remain_reviewable_after_job_edit(self):
        run = await self.start()
        async with SessionFactory() as db:
            job = await db.get(JobDescription, self.job)
            job.profile_version = 2
            job.screening_profile = {"criteria": [{**self.model.input["criteria"][0], "text": "Changed criterion"}]}
            await db.commit()
        result = await runs.run_result(UUID(run["id"]), self.owner)
        self.assertTrue(result["outdated"])
        self.assertEqual(result["criteria"], self.model.input["criteria"])
        new = await self.start()
        self.assertNotEqual(run["id"], new["id"])


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    unittest.main()
