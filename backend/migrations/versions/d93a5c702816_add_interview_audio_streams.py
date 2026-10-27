"""Persist measured microphone stream timing and flush lifecycle.

Revision ID: d93a5c702816
Revises: c8d420f6a137
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d93a5c702816"
down_revision = "c8d420f6a137"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("interview_audio_streams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("transcript_id", sa.Uuid(), sa.ForeignKey("interview_transcripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("capture_id", sa.Uuid(), nullable=False),
        sa.Column("speaker_role", sa.String(20), nullable=False),
        sa.Column("participant_identity", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("timing_basis", sa.String(80), nullable=False),
        sa.Column("origin_ms", sa.Integer(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("samples_sent", sa.Integer(), nullable=False),
        sa.Column("max_skew_ms", sa.Integer(), nullable=False),
        sa.Column("quality_flags", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('capturing', 'drained', 'failed')", name="ck_audio_stream_status"),
        sa.CheckConstraint("sample_rate = 16000 AND samples_sent >= 0", name="ck_audio_stream_samples"),
        sa.CheckConstraint("origin_ms IS NULL OR origin_ms >= 0", name="ck_audio_stream_origin"),
        sa.CheckConstraint("speaker_role IN ('recruiter', 'candidate')", name="ck_audio_stream_role"))
    op.create_index("ix_interview_audio_streams_transcript_id", "interview_audio_streams", ["transcript_id"])


def downgrade():
    op.drop_table("interview_audio_streams")
