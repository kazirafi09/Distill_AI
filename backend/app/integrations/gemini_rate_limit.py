"""Database-wide pacing. Never hold a transaction while waiting or calling Google."""
import asyncio
import math
import random
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.model_rate_limit import ModelRateLimit

# One conservative allowance for all Gemini traffic in this deployment.
SCOPE = "gemini"
MAX_REQUESTS_PER_MINUTE = 12


async def _slot(interval, cooldown=0, scope=SCOPE):
    async with SessionFactory() as db:
        async with db.begin():
            await db.execute(insert(ModelRateLimit).values(
                scope=scope, next_request_at=func.clock_timestamp()
            ).on_conflict_do_nothing(index_elements=["scope"]))
            row = await db.scalar(select(ModelRateLimit).where(
                ModelRateLimit.scope == scope).with_for_update())
            # Read database time AFTER obtaining the row lock.
            now = await db.scalar(select(func.clock_timestamp()))
            if cooldown:
                row.next_request_at = max(row.next_request_at, now + timedelta(seconds=cooldown))
                return 0
            delay = (row.next_request_at - now).total_seconds()
            if delay <= 0:
                row.next_request_at = now + timedelta(seconds=interval)
                return 0
            return delay


async def acquire_gemini_slot():
    # Shared DB row serializes permits across API processes, workers and F5
    # branches. No burst allowance; even a legacy setting above 12 is capped.
    limit = min(get_settings().gemini_requests_per_minute, MAX_REQUESTS_PER_MINUTE)
    interval = 60 / limit + 0.1
    while True:
        delay = await _slot(interval)
        if delay <= 0:
            return
        # No future reservations: cancellation cannot leave a queue of unused slots.
        await asyncio.sleep(delay)


def retry_delay(response, attempt):
    """Honor Retry-After and Google's RetryInfo; fall back to bounded backoff."""
    delays = [min(60, 5 * 2 ** attempt) + random.uniform(0, 1)]
    value = response.headers.get("Retry-After", "")
    try:
        delays.append(float(value))
    except ValueError:
        try:
            delays.append((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    try:
        for detail in response.json().get("error", {}).get("details", []):
            if detail.get("@type", "").endswith("google.rpc.RetryInfo"):
                delays.append(float(detail["retryDelay"].removesuffix("s")))
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return max(delay for delay in delays if math.isfinite(delay))


async def pause_gemini_requests(delay):
    await _slot(0, cooldown=delay)
