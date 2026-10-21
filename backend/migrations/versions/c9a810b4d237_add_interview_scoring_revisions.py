"""Add immutable per-job interview scoring revisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c9a810b4d237"
down_revision = "f3a921d806bc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("interview_scoring_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("policy", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "revision", name="uq_interview_scoring_revision"),
        sa.CheckConstraint("revision > 0", name="ck_interview_scoring_revision_positive"))
    op.create_index("ix_interview_scoring_revisions_job_id", "interview_scoring_revisions", ["job_id"])


def downgrade():
    op.drop_index("ix_interview_scoring_revisions_job_id", table_name="interview_scoring_revisions")
    op.drop_table("interview_scoring_revisions")
