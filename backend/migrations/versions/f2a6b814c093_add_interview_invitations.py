"""Add individual interview invitation tokens."""
from alembic import op
import sqlalchemy as sa

revision = "f2a6b814c093"
down_revision = "e7c142d9a630"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "interview_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_description_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("application_id", name="uq_interview_invitation_application"),
        sa.UniqueConstraint("token_hash", name="uq_interview_invitation_token"),
    )
    op.create_index("ix_interview_invitations_recruiter_id", "interview_invitations", ["recruiter_id"])
    op.create_index("ix_interview_invitations_job_description_id", "interview_invitations", ["job_description_id"])


def downgrade():
    op.drop_table("interview_invitations")
