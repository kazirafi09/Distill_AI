"""Persist editable invitation times separately from active interview sessions."""
from alembic import op
import sqlalchemy as sa

revision = "b8e124d60a39"
down_revision = "a7e013c59d22"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("interview_invitation_schedule_drafts",
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("display_timezone", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_invitation_draft_times"))


def downgrade():
    op.drop_table("interview_invitation_schedule_drafts")
