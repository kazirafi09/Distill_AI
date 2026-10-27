"""Add versioned F5/F6 assessment storage and durable execution checkpoints."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c8d420f6a137"
down_revision = "b7e530a8d214"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("assessment_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("candidate_applications.id", ondelete="CASCADE")),
        sa.Column("transcript_id", sa.Uuid(), sa.ForeignKey("interview_transcripts.id", ondelete="CASCADE")),
        sa.Column("parent_run_id", sa.Uuid(), sa.ForeignKey("assessment_runs.id", ondelete="CASCADE")),
        sa.Column("cv_assessment_id", sa.Uuid(), sa.ForeignKey("cv_assessments.id", ondelete="SET NULL")),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("input_key", sa.String(64), nullable=False),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stage", sa.String(100), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("total_groups", sa.Integer(), nullable=False),
        sa.Column("completed_groups", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("recruiter_id", "input_key", name="uq_assessment_run_input"),
        sa.CheckConstraint("kind IN ('f5', 'f6')", name="ck_assessment_run_kind"),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'needs_review', 'failed')", name="ck_assessment_run_status"),
        sa.CheckConstraint("attempt >= 0 AND attempt <= 6", name="ck_assessment_run_attempt"),
        sa.CheckConstraint("NOT synthetic OR (application_id IS NULL AND transcript_id IS NULL)", name="ck_assessment_synthetic_scope"))
    for column in ("recruiter_id", "job_id", "status", "lease_expires_at"):
        op.create_index(f"ix_assessment_runs_{column}", "assessment_runs", [column])
    op.create_table("assessment_stages",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("assessment_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.String(100), primary_key=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("assessment_answers",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("assessment_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("group_id", sa.String(16), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False))
    op.create_table("assessment_results",
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("assessment_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    for table in ("assessment_results", "assessment_answers", "assessment_stages", "assessment_runs"):
        op.drop_table(table)
