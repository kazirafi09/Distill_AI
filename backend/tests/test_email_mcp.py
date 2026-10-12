"""Focused email MCP tests; SMTP delivery is always mocked."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
from pydantic import SecretStr
from starlette.responses import JSONResponse

from app import mcp_email
from app.integrations import email_mcp
from app.integrations.gmail import EmailSendResult


class EmailMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = "synthetic-email-mcp-secret-32-characters"
        self.settings = SimpleNamespace(
            email_mcp_secret=SecretStr(self.secret),
            email_mcp_url="http://127.0.0.1:8003/mcp/",
        )
        self.job = email_mcp.EmailJob(
            job_id=uuid4(), recipient="candidate@example.com",
            subject="Interview invitation", body="Synthetic individual interview link",
        )
        config = patch.object(email_mcp, "get_settings", return_value=self.settings)
        config.start()
        self.addCleanup(config.stop)

    def test_token_binds_job_and_content(self):
        token = email_mcp.create_job_token(self.job)
        claims = jwt.decode(token, self.secret, algorithms=["HS256"], audience="email-mcp")
        self.assertEqual(claims["sub"], str(self.job.job_id))
        self.assertEqual(claims["message_hash"], email_mcp.message_hash(self.job))
        self.assertEqual(claims["exp"] - claims["iat"], 120)
        self.assertNotIn(self.job.body, token)

    async def test_missing_secret_fails_before_connection(self):
        for secret in (None, SecretStr("short"), SecretStr("")):
            self.settings.email_mcp_secret = secret
            with patch.object(email_mcp.httpx, "AsyncClient") as factory:
                result = await email_mcp.send_email_job(self.job)
            self.assertEqual(result.error_code, "EMAIL_MCP_NOT_CONFIGURED")
            factory.assert_not_called()

    async def test_auth_rejects_missing_invalid_expired_and_wrong_audience(self):
        called = AsyncMock()
        app = mcp_email.EmailAuth(called)
        now = datetime.now(timezone.utc)
        expired = jwt.encode({"sub": str(self.job.job_id), "aud": "email-mcp",
            "iat": now - timedelta(minutes=5), "exp": now - timedelta(minutes=1),
            "message_hash": email_mcp.message_hash(self.job)}, self.secret, algorithm="HS256")
        ordinary = jwt.encode({"sub": str(self.job.job_id), "aud": "recruiter",
            "iat": now, "exp": now + timedelta(minutes=1),
            "message_hash": email_mcp.message_hash(self.job)}, self.secret, algorithm="HS256")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url=self.settings.email_mcp_url) as client:
            for token in ("", "invalid", expired, ordinary):
                response = await client.post("", headers={"Authorization": f"Bearer {token}"}, json={})
                self.assertEqual(response.status_code, 401)
        called.assert_not_awaited()

    async def test_valid_auth_context_is_reset_after_request(self):
        async def endpoint(scope, receive, send):
            claims = mcp_email.authorized_job.get()
            await JSONResponse({"job_id": claims["sub"]})(scope, receive, send)

        app = mcp_email.EmailAuth(endpoint)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url=self.settings.email_mcp_url) as client:
            response = await client.post("", headers={
                "Authorization": f"Bearer {email_mcp.create_job_token(self.job)}"}, json={})
        self.assertEqual(response.json()["job_id"], str(self.job.job_id))
        with self.assertRaises(LookupError):
            mcp_email.authorized_job.get()

    async def test_tool_rejects_changed_recipient_body_or_job(self):
        claims = {"sub": str(self.job.job_id), "message_hash": email_mcp.message_hash(self.job)}
        context = mcp_email.authorized_job.set(claims)
        try:
            with patch.object(mcp_email, "deliver", new_callable=AsyncMock) as delivery:
                for update in ({"recipient": "other@example.com"}, {"body": "changed"},
                               {"subject": "changed"}, {"job_id": uuid4()}):
                    changed = email_mcp.EmailJob(**{**self.job.model_dump(), **update})
                    with self.assertRaises(ValueError):
                        await mcp_email.send_email(changed)
                delivery.assert_not_awaited()
        finally:
            mcp_email.authorized_job.reset(context)

    async def test_real_mcp_client_server_round_trip_with_fake_delivery(self):
        client_factory = httpx.AsyncClient

        def local_client(**kwargs):
            return client_factory(transport=httpx.ASGITransport(app=mcp_email.app), **kwargs)

        async with mcp_email.mcp.session_manager.run():
            with patch.object(email_mcp.httpx, "AsyncClient", side_effect=local_client), \
                 patch.object(mcp_email, "deliver", new_callable=AsyncMock) as delivery:
                for status in ("provider_accepted", "failed", "uncertain"):
                    delivery.return_value = EmailSendResult(status=status)
                    result = await email_mcp.send_email_job(self.job)
                    self.assertEqual(result.status, status)
                    self.assertIsNone(result.error_code)
                self.assertEqual(delivery.await_count, 3)
                self.assertEqual(delivery.await_args.args[0], self.job)

    async def test_connection_failure_before_call_is_failed(self):
        with patch.object(email_mcp, "streamable_http_client", side_effect=OSError("synthetic")):
            result = await email_mcp.send_email_job(self.job)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "EMAIL_MCP_CONNECTION_ERROR")

    async def test_lost_tool_response_is_uncertain_without_retry(self):
        session = AsyncMock()
        session.call_tool.side_effect = OSError("synthetic lost response")

        class FakeTransport:
            async def __aenter__(self):
                return None, None, None

            async def __aexit__(self, *args):
                return False

        with patch.object(email_mcp, "streamable_http_client", return_value=FakeTransport()), \
             patch.object(email_mcp, "ClientSession", return_value=session):
            session.__aenter__.return_value = session
            result = await email_mcp.send_email_job(self.job)
        self.assertEqual(result.status, "uncertain")
        session.call_tool.assert_awaited_once()
