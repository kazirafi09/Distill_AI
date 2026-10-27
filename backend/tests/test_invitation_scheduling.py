"""Scheduled invitations and calendar content; no real emails or model calls."""
import os
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from app.integrations.calendar_event import CalendarEvent, calendar_ics, google_calendar_link
from app.services.interviews.scheduling import InvitationSchedule, join_window, require_join_window
from app.services.interviews.invitation_content import invitation_body, private_interview_link


class CalendarContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.start = datetime.now(timezone.utc) + timedelta(days=2)
        self.end = self.start + timedelta(minutes=30)
        self.schedule = InvitationSchedule(application_id=uuid4(), starts_at=self.start,
            ends_at=self.end, display_timezone="Asia/Dhaka")
        self.link = "https://example.com/interview#" + "a" * 43

    def test_schedule_rejects_naive_reversed_and_unknown_timezone(self):
        for changes in ({"starts_at": self.start.replace(tzinfo=None)}, {"ends_at": self.start},
                        {"display_timezone": "Invalid/Timezone"}):
            with self.assertRaises(ValidationError):
                InvitationSchedule(**{**self.schedule.model_dump(), **changes})

    def test_google_link_preserves_utc_times_and_private_fragment(self):
        query = parse_qs(urlsplit(google_calendar_link("Python developer", self.schedule.public(), self.link)).query)
        self.assertEqual(query["location"], [self.link])
        self.assertEqual(query["ctz"], ["Asia/Dhaka"])
        self.assertIn(self.start.strftime("%Y%m%dT%H%M%SZ"), query["dates"][0])
        self.assertEqual(query["action"], ["TEMPLATE"])

    def test_calendar_escapes_newlines_folds_unicode_and_uses_stable_uid(self):
        event = CalendarEvent(uid=uuid4(), title="বাংলা " * 20 + "\nInjected, heading;", starts_at=self.start,
            ends_at=self.end, link=self.link)
        value = calendar_ics(event)
        self.assertIn(f"UID:interview-{event.uid}@distill.ai", value)
        self.assertTrue(all(len(line.encode()) <= 75 for line in value.split("\r\n")))
        self.assertIn(r"\nInjected\, heading\;", value.replace("\r\n ", ""))
        self.assertIn("METHOD:PUBLISH", value)
        self.assertEqual(value.count("BEGIN:VEVENT"), 1)

    def test_professional_email_and_legacy_link_extraction(self):
        body = invitation_body("Python developer", "Ada", self.schedule.public(), self.link)
        self.assertIn("Dear Ada,", body)
        self.assertIn("Asia/Dhaka", body)
        self.assertIn("10 minutes", body)
        self.assertNotIn("calendar.google.com", body)
        self.assertNotIn("{google_calendar_link}", body)
        self.assertNotIn("interview.ics", body)
        self.assertEqual(private_interview_link({"body": body}), self.link)
        self.assertEqual(private_interview_link({"body": "Legacy\n\n" + self.link}), self.link)
        self.assertNotIn("calendar.google.com", invitation_body("Role", include_calendar=False))

    def test_join_window_boundaries_and_unscheduled_compatibility(self):
        session = SimpleNamespace(application_id=self.schedule.application_id, starts_at=self.start,
            ends_at=self.end, display_timezone="Asia/Dhaka")
        opening = self.start - timedelta(minutes=10)
        self.assertFalse(join_window(session, now=opening - timedelta(microseconds=1))["join_window_open"])
        self.assertTrue(join_window(session, now=opening)["join_window_open"])
        self.assertTrue(join_window(session, now=self.end - timedelta(microseconds=1))["join_window_open"])
        self.assertFalse(join_window(session, now=self.end)["join_window_open"])
        with self.assertRaises(HTTPException):
            require_join_window(session)
        self.assertIsNone(join_window(None))
        self.assertIsNone(join_window(SimpleNamespace(starts_at=None, ends_at=None)))

    async def test_smtp_has_plain_text_and_one_calendar_attachment(self):
        from app.integrations import gmail
        settings = SimpleNamespace(smtp_username="sender@example.com", smtp_password=SecretStr("fixture"))
        event = CalendarEvent(uid=uuid4(), title="Interview", starts_at=self.start, ends_at=self.end, link=self.link)
        message = gmail.EmailMessageRequest(recipient="candidate@example.com", subject="Interview", body="Fixture", calendar_event=event)
        with patch.object(gmail, "get_settings", return_value=settings), patch.object(gmail, "run_in_threadpool", new=AsyncMock()) as send:
            await gmail.send_email(message)
        mime = send.call_args.args[2]
        self.assertEqual(mime.get_body(preferencelist=("plain",)).get_content().strip(), "Fixture")
        attachments = list(mime.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "interview.ics")
        self.assertEqual(attachments[0].get_content_type(), "text/calendar")
        self.assertIn(b"BEGIN:VCALENDAR", attachments[0].get_payload(decode=True))

    def test_candidate_room_credential_cannot_outlive_joining_window(self):
        import jwt
        from app.services.interviews import livekit
        end = datetime.now(timezone.utc) + timedelta(minutes=2)
        settings = SimpleNamespace(livekit_api_key="fixture-key", livekit_api_secret=SecretStr("fixture-secret-at-least-32-characters"),
            livekit_token_ttl_seconds=1200, livekit_ws_url="wss://example.com")
        with patch.object(livekit, "get_settings", return_value=settings):
            credential = livekit.create_join_token("fixture-room", "fixture-candidate", expires_at=end)
            claims = jwt.decode(credential["participant_token"], options={"verify_signature": False})
            self.assertLessEqual(claims["exp"], end.timestamp())
            with self.assertRaises(HTTPException):
                livekit.create_join_token("fixture-room", "fixture-candidate", expires_at=end - timedelta(hours=1))

    async def test_candidate_cannot_join_early_even_if_recruiter_has_started(self):
        from app.services.interviews import sessions
        session = SimpleNamespace(application_id=self.schedule.application_id, starts_at=self.start,
            ends_at=self.end, display_timezone="Asia/Dhaka", status="in_progress")
        context = AsyncMock()
        with patch.object(sessions, "SessionFactory", return_value=context), \
             patch.object(sessions, "candidate_session", new=AsyncMock(return_value=(SimpleNamespace(), session))), \
             patch.object(sessions, "create_join_token") as token, \
             patch.object(sessions, "identity_row", new=AsyncMock()) as identity:
            result = await sessions.candidate_status("fixture")
            self.assertFalse(result["can_join"])
            self.assertEqual(result["status"], "scheduled")
            with self.assertRaises(HTTPException) as denied:
                await sessions.candidate_join("fixture")
            self.assertEqual(denied.exception.status_code, 409)
            token.assert_not_called()
            identity.assert_not_awaited()


@unittest.skipUnless(os.environ.get("RUN_INVITATION_DB_TESTS") == "1", "Opt in to disposable-schema invitation acceptance")
class ScheduledInvitationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_confirmation_calendar_send_and_reschedule_are_bound_together(self):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from sqlalchemy.schema import CreateSchema, DropSchema
        from app.core.config import get_settings
        from app.db.base import Base
        from app.models.user import User
        from app.models.jd import JDConversation, JobDescription
        from app.models.cv import Candidate, CandidateApplication, CVSource
        from app.models.cv_screening import ScreeningRun, CVAssessment
        from app.models.candidate_identity import CandidateIdentity
        from app.models.interviews import InterviewShortlist, InterviewShortlistMember, InterviewInvitation, InvitationBatch, InvitationEmailJob, InterviewSession
        from app.services.interviews import delivery, invitations, sessions
        from app.scripts import invitation_worker as worker
        from app.integrations.gmail import EmailSendResult

        settings = get_settings().model_copy(update={"smtp_username": "sender@example.com", "smtp_password": SecretStr("fixture"),
            "public_app_url": "https://example.com", "jwt_secret_key": SecretStr("fixture-encryption-secret-for-tests")})
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        schema = "invitation_schedule_" + uuid4().hex
        scoped = engine.execution_options(schema_translate_map={None: schema})
        factory = async_sessionmaker(scoped, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        try:
            models = (User, JDConversation, JobDescription, Candidate, CandidateApplication, CVSource, ScreeningRun, CVAssessment,
                InterviewShortlist, InterviewShortlistMember, InterviewInvitation, InvitationBatch, InvitationEmailJob, InterviewSession, CandidateIdentity)
            async with scoped.begin() as connection:
                await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[model.__table__ for model in models]))
            async with factory() as db:
                user = User(email="recruiter@example.com", password_hash="unused", role="recruiter")
                db.add(user); await db.flush()
                jd = JDConversation(recruiter_id=user.id)
                db.add(jd); await db.flush()
                job = JobDescription(conversation_id=jd.id, title="Python developer", content="Fixture", requirements="Fixture")
                db.add(job); await db.flush()
                source = CVSource(job_description_id=job.id, drive_folder_id="fixture-folder")
                candidate = Candidate(recruiter_id=user.id, full_name="Ada", email="candidate@example.com")
                db.add_all([source, candidate]); await db.flush()
                application = CandidateApplication(job_description_id=job.id, candidate_id=candidate.id, source_id=source.id, source_row_number=2)
                run = ScreeningRun(job_id=job.id, recruiter_id=user.id, input_key="fixture", inputs={}, status="completed")
                db.add_all([application, run]); await db.flush()
                score = {"aggregate": "90"}
                assessment = CVAssessment(run_id=run.id, application_id=application.id, file_version_ids=[], status="completed", score=score)
                shortlist = InterviewShortlist(job_description_id=job.id, recruiter_id=user.id, screening_run_id=run.id, filter_inputs={}, proposed_application_ids=[])
                db.add_all([assessment, shortlist]); await db.flush()
                db.add(InterviewShortlistMember(shortlist_id=shortlist.id, application_id=application.id, assessment_id=assessment.id, score_snapshot=score, rank_snapshot=1))
                await db.commit()
                owner, job_id, app_id = user.id, job.id, application.id
                results = {"run": {"id": str(run.id), "status": "completed"}, "warnings": [], "ranked": [{"application_id": str(app_id), "id": str(assessment.id), "score": score, "rank": 1}]}
            # Invitations can be sent for a window that has already opened.
            start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
            schedule = InvitationSchedule(application_id=app_id, starts_at=start, ends_at=start + timedelta(minutes=30), display_timezone="Asia/Dhaka")
            with ExitStack() as stack:
                for module in (delivery, worker, invitations, sessions):
                    stack.enter_context(patch.object(module, "SessionFactory", factory))
                for module in (delivery, worker, invitations):
                    stack.enter_context(patch.object(module, "get_settings", return_value=settings))
                stack.enter_context(patch.object(delivery, "signing_secret", return_value="fixture-secret"))
                stack.enter_context(patch.object(delivery, "get_screening_results", new=AsyncMock(return_value=results)))
                stack.enter_context(patch.object(delivery, "available_processing_versions", new=AsyncMock(return_value={"synthetic": "v1"})))
                sender = stack.enter_context(patch.object(worker, "send_email_job", new=AsyncMock(return_value=EmailSendResult(status="provider_accepted"))))
                with self.assertRaises(HTTPException):
                    await delivery.prepare_invitations(job_id, owner)
                preview = await delivery.prepare_invitations(job_id, owner, schedules=[schedule])
                self.assertEqual(preview["recipients"][0]["schedule"], schedule.public())
                confirmed = await delivery.confirm_invitations(job_id, owner, UUID(preview["batch_id"]), preview["preview_key"])
                self.assertEqual(confirmed["queued"], 1)
                duplicate = await delivery.confirm_invitations(job_id, owner, UUID(preview["batch_id"]), preview["preview_key"])
                self.assertTrue(duplicate["already_confirmed"])
                async with factory() as db:
                    invitation = await db.scalar(select(InterviewInvitation))
                    old_hash, invitation_id = invitation.token_hash, invitation.id
                    self.assertEqual(invitation.expires_at, schedule.ends_at)
                    email = await db.scalar(select(InvitationEmailJob))
                    payload = delivery.decrypt_message(email.encrypted_message)
                    self.assertNotIn(private_interview_link(payload), str(preview))
                    self.assertEqual(payload["calendar_event"]["uid"], str(invitation_id))
                self.assertTrue(await worker.send_one())
                self.assertEqual(sender.await_count, 1)
                self.assertEqual(sender.call_args.args[0].calendar_event.ends_at, schedule.ends_at)
                async with factory() as db:
                    email = await db.scalar(select(InvitationEmailJob))
                    email.status = "failed"
                    encrypted = email.encrypted_message
                    await db.commit()
                retry = await delivery.prepare_invitations(job_id, owner, "retry_failed", schedules=[schedule])
                await delivery.confirm_invitations(job_id, owner, UUID(retry["batch_id"]), retry["preview_key"])
                async with factory() as db:
                    email = await db.scalar(select(InvitationEmailJob))
                    self.assertEqual(email.encrypted_message, encrypted)
                self.assertTrue(await worker.send_one())
                schedule.starts_at += timedelta(days=1)
                schedule.ends_at += timedelta(days=1)
                updated = await delivery.prepare_invitations(job_id, owner, "reissue", schedules=[schedule])
                await delivery.confirm_invitations(job_id, owner, UUID(updated["batch_id"]), updated["preview_key"])
                async with factory() as db:
                    invitation = await db.get(InterviewInvitation, invitation_id)
                    self.assertNotEqual(invitation.token_hash, old_hash)
                    self.assertEqual(invitation.expires_at, schedule.ends_at)
                    session = await db.scalar(select(InterviewSession))
                    self.assertEqual(session.starts_at, schedule.starts_at)
                stale = await delivery.prepare_invitations(job_id, owner, "reissue", schedules=[schedule])
                async with factory() as db:
                    session = await db.scalar(select(InterviewSession))
                    session.version += 1
                    await db.commit()
                with self.assertRaises(HTTPException) as conflict:
                    await delivery.confirm_invitations(job_id, owner, UUID(stale["batch_id"]), stale["preview_key"])
                self.assertEqual(conflict.exception.status_code, 409)
        finally:
            async with engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
