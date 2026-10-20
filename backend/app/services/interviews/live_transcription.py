"""API-process automatic transcription; durable restart recovery is separate."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from aiohttp import ClientResponseError
from livekit import api, rtc
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.interviews import InterviewSession
from app.models.interview_transcripts import InterviewTranscript, InterviewAudioStream
from app.services.interviews.livekit import livekit_credentials
from app.services.interviews.transcription import transcribe_microphone
from app.services.interviews.transcripts import begin_transcript, mark_speech_boundary, save_final_turn
from app.services.interviews.audio_timing import AudioClock, TIMING_BASIS
from app.services.interviews.transcript_preview import TranscriptPreview


logger = logging.getLogger("distill_ai.transcription")


@dataclass
class Capture:
    id: UUID = field(default_factory=uuid4)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    streams: dict = field(default_factory=dict)
    flags: set = field(default_factory=set)
    error: str | None = None
    connected: bool = False
    halt: asyncio.Event = field(default_factory=asyncio.Event)
    reconnecting: bool = False
    transcript_id: UUID | None = None
    preview: TranscriptPreview = field(default_factory=TranscriptPreview)


_captures: dict[UUID, Capture] = {}
RETRY_DELAYS = (1, 2, 4)
RETRYABLE = {"TRANSCRIPTION_FAILED", "TRANSCRIPTION_TIMEOUT", "TRANSCRIPTION_CONNECTION_CLOSED",
             "TRANSCRIPTION_FLUSH_FAILED", "TRANSCRIPTION_ROOM_DISCONNECTED", "TRANSCRIPTION_PROVIDER_FAILED",
             "TRANSCRIPTION_RATE_LIMITED", "AUDIO_BUFFER_OVERFLOW"}


async def retry_delay(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return False
    except TimeoutError:
        return True


def interim_snapshot(session_id, transcript_id):
    capture = _captures.get(session_id)
    if capture is None or capture.transcript_id != transcript_id:
        return []
    return capture.preview.snapshot()


def capture_status(session_id):
    capture = _captures.get(session_id)
    if capture is None:
        return {"status": "inactive", "roles": []}
    roles = sorted({s["role"] for s in capture.streams.values()
                    if s["ready"].is_set() and not s["task"].done() and not s.get("error")})
    recovering = capture.reconnecting or any(s.get("recovering") for s in capture.streams.values())
    failed = any(s.get("error") and s["task"].done() for s in capture.streams.values())
    status = "stopping" if capture.halt.is_set() else "reconnecting" if recovering else (
        "listening" if roles else "failed" if failed else "stopping" if capture.stop.is_set() else
        "waiting_for_microphone" if capture.connected else "connecting")
    return {"status": status, "roles": roles,
            "failed_roles": sorted({s["role"] for s in capture.streams.values() if s.get("error")})}


def start_transcription(session_id):
    """Call only after the session service has authorized and committed admission."""
    current = _captures.get(session_id)
    if current is not None and current.task is not None and not current.task.done():
        return
    capture = Capture()
    _captures[session_id] = capture
    capture.task = asyncio.create_task(_supervise(session_id, capture))

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
    capture.halt.set()
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


async def resume_active_transcriptions():
    """Resume admitted active interviews after an API reload without another Join."""
    from app.models.interviews import InterviewInvitation
    async with SessionFactory() as db:
        ids = list(await db.scalars(select(InterviewSession.id)
            .join(InterviewInvitation, InterviewInvitation.application_id == InterviewSession.application_id)
            .where(InterviewSession.status == "in_progress", InterviewSession.candidate_admitted_at.is_not(None),
                InterviewInvitation.recruiter_id == InterviewSession.recruiter_id,
                InterviewInvitation.token_hash == InterviewSession.admitted_invitation_hash,
                InterviewInvitation.revoked_at.is_(None),
                InterviewInvitation.expires_at > datetime.now(timezone.utc))))
    for session_id in ids:
        start_transcription(session_id)


def safe_error(error):
    if isinstance(error, ClientResponseError):
        return {401: "TRANSCRIPTION_AUTH_FAILED", 402: "TRANSCRIPTION_CREDITS_REQUIRED",
                403: "TRANSCRIPTION_ACCESS_DENIED", 429: "TRANSCRIPTION_RATE_LIMITED"}.get(
                    error.status, "TRANSCRIPTION_PROVIDER_FAILED")
    if isinstance(error, TimeoutError):
        return "TRANSCRIPTION_TIMEOUT"
    if isinstance(error, ValueError) and str(error) == "DEEPGRAM_API_KEY_MISSING":
        return "DEEPGRAM_API_KEY_MISSING"
    if isinstance(error, ValueError) and str(error) in {
        "AUDIO_FRAME_INVALID", "AUDIO_BUFFER_OVERFLOW", "AUDIO_CLOCK_INVALID", "TRANSCRIPTION_FLUSH_FAILED",
        "TRANSCRIPTION_CONNECTION_CLOSED"}:
        return str(error)
    return "TRANSCRIPTION_FAILED"


async def _supervise(session_id, capture):
    """Recover the room subscriber; End/shutdown cancels any pending backoff."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        if capture.halt.is_set():
            return
        capture.stop.clear()
        capture.streams.clear()
        capture.connected = False
        capture.error = None
        capture.id = uuid4()
        capture.preview.drafts.clear()
        try:
            await _run(session_id, capture)
        except asyncio.CancelledError:
            raise
        except Exception:
            capture.error = "TRANSCRIPTION_FAILED"
            logger.warning("Capture cleanup failed; session_id=%s", session_id)
        if capture.halt.is_set() or capture.error not in RETRYABLE or attempt == len(RETRY_DELAYS):
            return
        capture.reconnecting = True
        logger.warning("Reconnecting transcription room; session_id=%s; attempt=%s; code=%s",
                       session_id, attempt + 1, capture.error)
        if not await retry_delay(capture.halt, RETRY_DELAYS[attempt]):
            return


async def _set_stream_error(transcript_id, capture, entry, code):
    entry["error"] = code
    if code:
        capture.flags.add("TRANSCRIPT_INCOMPLETE")
    current_error = next((s.get("error") for s in capture.streams.values() if s.get("error")), None)
    async with SessionFactory() as db:
        async with db.begin():
            transcript = await db.scalar(select(InterviewTranscript).where(
                InterviewTranscript.id == transcript_id).with_for_update())
            if transcript is not None and transcript.status == "capturing":
                if code:
                    transcript.quality_flags = sorted(set(transcript.quality_flags) | {code, "TRANSCRIPT_INCOMPLETE"})
                transcript.error_code = current_error


async def _follow_microphone(transcript_id, owner_id, capture, entry, identity, track, elapsed, anchor):
    """Each provider reconnect gets its own stream ID; the transcript is retained."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        if capture.stop.is_set() or entry["stop"].is_set():
            return
        entry["ready"].clear()
        stream_id = uuid4()

        async def emit(event):
            if event["role"] != entry["role"]:
                raise ValueError("TRANSCRIPT_SPEAKER_MISMATCH")
            if event.get("kind") == "speech_boundary":
                await mark_speech_boundary(transcript_id, owner_id, stream_id, event["stream_end_ms"])
            elif event["is_final"]:
                await save_final_turn(transcript_id, owner_id, stream_id, identity, event)
                # Keep the draft visible until the final source is committed.
                capture.preview.discard(stream_id)
            else:
                capture.preview.update(stream_id, event)

        async def ready():
            entry["recovering"] = False
            await _set_stream_error(transcript_id, capture, entry, None)

        try:
            await _capture_stream(transcript_id, capture.id, stream_id, identity,
                track, entry["role"], emit, entry["stop"], entry["ready"], AudioClock(elapsed, anchor), ready)
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = safe_error(error)
            entry["ready"].clear()
            capture.preview.discard(stream_id)
            await _set_stream_error(transcript_id, capture, entry, code)
            logger.warning("Microphone transcription failed; role=%s; attempt=%s; code=%s",
                           entry["role"], attempt + 1, code)
            if (capture.stop.is_set() or entry["stop"].is_set() or code not in RETRYABLE
                    or attempt == len(RETRY_DELAYS)):
                entry["recovering"] = False
                return
            entry["recovering"] = True
            if not await retry_delay(entry["stop"], RETRY_DELAYS[attempt]):
                entry["recovering"] = False
                return
        finally:
            capture.preview.discard(stream_id)


async def _record_stop(transcript_id, capture):
    async with SessionFactory() as db:
        async with db.begin():
            transcript = await db.scalar(select(InterviewTranscript).where(
                InterviewTranscript.id == transcript_id).with_for_update())
            if transcript is not None and transcript.status == "capturing":
                session = await db.get(InterviewSession, transcript.session_id)
                ended = session is not None and session.status in {"closing", "completed"}
                if not ended:
                    capture.flags.add("TRANSCRIPT_INCOMPLETE")
                transcript.status = "finalizing" if ended and not capture.error else "needs_review"
                transcript.error_code = capture.error
                transcript.quality_flags = sorted(set(transcript.quality_flags) | capture.flags | {"FINALIZATION_PENDING"})
    from app.services.interviews.transcript_finalization import finalize_transcript
    await finalize_transcript(transcript_id, automatic=True)


async def _capture_stream(transcript_id, capture_id, stream_id, identity,
                          track, role, emit, stop, ready, clock, on_ready=None):
    async with SessionFactory() as db:
        async with db.begin():
            db.add(InterviewAudioStream(id=stream_id, transcript_id=transcript_id,
                capture_id=capture_id, speaker_role=role, participant_identity=identity,
                timing_basis=TIMING_BASIS, status="capturing"))
    drained = False
    try:
        await transcribe_microphone(track, role, emit, seconds=None,
            stop_event=stop, ready_event=ready, audio_clock=clock, on_ready=on_ready)
        drained = True
    finally:
        async with SessionFactory() as db:
            async with db.begin():
                stream = await db.get(InterviewAudioStream, stream_id)
                if stream is not None:
                    for name, value in clock.summary().items():
                        setattr(stream, name, value)
                    stream.status = "drained" if drained else "failed"
                    stream.ended_at = datetime.now(timezone.utc)


async def _run(session_id, capture):
    room = rtc.Room()
    transcript_id = None
    try:
        async with SessionFactory() as db:
            session = await db.get(InterviewSession, session_id)
            if session is None or session.status != "in_progress" or session.candidate_admitted_at is None:
                return
            # Recheck admission on every automatic room reconnect.
            from app.models.interviews import InterviewInvitation
            invitation = await db.scalar(select(InterviewInvitation).where(
                InterviewInvitation.application_id == session.application_id,
                InterviewInvitation.recruiter_id == session.recruiter_id,
                InterviewInvitation.token_hash == session.admitted_invitation_hash,
                InterviewInvitation.revoked_at.is_(None),
                InterviewInvitation.expires_at > datetime.now(timezone.utc)))
            if invitation is None:
                return
            # Copy immutable identifiers and close the read transaction before media work.
            job_id, application_id, owner_id = session.job_description_id, session.application_id, session.recruiter_id
            room_name = session.room_name
            # Sample-clock arithmetic uses monotonic time after this single
            # UTC-to-session reference. Result/save arrival time is never used.
            anchor = asyncio.get_running_loop().time()
            session_elapsed_ms = (datetime.now(timezone.utc) - session.started_at).total_seconds() * 1000
        info = await begin_transcript(job_id, application_id, owner_id)
        transcript_id = UUID(info["transcript_id"])
        capture.transcript_id = transcript_id
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
            previous = capture.streams.get(publication.sid)
            if (capture.stop.is_set() or role is None
                    or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
                    or track.kind != rtc.TrackKind.KIND_AUDIO):
                return
            if previous and not previous["task"].done():
                if previous["stop"].is_set() and not previous.get("resubscribe_pending"):
                    previous["resubscribe_pending"] = True
                    def resume_after_flush(_):
                        if getattr(publication, "track", None) is track:
                            subscribed(track, publication, participant)
                    previous["task"].add_done_callback(resume_after_flush)
                return
            if any(s["role"] == role and not s["stop"].is_set() and not s["task"].done()
                   for s in capture.streams.values()):
                capture.error = "DUPLICATE_ROLE_MICROPHONE"
                capture.stop.set()
                return
            ready, stop = asyncio.Event(), asyncio.Event()
            identity = participant.identity
            entry = {"role": role, "ready": ready, "stop": stop, "track": track, "error": None, "recovering": False}
            task = asyncio.create_task(_follow_microphone(transcript_id, owner_id, capture,
                entry, identity, track, session_elapsed_ms, anchor))
            entry["task"] = task
            capture.streams[publication.sid] = entry

            def done(finished):
                if not finished.cancelled() and finished.exception() is not None:
                    entry["error"] = safe_error(finished.exception())
                    entry["ready"].clear()
                    logger.warning("Microphone recovery stopped; role=%s; code=%s", role, entry["error"])

            task.add_done_callback(done)

        @room.on("track_unmuted")
        def unmuted(publication, participant):
            entry = capture.streams.get(publication.sid)
            if entry is not None and entry["task"].done():
                subscribed(entry["track"], publication, participant)
            else:
                subscribe(publication, participant)

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
        capture.reconnecting = False
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
