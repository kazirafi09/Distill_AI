"""Completed interviews stay in history but leave invitation scheduling."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import SecretStr

from app.services.interviews import delivery


class CompletedInvitationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.job, self.owner, self.done, self.pending = [uuid4() for _ in range(4)]
        self.db = AsyncMock()
        self.db.add = MagicMock()
        self.db.get.return_value = None
        self.db.execute.return_value = MagicMock()
        factory = MagicMock()
        factory.return_value.__aenter__.return_value = self.db
        self.sessions = {
            self.done: SimpleNamespace(status="completed", starts_at=None, ends_at=None),
            self.pending: None,
        }
        patches = [
            patch.object(delivery, "SessionFactory", factory),
            patch.object(delivery, "owned_job", new_callable=AsyncMock),
            patch.object(delivery, "lock_screening_job", new_callable=AsyncMock),
            patch.object(delivery, "application_session", new_callable=AsyncMock),
        ]
        _, self.owned, _, self.session = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.session.side_effect = lambda db, identifier: self.sessions[identifier]

    def schedule(self, identifier):
        return {"application_id": identifier, "starts_at": "2099-01-01T10:00:00Z",
                "ends_at": "2099-01-01T11:00:00Z", "display_timezone": "Asia/Dhaka"}

    async def test_options_exclude_completed_and_keep_pending_candidates(self):
        self.db.execute.return_value.all.return_value = [
            (self.done, "Completed", "done@example.com"),
            (self.pending, "Pending", "pending@example.com"),
        ]
        result = await delivery.invitation_options(self.job, self.owner)
        self.assertEqual([r["application_id"] for r in result["recipients"]], [str(self.pending)])
        self.db.get.assert_awaited_once_with(delivery.InvitationScheduleDraft, self.pending)

    async def test_all_completed_returns_empty_picker(self):
        self.db.execute.return_value.all.return_value = [(self.done, "Completed", "done@example.com")]
        self.assertEqual((await delivery.invitation_options(self.job, self.owner))["recipients"], [])

    async def test_saving_pending_times_does_not_require_completed_candidates(self):
        self.db.scalars.side_effect = [[self.done, self.pending], [self.done]]
        result = await delivery.save_invitation_schedules(self.job, self.owner, [self.schedule(self.pending)])
        self.assertEqual(result["saved"], 1)
        self.db.commit.assert_awaited_once()
        self.assertEqual(self.db.add.call_args.args[0].application_id, self.pending)

    async def test_stale_picker_cannot_save_a_now_completed_candidate(self):
        self.db.scalars.side_effect = [[self.done, self.pending], [self.done]]
        with self.assertRaises(HTTPException) as caught:
            await delivery.save_invitation_schedules(
                self.job, self.owner, [self.schedule(self.done), self.schedule(self.pending)])
        self.assertEqual(caught.exception.status_code, 409)
        self.db.commit.assert_not_awaited()

    async def test_completed_candidates_do_not_block_reissue_preview(self):
        self.owned.return_value = SimpleNamespace(title="Developer", number=1)
        self.db.scalar.return_value = SimpleNamespace(id=uuid4())
        self.db.execute.return_value.all.return_value = [
            (SimpleNamespace(application_id=self.done), SimpleNamespace(full_name="Completed"))]
        settings = SimpleNamespace(smtp_username="sender@example.com", smtp_password=SecretStr("synthetic"),
                                   public_app_url="https://example.com", interview_link_days=7)
        with patch.object(delivery, "get_screening_results", new_callable=AsyncMock) as screening, \
                patch.object(delivery, "get_settings", return_value=settings), \
                patch.object(delivery, "signing_secret", return_value="synthetic"):
            screening.return_value = {"run": {"id": "synthetic", "status": "completed"}, "warnings": [], "ranked": []}
            result = await delivery.preview_data(self.db, self.job, self.owner, "reissue")
        self.assertEqual(result["recipients"], [])


if __name__ == "__main__":
    unittest.main()
