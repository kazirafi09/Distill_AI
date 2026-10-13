"""Signed token and configuration checks; no live room is required."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from pydantic import SecretStr

from app.services.interviews.livekit import create_join_token, livekit_credentials


class LiveKitAdapterTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            livekit_api_key="test-key",
            livekit_api_secret=SecretStr("synthetic-test-secret-32-characters"),
            livekit_ws_url="ws://localhost:7880",
            livekit_token_ttl_seconds=300,
        )

    def test_signed_token_is_scoped_to_room_identity_and_media(self):
        before = int(time.time())
        with patch("app.services.interviews.livekit.get_settings", return_value=self.settings):
            result = create_join_token("interview-test-room", "candidate-test-id")
        claims = jwt.decode(
            result["participant_token"],
            self.settings.livekit_api_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer="test-key",
        )
        self.assertEqual(result["server_url"], "ws://localhost:7880")
        self.assertEqual(claims["sub"], "candidate-test-id")
        self.assertGreaterEqual(claims["exp"], before + 300)
        self.assertLessEqual(claims["exp"], int(time.time()) + 300)
        grants = claims["video"]
        self.assertEqual(grants["room"], "interview-test-room")
        self.assertTrue(grants["roomJoin"])
        self.assertTrue(grants["canPublish"])
        self.assertTrue(grants["canSubscribe"])
        self.assertEqual(grants["canPublishSources"], ["camera", "microphone"])
        self.assertFalse(grants["canPublishData"])
        self.assertFalse(grants["canUpdateOwnMetadata"])
        for permission in ("roomAdmin", "roomCreate", "roomList", "roomRecord", "ingressAdmin"):
            self.assertFalse(grants.get(permission, False))

    def test_missing_or_empty_credentials_are_rejected(self):
        for key, secret in ((None, None), (None, SecretStr("test")), ("test", None), ("test", SecretStr(""))):
            with self.subTest(key=key, secret_present=secret is not None):
                self.settings.livekit_api_key = key
                self.settings.livekit_api_secret = secret
                with self.assertRaises(HTTPException) as caught:
                    livekit_credentials(self.settings)
                self.assertEqual(caught.exception.status_code, 503)
                self.assertEqual(caught.exception.detail, "LiveKit is not configured.")


if __name__ == "__main__":
    unittest.main()
