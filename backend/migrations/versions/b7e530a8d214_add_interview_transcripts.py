"""Add versioned interview transcripts and finalized speech segments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b7e530a8d214"
down_revision = "a21c78e4b609"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "interview_transcripts",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "session_id", sa.Uuid(),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("language", sa.String(30), nullable=False),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "session_id", "version", name="uq_interview_transcript_session_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_interview_transcript_version"),
        sa.CheckConstraint(
            "status IN ('capturing', 'finalizing', 'completed', 'needs_review', 'failed')",
            name="ck_interview_transcript_status",
        ),
    )
    op.create_index(
        "ix_interview_transcripts_session_id", "interview_transcripts", ["session_id"],
    )

    op.create_table(
        "interview_transcript_turns",
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column(
            "transcript_id", sa.Uuid(),
            sa.ForeignKey("interview_transcripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_key", sa.String(160), nullable=False),
        sa.Column("speaker_role", sa.String(20), nullable=False),
        sa.Column("participant_identity", sa.String(255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("stream_start_ms", sa.Integer(), nullable=False),
        sa.Column("stream_end_ms", sa.Integer(), nullable=False),
        sa.Column("session_start_ms", sa.Integer(), nullable=True),
        sa.Column("session_end_ms", sa.Integer(), nullable=True),
        sa.Column("quality_flags", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "transcript_id", "source_event_key",
            name="uq_interview_transcript_turn_event",
        ),
        sa.CheckConstraint(
            "speaker_role IN ('recruiter', 'candidate')",
            name="ck_interview_transcript_turn_role",
        ),
        sa.CheckConstraint(
            "stream_start_ms >= 0 AND stream_end_ms >= stream_start_ms",
            name="ck_interview_transcript_turn_stream_times",
        ),
        sa.CheckConstraint(
            "(session_start_ms IS NULL AND session_end_ms IS NULL) OR "
            "(session_start_ms IS NOT NULL AND session_end_ms IS NOT NULL AND "
            "session_start_ms >= 0 AND session_end_ms >= session_start_ms)",
            name="ck_interview_transcript_turn_session_times",
        ),
        sa.CheckConstraint(
            "length(trim(text)) > 0", name="ck_interview_transcript_turn_text",
        ),
    )
    op.create_index(
        "ix_interview_transcript_turns_transcript_id",
        "interview_transcript_turns", ["transcript_id"],
    )


def downgrade():
    op.drop_index(
        "ix_interview_transcript_turns_transcript_id",
        table_name="interview_transcript_turns",
    )
    op.drop_table("interview_transcript_turns")
    op.drop_index(
        "ix_interview_transcripts_session_id", table_name="interview_transcripts",
    )
    op.drop_table("interview_transcripts")
