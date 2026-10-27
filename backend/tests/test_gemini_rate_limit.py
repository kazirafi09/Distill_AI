"""Shared Gemini pacing: at most twelve permits/minute, including retries."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations import gemini_rate_limit as limiter
from app.integrations.cv_model import gemini_request


class GeminiRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_even_legacy_higher_config_is_capped_at_twelve(self):
        with patch.object(limiter, "get_settings", return_value=SimpleNamespace(gemini_requests_per_minute=14)), \
             patch.object(limiter, "_slot", new_callable=AsyncMock, return_value=0) as slot:
            await limiter.acquire_gemini_slot()
        interval = slot.await_args.args[0]
        self.assertEqual(interval, 5.1)
        self.assertGreater(12 * interval, 60)  # Thirteenth permit cannot fit in the same rolling minute.

    async def test_configured_lower_rate_is_respected(self):
        with patch.object(limiter, "get_settings", return_value=SimpleNamespace(gemini_requests_per_minute=6)), \
             patch.object(limiter, "_slot", new_callable=AsyncMock, return_value=0) as slot:
            await limiter.acquire_gemini_slot()
        self.assertEqual(slot.await_args.args[0], 10.1)

    async def test_waiting_branch_rechecks_shared_slot_after_sleep(self):
        with patch.object(limiter, "_slot", new_callable=AsyncMock, side_effect=[7, 0]) as slot, \
             patch.object(limiter.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await limiter.acquire_gemini_slot()
        self.assertEqual(slot.await_count, 2)
        sleep.assert_awaited_once_with(7)

    async def test_get_and_every_retry_acquire_a_shared_permit(self):
        responses = iter([200, 429, 200])
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(next(responses), json={})))
        async with client:
            with patch("app.integrations.cv_model.acquire_gemini_slot", new_callable=AsyncMock) as acquire, \
                 patch("app.integrations.cv_model.pause_gemini_requests", new_callable=AsyncMock), \
                 patch("app.integrations.cv_model.headers", return_value={"x-goog-api-key": "test-only"}):
                await gemini_request(client, "GET", "https://example.test/model")
                await gemini_request(client, "POST", "https://example.test/generate", json={})
        self.assertEqual(acquire.await_count, 3)

    def test_settings_reject_more_than_twelve(self):
        with self.assertRaises(ValidationError):
            Settings(postgres_user="test", postgres_password="test", postgres_db="test",
                     jwt_secret_key="x" * 32, gemini_requests_per_minute=13, _env_file=None)

    async def test_temporary_server_failure_recovers_with_pacing_and_same_payload(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                client = SimpleNamespace(request=AsyncMock(side_effect=[
                    httpx.Response(status, headers={'Retry-After': '7'}), httpx.Response(200, json={})]))
                settings = SimpleNamespace(gemini_max_retries=3, cv_model_timeout_seconds=300)
                with patch('app.integrations.cv_model.get_settings', return_value=settings), \
                     patch('app.integrations.cv_model.acquire_gemini_slot', new_callable=AsyncMock) as acquire, \
                     patch('app.integrations.cv_model.pause_gemini_requests', new_callable=AsyncMock) as pause, \
                     patch('app.integrations.cv_model.headers', return_value={}):
                    result = await gemini_request(client, 'POST', 'https://example.test/generate', json={'synthetic': True})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(acquire.await_count, 2)
                self.assertEqual(pause.await_count, 1)
                self.assertGreaterEqual(pause.await_args.args[0], 7)
                self.assertEqual(client.request.await_args_list[0], client.request.await_args_list[1])

    async def test_server_failure_stops_at_retry_budget_and_keeps_safe_error(self):
        client = SimpleNamespace(request=AsyncMock(return_value=httpx.Response(503, text='private provider response')))
        settings = SimpleNamespace(gemini_max_retries=2, cv_model_timeout_seconds=300)
        with patch('app.integrations.cv_model.get_settings', return_value=settings), \
             patch('app.integrations.cv_model.acquire_gemini_slot', new_callable=AsyncMock) as acquire, \
             patch('app.integrations.cv_model.pause_gemini_requests', new_callable=AsyncMock), \
             patch('app.integrations.cv_model.headers', return_value={}):
            with self.assertRaisesRegex(ValueError, '^MODEL_UNAVAILABLE$'):
                await gemini_request(client, 'POST', 'https://example.test/generate')
        self.assertEqual(client.request.await_count, 3)
        self.assertEqual(acquire.await_count, 3)

    async def test_permanent_errors_are_not_retried(self):
        for status, code in [(400, 'MODEL_REQUEST_INVALID'), (401, 'MODEL_AUTH_FAILED'),
                             (403, 'MODEL_AUTH_FAILED'), (404, 'MODEL_NOT_AVAILABLE')]:
            with self.subTest(status=status):
                client = SimpleNamespace(request=AsyncMock(return_value=httpx.Response(status)))
                settings = SimpleNamespace(gemini_max_retries=3, cv_model_timeout_seconds=300)
                with patch('app.integrations.cv_model.get_settings', return_value=settings), \
                     patch('app.integrations.cv_model.acquire_gemini_slot', new_callable=AsyncMock), \
                     patch('app.integrations.cv_model.pause_gemini_requests', new_callable=AsyncMock) as pause, \
                     patch('app.integrations.cv_model.headers', return_value={}):
                    with self.assertRaisesRegex(ValueError, '^' + code + '$'):
                        await gemini_request(client, 'GET', 'https://example.test/model')
                self.assertEqual(client.request.await_count, 1)
                pause.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
