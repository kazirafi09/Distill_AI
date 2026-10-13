"""Add admission state and retryable interview closure."""
from alembic import op
import sqlalchemy as sa

revision = "d7f1b9036a42"
down_revision = "c6e9a2048f73"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("interview_sessions", sa.Column("candidate_admitted_at", sa.DateTime(timezone=True)))
    op.add_column("interview_sessions", sa.Column("admitted_invitation_hash", sa.String(64)))
    op.drop_constraint("ck_interview_session_status", "interview_sessions", type_="check")
    op.create_check_constraint(
        "ck_interview_session_status", "interview_sessions",
        "status IN ('ready', 'scheduled', 'in_progress', 'closing', 'completed', 'cancelled')",
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM interview_sessions WHERE status = 'closing') THEN
                RAISE EXCEPTION 'Finish pending room closures before downgrading';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_interview_session_status", "interview_sessions", type_="check")
    op.create_check_constraint(
        "ck_interview_session_status", "interview_sessions",
        "status IN ('ready', 'scheduled', 'in_progress', 'completed', 'cancelled')",
    )
    op.drop_column("interview_sessions", "admitted_invitation_hash")
    op.drop_column("interview_sessions", "candidate_admitted_at")
