"""Owned transcript creation and finalized-segment persistence."""

from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.interview_transcripts import (
    InterviewTranscript,
    InterviewTranscriptTurn,
)
from app.models.interviews import InterviewSession
from app.services.interviews.sessions import owned_session
from app.services.cv.screening import owned_application


def speech_flags(flags, speech_final=None, from_finalize=False):
    """Keep source boundary metadata in the existing JSONB field."""
    flags = set(flags)
    if speech_final is not None:
        flags -= {"SPEECH_CONTINUES", "SPEECH_BOUNDARY_UNKNOWN"}
        flags.add("SPEECH_FINAL" if speech_final or "SPEECH_FINAL" in flags else "SPEECH_CONTINUES")
    elif not flags & {"SPEECH_FINAL", "SPEECH_CONTINUES"}:
        flags.add("SPEECH_BOUNDARY_UNKNOWN")
    if from_finalize:
        flags.add("SOURCE_FINALIZE")
    return sorted(flags)


async def _owned_transcript(db, transcript_id, recruiter_id):
    row = (await db.execute(
        select(InterviewTranscript, InterviewSession)
        .join(InterviewSession, InterviewSession.id == InterviewTranscript.session_id)
        .where(InterviewTranscript.id == transcript_id, InterviewSession.recruiter_id == recruiter_id)
        .with_for_update(of=InterviewTranscript)
    )).one_or_none()
    if row is None:
        raise HTTPException(404, "Transcript not found.")
    return row


async def mark_speech_boundary(transcript_id, recruiter_id, stream_id, end_ms):
    """Attach a text-free provider endpoint to its preceding segment, not a new turn."""
    async with SessionFactory() as db:
        async with db.begin():
            transcript, _ = await _owned_transcript(db, transcript_id, recruiter_id)
            if transcript.status != "capturing":
                return
            turn = await db.scalar(select(InterviewTranscriptTurn).where(
                InterviewTranscriptTurn.transcript_id == transcript_id,
                InterviewTranscriptTurn.stream_id == stream_id,
                InterviewTranscriptTurn.stream_end_ms <= end_ms,
            ).order_by(InterviewTranscriptTurn.stream_end_ms.desc(), InterviewTranscriptTurn.id.desc()).limit(1))
            if turn is not None:
                turn.quality_flags = speech_flags(turn.quality_flags, True)


async def get_transcript(job_id: UUID, application_id: UUID, recruiter_id: UUID):
    """Read the latest owned version in save order; stream clocks are independent."""
    async with SessionFactory() as db:
        await owned_application(db, job_id, application_id, recruiter_id)
        transcript = await db.scalar(
            select(InterviewTranscript)
            .join(InterviewSession, InterviewSession.id == InterviewTranscript.session_id)
            .where(
                InterviewSession.job_description_id == job_id,
                InterviewSession.application_id == application_id,
                InterviewSession.recruiter_id == recruiter_id,
            )
            .order_by(InterviewTranscript.version.desc())
            .limit(1)
        )
        response = {"transcript": None, "turns": [], "has_earlier_turns": False,
                    "capture": {"status": "inactive", "roles": []}}
        if transcript is None:
            return response
        # Bound repeated polling responses. Fetch an extra row so older omitted
        # segments are explicitly disclosed instead of silently disappearing.
        turns = list(await db.scalars(
            select(InterviewTranscriptTurn)
            .where(InterviewTranscriptTurn.transcript_id == transcript.id)
            .order_by(InterviewTranscriptTurn.created_at.desc(), InterviewTranscriptTurn.id.desc())
            .limit(501)
        ))
        response["has_earlier_turns"] = len(turns) > 500
        response["transcript"] = {
            "id": str(transcript.id), "version": transcript.version,
            "status": transcript.status, "quality_flags": transcript.quality_flags,
            "error_code": transcript.error_code,
        }
        from app.services.interviews.live_transcription import capture_status
        response["capture"] = capture_status(transcript.session_id)
        response["turns"] = [{
            "id": str(turn.id), "stream_id": str(turn.stream_id),
            "speaker_role": turn.speaker_role, "text": turn.text,
            "stream_start_ms": turn.stream_start_ms, "stream_end_ms": turn.stream_end_ms,
            "speech_final": True if "SPEECH_FINAL" in turn.quality_flags else (
                False if "SPEECH_CONTINUES" in turn.quality_flags else None),
            "created_at": turn.created_at.isoformat(),
        } for turn in reversed(turns[:500])]
        return response


class TranscriptEvent(BaseModel):
    # Deepgram word metadata is not persisted by this first service.
    model_config = ConfigDict(
        extra="ignore",
        hide_input_in_errors=True,
    )

    role: Literal["recruiter", "candidate"]
    text: str = Field(min_length=1, max_length=20000)
    is_final: StrictBool
    stream_start_ms: int = Field(strict=True, ge=0, le=2147483647)
    stream_end_ms: int = Field(strict=True, ge=0, le=2147483647)
    speech_final: StrictBool | None = None
    from_finalize: StrictBool = False

    @model_validator(mode="after")
    def validate_segment(self):
        if not self.text.strip():
            raise ValueError("TRANSCRIPT_TEXT_EMPTY")
        if self.stream_end_ms < self.stream_start_ms:
            raise ValueError("TRANSCRIPT_TIMES_INVALID")
        return self


async def begin_transcript(
    job_id: UUID,
    application_id: UUID,
    recruiter_id: UUID,
):
    settings = get_settings()

    async with SessionFactory() as db:
        async with db.begin():
            # Existing helper verifies ownership and serializes this job's
            # short database writes. No provider call happens here.
            session = await owned_session(
                db, job_id, application_id, recruiter_id,
            )

            if session is None:
                raise HTTPException(404, "Interview session not found.")

            if session.status != "in_progress":
                raise HTTPException(409, "Interview is not active.")

            current = await db.scalar(
                select(InterviewTranscript)
                .where(InterviewTranscript.session_id == session.id)
                .order_by(InterviewTranscript.version.desc())
                .limit(1)
            )

            if current and current.status == "capturing":
                if (
                    current.provider != "deepgram"
                    or current.model != settings.deepgram_model
                    or current.language != settings.deepgram_language
                ):
                    raise HTTPException(
                        409,
                        "Active transcript configuration has changed.",
                    )
                transcript = current
            else:
                if current and current.status == "finalizing":
                    raise HTTPException(
                        409, "Transcript finalization is still running.",
                    )

                latest_version = await db.scalar(
                    select(func.max(InterviewTranscript.version))
                    .where(InterviewTranscript.session_id == session.id)
                )

                transcript = InterviewTranscript(
                    session_id=session.id,
                    version=(latest_version or 0) + 1,
                    status="capturing",
                    provider="deepgram",
                    model=settings.deepgram_model,
                    language=settings.deepgram_language,
                    quality_flags=["SESSION_TIMELINE_NOT_ALIGNED"],
                )
                db.add(transcript)
                await db.flush()

            result = {
                "transcript_id": str(transcript.id),
                "session_id": str(transcript.session_id),
                "version": transcript.version,
                "status": transcript.status,
            }

        return result


async def save_final_turn(
    transcript_id: UUID,
    recruiter_id: UUID,
    stream_id: UUID,
    participant_identity: str,
    event: dict,
):
    parsed = TranscriptEvent.model_validate(event)

    if not parsed.is_final:
        return {"saved": False, "reason": "interim_result"}

    async with SessionFactory() as db:
        async with db.begin():
            transcript, session = await _owned_transcript(db, transcript_id, recruiter_id)

            expected_identity = (
                f"recruiter-{session.recruiter_id}"
                if parsed.role == "recruiter"
                else f"candidate-{session.application_id}"
            )

            if participant_identity != expected_identity:
                raise HTTPException(
                    409, "Transcript speaker does not match this interview.",
                )

            source_event_key = (
                f"{stream_id}:"
                f"{parsed.stream_start_ms}:"
                f"{parsed.stream_end_ms}"
            )

            existing = await db.scalar(
                select(InterviewTranscriptTurn).where(
                    InterviewTranscriptTurn.transcript_id == transcript.id,
                    InterviewTranscriptTurn.source_event_key
                    == source_event_key,
                )
            )

            if existing:
                if (
                    existing.text != parsed.text
                    or existing.speaker_role != parsed.role
                    or existing.participant_identity != participant_identity
                ):
                    raise HTTPException(
                        409,
                        "Repeated event conflicts with saved transcript text.",
                    )

                result = {
                    "saved": False,
                    "reason": "duplicate",
                    "turn_id": str(existing.id),
                }
                if transcript.status == "capturing":
                    existing.quality_flags = speech_flags(
                        existing.quality_flags, parsed.speech_final, parsed.from_finalize,
                    )
            else:
                if transcript.status != "capturing":
                    raise HTTPException(
                        409, "Transcript is no longer accepting new segments.",
                    )

                turn = InterviewTranscriptTurn(
                    transcript_id=transcript.id,
                    stream_id=stream_id,
                    source_event_key=source_event_key,
                    speaker_role=parsed.role,
                    participant_identity=participant_identity,
                    text=parsed.text,
                    stream_start_ms=parsed.stream_start_ms,
                    stream_end_ms=parsed.stream_end_ms,
                    session_start_ms=None,
                    session_end_ms=None,
                    quality_flags=speech_flags(
                        ["SESSION_TIMELINE_NOT_ALIGNED"], parsed.speech_final, parsed.from_finalize,
                    ),
                )
                db.add(turn)
                await db.flush()

                result = {
                    "saved": True,
                    "turn_id": str(turn.id),
                    "transcript_id": str(transcript.id),
                }

        return result
