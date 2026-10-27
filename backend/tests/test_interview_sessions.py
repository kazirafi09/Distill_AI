"""Session HTTP contracts and opt-in PostgreSQL lifecycle checks.

Database fixtures are synthetic and deleted after each test. LiveKit room deletion
is mocked; token generation uses the SDK with synthetic test credentials.
"""

import asyncio
import os
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr
from sqlalchemy import delete, select, update

from app.core.security import create_access_token
from app.db.session import SessionFactory, engine, get_db
from app.interview_routes import router
from app.models.cv import Candidate, CandidateApplication, CVSource
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.models.interviews import (
    InterviewInvitation, InterviewSession, InterviewShortlist, InterviewShortlistMember,
)
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.services.interviews import sessions
from app.services.interviews.invitations import create_invitation, token_hash
from app.services.interviews.identification import recruiter_identity


class SessionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = SimpleNamespace(id=uuid4(), role="recruiter", is_active=True)
        self.db = SimpleNamespace(get=AsyncMock(return_value=self.user), commit=AsyncMock())
        self.app = FastAPI()
        self.app.include_router(router)

        async def test_db():
            yield self.db

        self.app.dependency_overrides[get_db] = test_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test",
        )
        self.addAsyncCleanup(self.client.aclose)
        self.job_id, self.application_id = uuid4(), uuid4()
        self.base = f"/interviews/jobs/{self.job_id}/applications/{self.application_id}/session"
        self.headers = {"Authorization": f"Bearer {create_access_token(self.user.id)}"}

    async def test_start_passes_authenticated_owner_and_does_not_cache_token(self):
        with patch("app.interview_routes.start_interview", new_callable=AsyncMock) as start:
            start.return_value = {"participant_token": "synthetic", "status": "in_progress"}
            response = await self.client.post(self.base + "/start", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            start.assert_awaited_once_with(self.job_id, self.application_id, self.user.id)

    async def test_all_host_operations_require_login_and_recruiter_role(self):
        endpoints = [("GET", self.base)] + [
            ("POST", self.base + suffix) for suffix in ("/start", "/admit", "/join", "/end")
        ]
        for method, url in endpoints:
            with self.subTest(method=method, url=url):
                response = await self.client.request(method, url)
                self.assertEqual(response.status_code, 401)
                self.user.role = "interviewer"
                response = await self.client.request(method, url, headers=self.headers)
                self.assertEqual(response.status_code, 403)
                self.user.role = "recruiter"

    async def test_candidate_token_is_in_body_and_response_is_not_cached(self):
        token = secrets.token_urlsafe(32)
        for endpoint, target in (("status", "candidate_status"), ("join", "candidate_join")):
            with self.subTest(endpoint=endpoint):
                with patch(f"app.interview_routes.{target}", new_callable=AsyncMock) as service:
                    service.return_value = {"status": "in_progress"}
                    response = await self.client.post(f"/interviews/candidate/{endpoint}", json={"token": token})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    service.assert_awaited_once_with(token)

    async def test_malformed_candidate_links_are_rejected_without_db_queries(self):
        for endpoint in ("status", "join"):
            response = await self.client.post(f"/interviews/candidate/{endpoint}", json={"token": "bad token"})
            self.assertEqual(response.status_code, 410)
            self.assertNotIn("bad token", response.text)

    async def test_bad_ids_and_missing_candidate_body_are_rejected(self):
        response = await self.client.post(self.base.replace(str(self.job_id), "invalid") + "/start", headers=self.headers)
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/interviews/candidate/join", json={})
        self.assertEqual(response.status_code, 422)


@unittest.skipUnless(os.environ.get("RUN_INTERVIEW_DB_TESTS") == "1", "Set RUN_INTERVIEW_DB_TESTS=1")
class SessionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        transcription = patch("app.services.interviews.live_transcription.start_transcription")
        transcription.start()
        self.addCleanup(transcription.stop)
        self.owner, self.other = uuid4(), uuid4()
        self.addAsyncCleanup(self.cleanup_records)
        self.token = secrets.token_urlsafe(32)
        self.close = AsyncMock()
        closer = patch.object(sessions, "close_room", self.close)
        closer.start()
        self.addCleanup(closer.stop)
        settings = SimpleNamespace(
            livekit_api_key="test-key",
            livekit_api_secret=SecretStr("synthetic-session-test-secret-32-characters"),
            livekit_token_ttl_seconds=300, livekit_ws_url="ws://localhost:7880",
        )
        config = patch("app.services.interviews.livekit.get_settings", return_value=settings)
        config.start()
        self.addCleanup(config.stop)
        async with SessionFactory() as db:
            for owner in (self.owner, self.other):
                db.add(User(id=owner, email=f"session-{owner}@example.invalid", password_hash="synthetic", role="recruiter"))
            await db.flush()
            for owner in (self.owner, self.other):
                conversation = JDConversation(recruiter_id=owner)
                db.add(conversation)
                await db.flush()
                job = JobDescription(conversation_id=conversation.id, title="Session fixture", content="Synthetic", requirements="Synthetic")
                db.add(job)
                await db.flush()
                source = CVSource(job_description_id=job.id, drive_folder_id=f"session-{uuid4()}")
                db.add(source)
                await db.flush()
                candidate = Candidate(recruiter_id=owner, full_name="Synthetic candidate", email=f"session-{owner}@example.com")
                db.add(candidate)
                await db.flush()
                application = CandidateApplication(candidate_id=candidate.id, job_description_id=job.id, source_id=source.id, source_row_number=2)
                db.add(application)
                await db.flush()
                if owner == self.other:
                    self.foreign_job, self.foreign_application = job.id, application.id
                    continue
                self.job_id, self.application_id = job.id, application.id
                self.email = candidate.email
                unselected = Candidate(recruiter_id=owner, full_name="Unselected fixture", email=f"unselected-{owner}@example.com")
                db.add(unselected)
                await db.flush()
                extra = CandidateApplication(candidate_id=unselected.id, job_description_id=job.id, source_id=source.id, source_row_number=3)
                db.add(extra)
                await db.flush()
                self.unselected_id = extra.id
                run = ScreeningRun(job_id=job.id, recruiter_id=owner, input_key="a" * 64, inputs={}, status="completed")
                db.add(run)
                await db.flush()
                assessment = CVAssessment(run_id=run.id, application_id=application.id, file_version_ids=[], status="completed", score={"aggregate": 90})
                db.add(assessment)
                await db.flush()
                shortlist = InterviewShortlist(job_description_id=job.id, recruiter_id=owner, screening_run_id=run.id, filter_inputs={}, proposed_application_ids=[str(application.id)])
                db.add(shortlist)
                await db.flush()
                db.add(InterviewShortlistMember(shortlist_id=shortlist.id, application_id=application.id, assessment_id=assessment.id, score_snapshot={"aggregate": 90}, rank_snapshot=1))
                invitation = InterviewInvitation(recruiter_id=owner, job_description_id=job.id, application_id=application.id, recipient_email=candidate.email, token_hash=token_hash(self.token), expires_at=datetime.now(timezone.utc) + timedelta(days=1))
                db.add(invitation)
                await db.flush()
                self.invitation_id = invitation.id
            await db.commit()

    async def cleanup_records(self):
        try:
            async with SessionFactory() as db:
                await db.execute(delete(User).where(User.id.in_((self.owner, self.other))))
                await db.commit()
        finally:
            await engine.dispose()

    async def start(self):
        return await sessions.start_interview(self.job_id, self.application_id, self.owner)

    async def admit(self):
        await recruiter_identity(self.job_id, self.application_id, self.owner,
                                 review_note="Synthetic manual review for session lifecycle tests")
        return await sessions.admit_candidate(self.job_id, self.application_id, self.owner)

    async def end(self):
        return await sessions.end_interview(self.job_id, self.application_id, self.owner)

    async def record(self):
        async with SessionFactory() as db:
            return await db.scalar(select(InterviewSession).where(InterviewSession.application_id == self.application_id))

    async def test_wait_start_admit_join_and_completion(self):
        self.assertEqual((await sessions.candidate_status(self.token))["status"], "waiting_for_recruiter")
        started = await self.start()
        self.assertFalse((await sessions.candidate_status(self.token))["can_join"])
        with self.assertRaises(HTTPException) as denied:
            await sessions.candidate_join(self.token)
        self.assertEqual(denied.exception.status_code, 403)
        await self.admit()
        self.assertTrue((await sessions.recruiter_status(self.job_id, self.application_id, self.owner))["candidate_admitted"])
        candidate = await sessions.candidate_join(self.token)
        secret = "synthetic-session-test-secret-32-characters"
        host_claims = jwt.decode(started["participant_token"], secret, algorithms=["HS256"])
        candidate_claims = jwt.decode(candidate["participant_token"], secret, algorithms=["HS256"])
        self.assertEqual(host_claims["video"]["room"], candidate_claims["video"]["room"])
        self.assertNotEqual(host_claims["sub"], candidate_claims["sub"])
        completed = await self.end()
        self.assertEqual(completed["status"], "completed")
        self.assertIsNotNone(completed["completed_at"])
        self.assertFalse((await sessions.candidate_status(self.token))["can_join"])

    async def test_concurrent_starts_reuse_session_and_started_time(self):
        first, second = await asyncio.gather(self.start(), self.start())
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["started_at"], second["started_at"])
        self.assertEqual((await self.record()).version, 2)

    async def test_other_recruiter_and_foreign_application_are_rejected(self):
        for job, application, owner in (
            (self.job_id, self.application_id, self.other),
            (self.job_id, self.foreign_application, self.owner),
            (self.foreign_job, self.application_id, self.owner),
        ):
            with self.subTest(job=job, application=application), self.assertRaises(HTTPException) as denied:
                await sessions.start_interview(job, application, owner)
            self.assertEqual(denied.exception.status_code, 404)

    async def test_unselected_and_missing_invitation_cannot_start(self):
        with self.assertRaises(HTTPException) as denied:
            await sessions.start_interview(self.job_id, self.unselected_id, self.owner)
        self.assertEqual(denied.exception.status_code, 409)
        async with SessionFactory() as db:
            await db.execute(delete(InterviewInvitation).where(InterviewInvitation.id == self.invitation_id))
            await db.commit()
        with self.assertRaises(HTTPException) as denied:
            await self.start()
        self.assertEqual(denied.exception.status_code, 409)
        self.assertIsNone(await self.record())

    async def test_expired_revoked_and_unknown_links_are_rejected(self):
        for change in (
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
            {"expires_at": datetime.now(timezone.utc) + timedelta(days=1), "revoked_at": datetime.now(timezone.utc)},
        ):
            async with SessionFactory() as db:
                await db.execute(update(InterviewInvitation).where(InterviewInvitation.id == self.invitation_id).values(**change))
                await db.commit()
            for action in (sessions.candidate_status, sessions.candidate_join):
                with self.assertRaises(HTTPException) as denied:
                    await action(self.token)
                self.assertEqual(denied.exception.status_code, 410)
        with self.assertRaises(HTTPException) as denied:
            await sessions.candidate_status(secrets.token_urlsafe(32))
        self.assertEqual(denied.exception.status_code, 410)

    async def test_reissued_link_requires_new_admission(self):
        await self.start()
        await self.admit()
        async with SessionFactory() as db:
            _, link = await create_invitation(db, self.job_id, self.owner, self.application_id, self.email, replace=True)
            await db.commit()
        new_token = link.split("#")[1]
        with self.assertRaises(HTTPException):
            await sessions.candidate_join(self.token)
        self.assertFalse((await sessions.candidate_status(new_token))["can_join"])
        with self.assertRaises(HTTPException) as denied:
            await sessions.candidate_join(new_token)
        self.assertEqual(denied.exception.status_code, 403)
        await self.admit()
        self.assertIn("participant_token", await sessions.candidate_join(new_token))

    async def test_failed_closure_blocks_tokens_and_can_be_retried(self):
        await self.start()
        await self.admit()
        self.close.side_effect = TimeoutError()
        with self.assertRaises(HTTPException) as failure:
            await self.end()
        self.assertEqual(failure.exception.status_code, 503)
        self.assertEqual((await self.record()).status, "closing")
        for action in (
            lambda: sessions.candidate_join(self.token),
            lambda: sessions.recruiter_join(self.job_id, self.application_id, self.owner),
            self.start,
        ):
            with self.assertRaises(HTTPException) as denied:
                await action()
            self.assertEqual(denied.exception.status_code, 409)
        self.close.side_effect = None
        completed = await self.end()
        again = await self.end()
        self.assertEqual(again["completed_at"], completed["completed_at"])
        self.assertEqual(self.close.await_count, 2)

    async def test_admit_racing_end_cannot_leave_join_access_after_completion(self):
        await self.start()
        results = await asyncio.gather(self.admit(), self.end(), return_exceptions=True)
        self.assertEqual((await self.record()).status, "completed")
        self.assertFalse((await sessions.candidate_status(self.token))["can_join"])
        for result in results:
            if isinstance(result, Exception):
                self.assertIsInstance(result, HTTPException)
                self.assertEqual(result.status_code, 409)


if __name__ == "__main__":
    unittest.main()
