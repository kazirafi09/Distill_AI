"""Add reviewed invitation batches and encrypted durable email jobs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a8b315c9e204"
down_revision = "f2a6b814c093"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("interview_invitation_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_description_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("preview_key", sa.String(64), nullable=False),
        sa.Column("preview", JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for column in ("recruiter_id", "job_description_id"):
        op.create_index(f"ix_interview_invitation_batches_{column}", "interview_invitation_batches", [column])
    op.create_table("interview_invitation_email_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("invitation_id", sa.Uuid(), sa.ForeignKey("interview_invitations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("interview_invitation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("encrypted_message", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("provider_message_id", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("invitation_id", name="uq_invitation_email_job"))
    for column in ("batch_id", "status"):
        op.create_index(f"ix_interview_invitation_email_jobs_{column}", "interview_invitation_email_jobs", [column])


def downgrade():
    op.drop_table("interview_invitation_email_jobs")
    op.drop_table("interview_invitation_batches")
