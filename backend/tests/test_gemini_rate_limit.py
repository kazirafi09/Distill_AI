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


if __name__ == "__main__":
    unittest.main()
