"""Focused Gmail SMTP tests. Fake client only; no email sends."""
import smtplib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pydantic import SecretStr, ValidationError
from app.integrations import gmail


class GmailSMTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = SimpleNamespace(smtp_host="smtp.gmail.com", smtp_port=587,
            smtp_username="sender@example.com", smtp_password=SecretStr("fake-password"), smtp_timeout_seconds=20)
        self.message = gmail.EmailMessageRequest(recipient="candidate@example.com", subject="Interview invitation", body="Individual link: https://example.com/interview/test")
        self.client = Mock()
        self.client.send_message.return_value = {}
        self.events = []
        for name in ("connect", "starttls", "login"):
            getattr(self.client, name).side_effect = lambda *args, name=name, **kwargs: self.events.append(name)

    async def send(self):
        with patch.object(gmail, "get_settings", return_value=self.settings), patch.object(gmail.smtplib, "SMTP", return_value=self.client) as factory:
            result = await gmail.send_email(self.message)
        return result, factory

    async def test_acceptance_tls_order_and_single_recipient(self):
        result, factory = await self.send()
        self.assertEqual(result.status, "provider_accepted")
        self.assertIsNone(result.provider_message_id)
        self.assertEqual(self.events, ["connect", "starttls", "login"])
        factory.assert_called_once_with(timeout=20)
        self.client.connect.assert_called_once_with("smtp.gmail.com", 587)
        self.assertTrue(self.client.starttls.call_args.kwargs["context"].check_hostname)
        self.client.login.assert_called_once_with("sender@example.com", "fake-password")
        email = self.client.send_message.call_args.args[0]
        self.assertEqual(email["From"], "sender@example.com")
        self.assertEqual(email["To"], "candidate@example.com")
        self.assertEqual(email["Subject"], self.message.subject)
        self.assertEqual(email.get_content().strip(), self.message.body)
        self.assertIsNone(email["Bcc"])
        self.client.send_message.assert_called_once_with(email, from_addr="sender@example.com", to_addrs=["candidate@example.com"])
        self.client.close.assert_called_once()

    async def test_auth_failure_never_sends_or_exposes_details(self):
        self.client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private details")
        result, _ = await self.send()
        self.assertEqual(result.error_code, "SMTP_AUTHENTICATION_FAILED")
        self.assertNotIn("private", result.model_dump_json())
        self.client.send_message.assert_not_called()

    async def test_recipient_rejection(self):
        self.client.send_message.side_effect = smtplib.SMTPRecipientsRefused({"candidate@example.com": (550, b"rejected")})
        result, _ = await self.send()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "SMTP_RECIPIENT_REJECTED")

    async def test_negative_data_reply(self):
        self.client.send_message.side_effect = smtplib.SMTPDataError(554, b"rejected")
        result, _ = await self.send()
        self.assertEqual(result.error_code, "SMTP_REJECTED_554")
        self.assertEqual(result.status, "failed")

    async def test_connection_timeout_before_send_is_failed(self):
        self.client.connect.side_effect = TimeoutError("synthetic")
        result, _ = await self.send()
        self.assertEqual(result.status, "failed")
        self.client.send_message.assert_not_called()

    async def test_timeout_during_send_is_uncertain_without_retry(self):
        self.client.send_message.side_effect = TimeoutError("synthetic")
        result, _ = await self.send()
        self.assertEqual(result.status, "uncertain")
        self.client.send_message.assert_called_once()

    async def test_tls_failure_prevents_login(self):
        self.client.starttls.side_effect = smtplib.SMTPNotSupportedError("no TLS")
        result, _ = await self.send()
        self.assertEqual(result.status, "failed")
        self.client.login.assert_not_called()
        self.client.send_message.assert_not_called()

    async def test_cleanup_failure_preserves_acceptance(self):
        self.client.close.side_effect = OSError("synthetic")
        result, _ = await self.send()
        self.assertEqual(result.status, "provider_accepted")

    async def test_missing_settings_never_connect(self):
        for name, value in (("smtp_username", None), ("smtp_password", None), ("smtp_password", SecretStr(""))):
            original = getattr(self.settings, name)
            setattr(self.settings, name, value)
            result, factory = await self.send()
            self.assertEqual(result.error_code, "SMTP_NOT_CONFIGURED")
            factory.assert_not_called()
            setattr(self.settings, name, original)

    async def test_invalid_sender_never_connects(self):
        self.settings.smtp_username = "bad-email"
        result, factory = await self.send()
        self.assertEqual(result.error_code, "EMAIL_CONFIGURATION_INVALID")
        factory.assert_not_called()

    def test_invalid_headers_and_multiple_recipients_are_rejected(self):
        for name, value in (("subject", "Invite\r\nBcc: other@example.com"), ("subject", " "), ("recipient", "a@example.com,b@example.com")):
            with self.assertRaises(ValidationError):
                gmail.EmailMessageRequest(**{**self.message.model_dump(), name: value})
