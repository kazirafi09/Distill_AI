"""Versioned interview transcripts and finalized speech segments."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InterviewTranscript(Base):
    __tablename__ = "interview_transcripts"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "version",
            name="uq_interview_transcript_session_version",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_interview_transcript_version",
        ),
        CheckConstraint(
            "status IN ('capturing', 'finalizing', 'completed', "
            "'needs_review', 'failed')",
            name="ck_interview_transcript_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4,
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(
        String(24), default="capturing",
    )

    provider: Mapped[str] = mapped_column(
        String(30), default="deepgram",
    )
    model: Mapped[str] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(30))

    quality_flags: Mapped[list] = mapped_column(
        JSONB, default=list,
    )
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(100))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )


class InterviewTranscriptTurn(Base):
    __tablename__ = "interview_transcript_turns"
    __table_args__ = (
        UniqueConstraint(
            "transcript_id",
            "source_event_key",
            name="uq_interview_transcript_turn_event",
        ),
        CheckConstraint(
            "speaker_role IN ('recruiter', 'candidate')",
            name="ck_interview_transcript_turn_role",
        ),
        CheckConstraint(
            "stream_start_ms >= 0 AND "
            "stream_end_ms >= stream_start_ms",
            name="ck_interview_transcript_turn_stream_times",
        ),
        CheckConstraint(
            "(session_start_ms IS NULL AND session_end_ms IS NULL) OR "
            "(session_start_ms IS NOT NULL AND "
            "session_end_ms IS NOT NULL AND "
            "session_start_ms >= 0 AND "
            "session_end_ms >= session_start_ms)",
            name="ck_interview_transcript_turn_session_times",
        ),
        CheckConstraint(
            "length(trim(text)) > 0",
            name="ck_interview_transcript_turn_text",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4,
    )
    transcript_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_transcripts.id", ondelete="CASCADE"),
        index=True,
    )

    # Identifies one microphone-to-Deepgram connection.
    stream_id: Mapped[UUID] = mapped_column(Uuid)
    source_event_key: Mapped[str] = mapped_column(String(160))

    speaker_role: Mapped[str] = mapped_column(String(20))
    participant_identity: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)

    # Original timestamps returned by this Deepgram stream.
    stream_start_ms: Mapped[int] = mapped_column(Integer)
    stream_end_ms: Mapped[int] = mapped_column(Integer)

    # Common interview timeline, populated only when alignment is known.
    session_start_ms: Mapped[int | None] = mapped_column(Integer)
    session_end_ms: Mapped[int | None] = mapped_column(Integer)

    quality_flags: Mapped[list] = mapped_column(
        JSONB, default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )


class InterviewAudioStream(Base):
    """Measured backend-reception clock; never reconstructed from saved speech."""

    __tablename__ = "interview_audio_streams"
    __table_args__ = (
        CheckConstraint("status IN ('capturing', 'drained', 'failed')", name="ck_audio_stream_status"),
        CheckConstraint("sample_rate = 16000 AND samples_sent >= 0", name="ck_audio_stream_samples"),
        CheckConstraint("origin_ms IS NULL OR origin_ms >= 0", name="ck_audio_stream_origin"),
        CheckConstraint("speaker_role IN ('recruiter', 'candidate')", name="ck_audio_stream_role"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    transcript_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_transcripts.id", ondelete="CASCADE"), index=True)
    capture_id: Mapped[UUID] = mapped_column(Uuid)
    speaker_role: Mapped[str] = mapped_column(String(20))
    participant_identity: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="capturing")
    timing_basis: Mapped[str] = mapped_column(String(80), default="backend_pcm_reception_monotonic_v1")
    origin_ms: Mapped[int | None] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer, default=16000)
    samples_sent: Mapped[int] = mapped_column(Integer, default=0)
    max_skew_ms: Mapped[int] = mapped_column(Integer, default=0)
    quality_flags: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
