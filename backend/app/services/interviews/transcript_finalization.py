"""Idempotent, transactional finalization from persisted measured PCM clocks."""
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.interviews import InterviewSession
from app.models.interview_transcripts import InterviewTranscript, InterviewTranscriptTurn, InterviewAudioStream
from app.services.interviews.audio_timing import TIMING_BASIS, MAX_SKEW_MS, SAMPLE_RATE
from app.services.interviews.analysis import digest
from app.services.interviews.transcript_preparation import AlignedTurn, saved_text_content

PENDING = {"FINALIZATION_PENDING", "SESSION_TIMELINE_NOT_ALIGNED"}
UNSAFE = {"MICROPHONE_TRACK_INTERRUPTED", "AUDIO_GAP", "TRANSCRIPT_INCOMPLETE", "TIMING_UNCERTAIN",
          "CAPTURE_PROCESS_LOST", "NO_MICROPHONE_CAPTURED"}


def aligned_content(transcript, rows, streams, session):
    """Validate everything before returning a proposed immutable content snapshot."""
    if transcript.error_code or UNSAFE.intersection(transcript.quality_flags):
        raise ValueError(transcript.error_code or "TRANSCRIPT_CAPTURE_NEEDS_REVIEW")
    if not 1 <= len(rows) <= 1000:
        raise ValueError("TRANSCRIPT_SEGMENT_COUNT_INVALID")
    if not streams:
        raise ValueError("STREAM_TIMING_MISSING")
    by_id = {stream.id: stream for stream in streams}
    if len(by_id) != len(streams):
        raise ValueError("STREAM_TIMING_INVALID")
    for stream in streams:
        if (stream.transcript_id != transcript.id or stream.status != "drained" or stream.ended_at is None
                or stream.timing_basis != TIMING_BASIS or stream.origin_ms is None or stream.origin_ms < 0
                or stream.sample_rate != SAMPLE_RATE or stream.samples_sent <= 0
                or not 0 <= stream.max_skew_ms <= MAX_SKEW_MS or stream.quality_flags):
            raise ValueError("STREAM_TIMING_OR_FLUSH_UNVERIFIED")
    content = []
    for row in rows:
        stream = by_id.get(row.stream_id)
        expected_identity = (f"recruiter-{session.recruiter_id}" if row.speaker_role == "recruiter"
                             else f"candidate-{session.application_id}")
        if (stream is None or stream.speaker_role != row.speaker_role
                or row.participant_identity != expected_identity or stream.participant_identity != expected_identity
                or UNSAFE.intersection(row.quality_flags)):
            raise ValueError("TRANSCRIPT_STREAM_OR_SPEAKER_INVALID")
        # Provider rounding can differ by one millisecond from sample counting.
        if row.stream_end_ms > round(stream.samples_sent * 1000 / SAMPLE_RATE) + 1:
            raise ValueError("TRANSCRIPT_AUDIO_RANGE_INVALID")
        data = {name: getattr(row, name) for name in (
            "id", "transcript_id", "stream_id", "source_event_key", "speaker_role", "participant_identity", "text",
            "stream_start_ms", "stream_end_ms", "quality_flags", "created_at")}
        data.update(session_start_ms=stream.origin_ms + row.stream_start_ms,
                    session_end_ms=stream.origin_ms + row.stream_end_ms,
                    quality_flags=sorted(set(row.quality_flags) - PENDING), timing_basis="finalized_session_timeline")
        proposed = AlignedTurn.model_validate(data)
        if row.session_start_ms is not None and (row.session_start_ms != proposed.session_start_ms
                                                 or row.session_end_ms != proposed.session_end_ms):
            raise ValueError("SESSION_TIMING_CONFLICT")
        content.append(proposed.model_dump(mode="json"))
    if {turn["speaker_role"] for turn in content} != {"recruiter", "candidate"}:
        raise ValueError("BOTH_SPEAKERS_REQUIRED")
    return sorted(content, key=lambda turn: (turn["session_start_ms"], turn["session_end_ms"], turn["id"]))


async def finalize_transcript(transcript_id, recruiter_id=None, *, automatic=False):
    async with SessionFactory() as db:
        async with db.begin():
            # The transcript lock serializes finalization with segment/boundary writes.
            pair = (await db.execute(select(InterviewTranscript, InterviewSession)
                .join(InterviewSession, InterviewSession.id == InterviewTranscript.session_id)
                .where(InterviewTranscript.id == transcript_id)
                .with_for_update(of=InterviewTranscript))).one_or_none()
            if pair is None or (recruiter_id is not None and pair[1].recruiter_id != recruiter_id):
                raise HTTPException(404, "Transcript not found.")
            transcript, session = pair
            if transcript.status == "completed":
                return {"status": "completed", "already_finalized": True}
            if session.status not in {"closing", "completed"} or (transcript.status == "capturing" and session.status != "completed"):
                if automatic:
                    return {"status": transcript.status, "pending": True}
                raise HTTPException(409, "End the interview before finalizing its transcript.")
            streams = list(await db.scalars(select(InterviewAudioStream)
                .where(InterviewAudioStream.transcript_id == transcript.id)))
            rows = list(await db.scalars(select(InterviewTranscriptTurn)
                .where(InterviewTranscriptTurn.transcript_id == transcript.id)))
            text_only = False
            try:
                content = aligned_content(transcript, rows, streams, session)
            except ValueError as error:
                # Ending the interview closes saved text even when clocks or the
                # provider flush cannot establish a complete measured timeline.
                # Preserve those limitations instead of inventing timestamps.
                try:
                    content = saved_text_content(transcript, rows, session)
                except ValueError:
                    transcript.status = "failed"
                    transcript.error_code = "TRANSCRIPT_SOURCE_INVALID"
                    return {"status": "failed", "error_code": transcript.error_code}
                text_only = True
                flags = set(transcript.quality_flags) - {"FINALIZATION_PENDING"}
                if transcript.error_code:
                    flags.add(transcript.error_code)
                flags.add(str(error) if type(error) is ValueError else "TRANSCRIPT_ALIGNMENT_INVALID")
                flags.update({"TEXT_ONLY_FINALIZED", "SAVED_ORDER_NOT_VERIFIED_CHRONOLOGY"})
                if not content:
                    flags.add("NO_USABLE_TRANSCRIPT_TEXT")
                elif {turn["speaker_role"] for turn in content} != {"recruiter", "candidate"}:
                    flags.add("NO_USABLE_QA_TEXT")
                transcript.quality_flags = sorted(flags)
            by_id = {str(row.id): row for row in rows}
            if not text_only:
                for item in content:
                    row = by_id[item["id"]]
                    row.session_start_ms, row.session_end_ms = item["session_start_ms"], item["session_end_ms"]
                    row.quality_flags = item["quality_flags"]
                transcript.quality_flags = sorted(set(transcript.quality_flags) - PENDING)
            transcript.content_sha256 = digest(content)
            transcript.finalized_at = datetime.now(timezone.utc)
            transcript.status = "completed"
            transcript.error_code = None
            return {"status": "completed", "already_finalized": False, "text_only": text_only}


async def retry_finalization(job_id, application_id, recruiter_id, transcript_id):
    # Scope the request before allowing any state transition; stale versions are rejected.
    from app.services.cv.screening import owned_application
    async with SessionFactory() as db:
        await owned_application(db, job_id, application_id, recruiter_id)
        latest = await db.scalar(select(InterviewTranscript.id).join(InterviewSession)
            .where(InterviewSession.job_description_id == job_id,
                   InterviewSession.application_id == application_id,
                   InterviewSession.recruiter_id == recruiter_id)
            .order_by(InterviewTranscript.version.desc()).limit(1))
        if latest != transcript_id:
            raise HTTPException(409, "The transcript version changed. Reload before retrying.")
    return await finalize_transcript(transcript_id, recruiter_id)


async def reconcile_finalization():
    """Restart-safe replay of durable stop records; never guesses lost clocks.

    Capturing transcripts on active sessions are left to their live owner.
    Ended sessions with no stop record are retained for review, not promoted.
    """
    async with SessionFactory() as db:
        async with db.begin():
            abandoned = list(await db.scalars(select(InterviewTranscript)
                .join(InterviewSession).where(InterviewSession.status.in_(["completed", "cancelled"]),
                    InterviewTranscript.status == "capturing").with_for_update(of=InterviewTranscript, skip_locked=True).limit(50)))
            for transcript in abandoned:
                transcript.status = "needs_review"
                transcript.error_code = "CAPTURE_PROCESS_LOST"
                transcript.quality_flags = sorted(set(transcript.quality_flags) | {"CAPTURE_PROCESS_LOST", "FINALIZATION_PENDING"})
        ids = list(await db.scalars(select(InterviewTranscript.id).join(InterviewSession)
            .where(InterviewTranscript.status.in_(["finalizing", "needs_review"]), InterviewSession.status.in_(["closing", "completed"]))
            .limit(50)))
    for transcript_id in ids:
        await finalize_transcript(transcript_id, automatic=True)
