"""Add scheduled human interview sessions."""

from alembic import op
import sqlalchemy as sa

revision = "b4d2e9017c65"
down_revision = "a8b315c9e204"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id", sa.Uuid(),
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "job_description_id", sa.Uuid(),
            sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recruiter_id", sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("display_timezone", sa.String(100), nullable=False),
        sa.Column("room_name", sa.String(100), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="scheduled"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("ends_at > starts_at", name="ck_interview_session_times"),
        sa.CheckConstraint(
            "status IN ('scheduled', 'in_progress', 'completed', 'cancelled')",
            name="ck_interview_session_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_interview_session_version"),
    )
    for column in ("job_description_id", "recruiter_id"):
        op.create_index(
            f"ix_interview_sessions_{column}", "interview_sessions", [column],
        )


def downgrade():
    op.drop_table("interview_sessions")
