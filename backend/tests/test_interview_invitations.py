"""Focused link lifecycle tests; no email, live models or database migration."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.interviews import InterviewInvitation
from app.services.interviews import invitations


class InvitationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = SimpleNamespace(public_app_url="http://localhost:5173", interview_link_days=7)
        config = patch.object(invitations, "get_settings", return_value=self.settings)
        config.start()
        self.addCleanup(config.stop)
        self.job_id, self.owner, self.application_id = uuid4(), uuid4(), uuid4()

    def test_links_are_unique_and_expire_in_seven_days(self):
        first, link, expiry = invitations.new_link()
        second, _, _ = invitations.new_link()
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 43)
        self.assertEqual(link, f"{self.settings.public_app_url}/interview#{first}")
        self.assertAlmostEqual((expiry - datetime.now(timezone.utc)).total_seconds(), 7 * 86400, delta=2)
        self.assertEqual(len(invitations.token_hash(first)), 64)

    async def create(self, existing=None, candidate=True, replace=False, recipient="candidate@example.com"):
        db = AsyncMock()
        db.add = MagicMock()
        db.scalar.side_effect = [SimpleNamespace(email="candidate@example.com") if candidate else None, existing, None, None]
        with patch.object(invitations, "lock_screening_job", new_callable=AsyncMock), \
             patch.object(invitations, "owned_job", new_callable=AsyncMock):
            return await invitations.create_invitation(db, self.job_id, self.owner,
                self.application_id, recipient, replace=replace)

    async def test_creation_stores_hash_and_not_raw_token(self):
        record, link = await self.create()
        token = link.split("#")[1]
        self.assertEqual(record.token_hash, invitations.token_hash(token))
        self.assertEqual(record.application_id, self.application_id)
        self.assertNotIn(token, repr(vars(record)))

    async def test_non_shortlisted_application_and_changed_email_are_rejected(self):
        for arguments, status in (({"candidate": False}, 422), ({"recipient": "changed@example.com"}, 409)):
            with self.assertRaises(HTTPException) as denied:
                await self.create(**arguments)
            self.assertEqual(denied.exception.status_code, status)

    async def test_existing_link_requires_explicit_replacement(self):
        record = InterviewInvitation(token_hash="old", revoked_at=datetime.now(timezone.utc))
        with self.assertRaises(HTTPException) as denied:
            await self.create(existing=record)
        self.assertEqual(denied.exception.status_code, 409)
        replaced, link = await self.create(existing=record, replace=True)
        self.assertIs(replaced, record)
        self.assertNotEqual(replaced.token_hash, "old")
        self.assertEqual(replaced.token_hash, invitations.token_hash(link.split("#")[1]))
        self.assertIsNone(replaced.revoked_at)

    async def test_valid_expired_revoked_and_unknown_entry(self):
        token, _, expiry = invitations.new_link()
        record = InterviewInvitation(token_hash=invitations.token_hash(token), expires_at=expiry)
        db = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = db
        with patch.object(invitations, "SessionFactory", return_value=context):
            db.scalar.side_effect = [record, "Synthetic role", "Synthetic candidate"]
            entry = await invitations.validate_entry(token)
            self.assertEqual(entry["job_title"], "Synthetic role")
            self.assertFalse(entry["interview_ready"])
            self.assertNotIn("recipient_email", entry)
            db.scalar.side_effect = None
            db.scalar.return_value = record
            record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            with self.assertRaises(HTTPException) as denied:
                await invitations.validate_entry(token)
            self.assertEqual(denied.exception.status_code, 410)
            record.expires_at, record.revoked_at = expiry, datetime.now(timezone.utc)
            with self.assertRaises(HTTPException):
                await invitations.validate_entry(token)
            db.scalar.return_value = None
            with self.assertRaises(HTTPException):
                await invitations.validate_entry(token)

    async def test_malformed_token_never_queries_database(self):
        with patch.object(invitations, "SessionFactory") as factory:
            with self.assertRaises(HTTPException) as denied:
                await invitations.validate_entry("bad token")
            self.assertEqual(denied.exception.status_code, 410)
            factory.assert_not_called()

    def test_public_url_rejects_embedded_credentials(self):
        self.settings.public_app_url = "http://user:password@localhost:5173"
        with self.assertRaises(ValueError):
            invitations.new_link()
