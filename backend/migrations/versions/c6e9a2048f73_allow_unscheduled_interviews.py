"""Allow unscheduled interviews without fabricated planned dates."""

from alembic import op
import sqlalchemy as sa

revision = "c6e9a2048f73"
down_revision = "b4d2e9017c65"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_interview_session_times", "interview_sessions", type_="check")
    op.drop_constraint("ck_interview_session_status", "interview_sessions", type_="check")
    for column in ("starts_at", "ends_at"):
        op.alter_column(
            "interview_sessions", column,
            existing_type=sa.DateTime(timezone=True), nullable=True,
        )
    op.alter_column(
        "interview_sessions", "display_timezone",
        existing_type=sa.String(100), nullable=True,
    )
    op.alter_column(
        "interview_sessions", "status",
        existing_type=sa.String(24), server_default="ready",
    )
    op.create_check_constraint(
        "ck_interview_session_times", "interview_sessions",
        "(starts_at IS NULL AND ends_at IS NULL) OR "
        "(starts_at IS NOT NULL AND ends_at IS NOT NULL AND ends_at > starts_at)",
    )
    op.create_check_constraint(
        "ck_interview_session_status", "interview_sessions",
        "status IN ('ready', 'scheduled', 'in_progress', 'completed', 'cancelled')",
    )


def downgrade():
    # Refuse to discard unscheduled data or invent dates to fit the old schema.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM interview_sessions
                WHERE starts_at IS NULL OR ends_at IS NULL
                   OR display_timezone IS NULL OR status = 'ready'
            ) THEN
                RAISE EXCEPTION 'Cannot downgrade while unscheduled interviews exist';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_interview_session_times", "interview_sessions", type_="check")
    op.drop_constraint("ck_interview_session_status", "interview_sessions", type_="check")
    for column in ("starts_at", "ends_at"):
        op.alter_column(
            "interview_sessions", column,
            existing_type=sa.DateTime(timezone=True), nullable=False,
        )
    op.alter_column(
        "interview_sessions", "display_timezone",
        existing_type=sa.String(100), nullable=False,
    )
    op.alter_column(
        "interview_sessions", "status",
        existing_type=sa.String(24), server_default="scheduled",
    )
    op.create_check_constraint(
        "ck_interview_session_times", "interview_sessions", "ends_at > starts_at",
    )
    op.create_check_constraint(
        "ck_interview_session_status", "interview_sessions",
        "status IN ('scheduled', 'in_progress', 'completed', 'cancelled')",
    )
