"""Room cleanup regression checks without a database or LiveKit connection."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from livekit import api

from app.services.interviews import session_cleanup as cleanup
from app.services.interviews import livekit, live_transcription


class InterviewCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = SimpleNamespace(
            id=uuid4(), job_description_id=uuid4(), application_id=uuid4(),
            recruiter_id=uuid4(), room_name="synthetic-room", status="in_progress",
            candidate_admitted_at=object(),
        )
        self.rows = [self.session]
        db = AsyncMock()
        db.scalars.side_effect = lambda *_: self.rows
        factory = MagicMock()
        factory.return_value.__aenter__.return_value = db
        patches = [
            patch.object(cleanup, "SessionFactory", factory),
            patch.object(cleanup, "participant_identities", new_callable=AsyncMock),
            patch.object(cleanup, "end_interview", new_callable=AsyncMock),
            patch.object(cleanup, "get_settings", return_value=SimpleNamespace(interview_empty_grace_seconds=60)),
            patch.object(cleanup, "monotonic", return_value=100),
            patch.object(live_transcription, "start_transcription"),
        ]
        _, self.presence, self.end, _, self.now, self.start = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.presence.return_value = set()
        self.empty = {}

    async def tick(self, seconds):
        self.now.return_value = seconds
        await cleanup.reconcile_sessions(self.empty)

    async def test_bot_only_room_ends_after_grace_and_does_not_resume_bot(self):
        self.presence.return_value = {"transcription-synthetic"}
        await self.tick(100)
        await self.tick(159)
        self.end.assert_not_awaited()
        await self.tick(160)
        self.end.assert_awaited_once_with(
            self.session.job_description_id, self.session.application_id, self.session.recruiter_id)
        self.start.assert_not_called()
        self.assertNotIn(self.session.id, self.empty)

    async def test_either_person_rejoining_resets_grace_and_resumes_capture(self):
        for identity in (f"recruiter-{self.session.recruiter_id}", f"candidate-{self.session.application_id}"):
            self.empty[self.session.id] = 0
            self.presence.return_value = {identity}
            await self.tick(100)
            self.assertNotIn(self.session.id, self.empty)
        self.end.assert_not_awaited()
        self.assertEqual(self.start.call_count, 2)

    async def test_new_empty_period_gets_full_grace_after_rejoin(self):
        await self.tick(100)
        self.presence.return_value = {f"recruiter-{self.session.recruiter_id}"}
        await self.tick(150)
        self.presence.return_value = set()
        await self.tick(160)
        await self.tick(200)
        self.end.assert_not_awaited()
        await self.tick(220)
        self.end.assert_awaited_once()

    async def test_unknown_presence_never_ends_call_and_resets_grace(self):
        await self.tick(100)
        self.presence.side_effect = TimeoutError()
        await self.tick(200)
        self.end.assert_not_awaited()
        self.assertEqual(self.empty, {})
        self.presence.side_effect = None
        await self.tick(300)
        self.end.assert_not_awaited()

    async def test_persisted_closing_is_retried_even_with_people_present(self):
        self.session.status = "closing"
        self.end.side_effect = [TimeoutError(), None]
        await self.tick(100)
        await self.tick(115)
        self.assertEqual(self.end.await_count, 2)
        self.presence.assert_not_awaited()

    async def test_empty_startup_does_not_restart_transcription(self):
        await self.tick(100)
        self.start.assert_not_called()
        self.end.assert_not_awaited()

    async def test_completed_sessions_are_removed_from_timer_state(self):
        self.empty[self.session.id] = 0
        self.rows.clear()
        await self.tick(100)
        self.assertEqual(self.empty, {})


class ParticipantPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_room_is_empty_but_service_errors_propagate(self):
        client = AsyncMock()
        settings = SimpleNamespace(livekit_api_url="http://synthetic", livekit_api_timeout_seconds=1)
        with patch.object(livekit, "get_settings", return_value=settings), \
                patch.object(livekit, "livekit_credentials", return_value=("key", "secret")), \
                patch.object(livekit.api, "LiveKitAPI") as factory:
            factory.return_value.__aenter__.return_value = client
            client.room.list_participants.return_value = SimpleNamespace(
                participants=[SimpleNamespace(identity="recruiter-synthetic"), SimpleNamespace(identity="transcription-synthetic")])
            self.assertEqual(await livekit.participant_identities("synthetic-room"),
                             {"recruiter-synthetic", "transcription-synthetic"})
            client.room.list_participants.side_effect = api.TwirpError("not_found", "missing", status=404)
            self.assertEqual(await livekit.participant_identities("synthetic-room"), set())
            client.room.list_participants.side_effect = api.TwirpError("unavailable", "unavailable", status=503)
            with self.assertRaises(api.TwirpError):
                await livekit.participant_identities("synthetic-room")


if __name__ == "__main__":
    unittest.main()
