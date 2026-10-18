"""API-process automatic transcription; durable restart recovery is separate."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from aiohttp import ClientResponseError
from livekit import api, rtc
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.interviews import InterviewSession
from app.models.interview_transcripts import InterviewTranscript
from app.services.interviews.livekit import livekit_credentials
from app.services.interviews.transcription import transcribe_microphone
from app.services.interviews.transcripts import begin_transcript, mark_speech_boundary, save_final_turn


logger = logging.getLogger("distill_ai.transcription")


@dataclass
class Capture:
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    streams: dict = field(default_factory=dict)
    flags: set = field(default_factory=set)
    error: str | None = None
    connected: bool = False


_captures: dict[UUID, Capture] = {}


def capture_status(session_id):
    capture = _captures.get(session_id)
    if capture is None:
        return {"status": "inactive", "roles": []}
    roles = sorted({s["role"] for s in capture.streams.values()
                    if s["ready"].is_set() and not s["task"].done()})
    status = "stopping" if capture.stop.is_set() else "listening" if roles else (
        "waiting_for_microphone" if capture.connected else "connecting")
    return {"status": status, "roles": roles}


def start_transcription(session_id):
    """Call only after the session service has authorized and committed admission."""
    current = _captures.get(session_id)
    if current is not None and current.task is not None and not current.task.done():
        return
    capture = Capture()
    _captures[session_id] = capture
    capture.task = asyncio.create_task(_run(session_id, capture))

    def finished(task):
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Automatic transcription cleanup failed; session_id=%s", session_id)
        if _captures.get(session_id) is capture:
            _captures.pop(session_id, None)

    capture.task.add_done_callback(finished)


async def stop_transcription(session_id):
    capture = _captures.get(session_id)
    if capture is None:
        return
    capture.stop.set()
    try:
        await asyncio.wait_for(asyncio.shield(capture.task), timeout=25)
    except TimeoutError:
        capture.task.cancel()
        await asyncio.gather(capture.task, return_exceptions=True)
    except Exception:
        # A capture failure must not prevent the recruiter from ending the call.
        logger.warning("Transcription stop failed; session_id=%s", session_id)


async def shutdown_transcriptions():
    await asyncio.gather(*(stop_transcription(sid) for sid in list(_captures)), return_exceptions=True)


def safe_error(error):
    if isinstance(error, ClientResponseError):
        return {401: "TRANSCRIPTION_AUTH_FAILED", 402: "TRANSCRIPTION_CREDITS_REQUIRED",
                403: "TRANSCRIPTION_ACCESS_DENIED", 429: "TRANSCRIPTION_RATE_LIMITED"}.get(
                    error.status, "TRANSCRIPTION_PROVIDER_FAILED")
    if isinstance(error, TimeoutError):
        return "TRANSCRIPTION_TIMEOUT"
    if isinstance(error, ValueError) and str(error) == "DEEPGRAM_API_KEY_MISSING":
        return "DEEPGRAM_API_KEY_MISSING"
    return "TRANSCRIPTION_FAILED"


async def _record_stop(transcript_id, capture):
    async with SessionFactory() as db:
        async with db.begin():
            transcript = await db.scalar(select(InterviewTranscript).where(
                InterviewTranscript.id == transcript_id).with_for_update())
            if transcript is not None and transcript.status == "capturing":
                # No finalizer has been approved yet: never publish F5-ready data
                # or leave a nonexistent finalization job permanently running.
                transcript.status = "needs_review"
                transcript.error_code = capture.error
                transcript.quality_flags = sorted(set(transcript.quality_flags) | capture.flags | {"FINALIZATION_PENDING"})


async def _run(session_id, capture):
    room = rtc.Room()
    transcript_id = None
    try:
        async with SessionFactory() as db:
            session = await db.get(InterviewSession, session_id)
            if session is None or session.status != "in_progress" or session.candidate_admitted_at is None:
                return
            # Copy immutable identifiers and close the read transaction before media work.
            job_id, application_id, owner_id = session.job_description_id, session.application_id, session.recruiter_id
            room_name = session.room_name
        info = await begin_transcript(job_id, application_id, owner_id)
        transcript_id = UUID(info["transcript_id"])
        if capture.stop.is_set():
            return
        settings = get_settings()
        if not settings.deepgram_api_key or not settings.deepgram_api_key.get_secret_value().strip():
            raise ValueError("DEEPGRAM_API_KEY_MISSING")
        key, secret = livekit_credentials(settings)
        expected = {f"recruiter-{owner_id}": "recruiter", f"candidate-{application_id}": "candidate"}

        def subscribe(publication, participant):
            if (not capture.stop.is_set() and participant.identity in expected
                    and publication.source == rtc.TrackSource.SOURCE_MICROPHONE):
                publication.set_subscribed(True)

        room.on("track_published", subscribe)

        @room.on("track_subscribed")
        def subscribed(track, publication, participant):
            role = expected.get(participant.identity)
            if (capture.stop.is_set() or role is None or publication.sid in capture.streams
                    or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
                    or track.kind != rtc.TrackKind.KIND_AUDIO):
                return
            if any(s["role"] == role and not s["stop"].is_set() and not s["task"].done()
                   for s in capture.streams.values()):
                capture.error = "DUPLICATE_ROLE_MICROPHONE"
                capture.stop.set()
                return
            stream_id, ready, stop = uuid4(), asyncio.Event(), asyncio.Event()
            identity = participant.identity

            async def emit(event):
                if event["role"] != role:
                    raise ValueError("TRANSCRIPT_SPEAKER_MISMATCH")
                if event.get("kind") == "speech_boundary":
                    await mark_speech_boundary(transcript_id, owner_id, stream_id, event["stream_end_ms"])
                elif event["is_final"]:
                    await save_final_turn(transcript_id, owner_id, stream_id, identity, event)

            task = asyncio.create_task(transcribe_microphone(
                track, role, emit, seconds=None, stop_event=stop, ready_event=ready))
            capture.streams[publication.sid] = {"role": role, "ready": ready, "stop": stop, "task": task}

            def done(finished):
                if not finished.cancelled() and finished.exception() is not None:
                    capture.error = safe_error(finished.exception())
                    capture.stop.set()

            task.add_done_callback(done)

        @room.on("track_unsubscribed")
        def unsubscribed(track, publication, participant):
            stream = capture.streams.get(publication.sid)
            if stream is not None:
                if not capture.stop.is_set():
                    capture.flags.add("MICROPHONE_TRACK_INTERRUPTED")
                stream["stop"].set()

        @room.on("disconnected")
        def disconnected(reason):
            if not capture.stop.is_set():
                capture.error = "TRANSCRIPTION_ROOM_DISCONNECTED"
            capture.stop.set()

        token = (api.AccessToken(key, secret).with_identity(f"transcription-{uuid4()}")
                 .with_ttl(timedelta(minutes=5)).with_grants(api.VideoGrants(
                     room_join=True, room=room_name, can_publish=False, can_publish_data=False,
                     can_subscribe=True, can_update_own_metadata=False)).to_jwt())
        address = urlsplit(settings.livekit_api_url)
        scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[address.scheme]
        rtc_url = urlunsplit((scheme, address.netloc, address.path, "", ""))
        await asyncio.wait_for(room.connect(rtc_url, token, options=rtc.RoomOptions(auto_subscribe=False)), timeout=15)
        capture.connected = True
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                subscribe(publication, participant)
        while not capture.stop.is_set():
            try:
                await asyncio.wait_for(capture.stop.wait(), timeout=2)
            except TimeoutError:
                async with SessionFactory() as db:
                    status = await db.scalar(select(InterviewSession.status).where(InterviewSession.id == session_id))
                if status != "in_progress":
                    capture.stop.set()
    except asyncio.CancelledError:
        capture.error = "TRANSCRIPTION_INTERRUPTED"
        raise
    except Exception as error:
        capture.error = safe_error(error)
        logger.warning("Automatic transcription stopped; session_id=%s; code=%s", session_id, capture.error)
    finally:
        capture.stop.set()
        streams = list(capture.streams.values())
        if not streams:
            capture.flags.add("NO_MICROPHONE_CAPTURED")
        for stream in streams:
            stream["stop"].set()
        tasks = [s["task"] for s in streams]
        if tasks:
            try:
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=15)
                if any(isinstance(result, BaseException) for result in results):
                    capture.error = capture.error or "TRANSCRIPTION_FLUSH_FAILED"
            except TimeoutError:
                capture.error = "TRANSCRIPTION_FLUSH_TIMEOUT"
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(room.disconnect(), timeout=5)
        except Exception:
            capture.error = capture.error or "TRANSCRIPTION_CLEANUP_FAILED"
        if transcript_id is not None:
            await _record_stop(transcript_id, capture)
