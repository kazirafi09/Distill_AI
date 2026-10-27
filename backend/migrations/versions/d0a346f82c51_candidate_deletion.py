"""Candidate deletion cleanup and source import exclusions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d0a346f82c51"
down_revision = "c9f235e71b40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cv_import_exclusions",
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("cv_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("file_key", sa.String(64), primary_key=True))
    op.create_table("candidate_deletions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_candidate_deletions_status", "candidate_deletions", ["status"])


def downgrade():
    op.drop_table("candidate_deletions")
    op.drop_table("cv_import_exclusions")
