"""Persist five-minute CV source checks."""
from alembic import op
import sqlalchemy as sa

revision = "f5c810d2b934"
down_revision = "e4b8c2907a15"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cv_source_checks",
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("cv_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("pending_file_ids", sa.JSON(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)))


def downgrade():
    op.drop_table("cv_source_checks")
