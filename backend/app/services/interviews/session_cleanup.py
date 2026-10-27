"""Reconcile persisted interviews with actual LiveKit presence, including bots."""

import asyncio
import logging
from time import monotonic

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.interviews import InterviewSession
from app.services.interviews.livekit import participant_identities
from app.services.interviews.sessions import end_interview

logger = logging.getLogger("distill_ai.interview_cleanup")


async def reconcile_sessions(empty_since):
    async with SessionFactory() as db:
        sessions = list(await db.scalars(select(InterviewSession).where(
            InterviewSession.status.in_(["in_progress", "closing"]))))
    active_ids = {session.id for session in sessions}
    for session_id in list(empty_since):
        if session_id not in active_ids:
            empty_since.pop(session_id, None)

    for session in sessions:
        try:
            if session.status == "closing":
                await end_interview(session.job_description_id, session.application_id, session.recruiter_id)
                empty_since.pop(session.id, None)
                continue
            identities = await participant_identities(session.room_name)
            people = {f"recruiter-{session.recruiter_id}", f"candidate-{session.application_id}"}
            if identities & people:
                empty_since.pop(session.id, None)
                # _run rechecks the current session and invitation before joining.
                if session.candidate_admitted_at is not None:
                    from app.services.interviews.live_transcription import start_transcription
                    start_transcription(session.id)
                continue
            now = monotonic()
            since = empty_since.setdefault(session.id, now)
            if now - since >= get_settings().interview_empty_grace_seconds:
                await end_interview(session.job_description_id, session.application_id, session.recruiter_id)
                empty_since.pop(session.id, None)
        except Exception:
            # Failed presence checks are unknown, never evidence of absence.
            empty_since.pop(session.id, None)
            logger.warning("Interview reconciliation failed; session_id=%s", session.id)


async def cleanup_interview_sessions():
    """Run throughout API lifespan; persisted closing states survive restarts."""
    empty_since = {}
    while True:
        try:
            await reconcile_sessions(empty_since)
        except Exception:
            empty_since.clear()
            logger.warning("Interview reconciliation unavailable")
        await asyncio.sleep(15)
