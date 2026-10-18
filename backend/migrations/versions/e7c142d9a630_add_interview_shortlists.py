"""Add saved interviewee selections."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "e7c142d9a630"
down_revision = "d6f120c8a941"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "interview_shortlists",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "job_description_id",
            sa.Uuid(),
            sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recruiter_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "screening_run_id",
            sa.Uuid(),
            sa.ForeignKey("cv_screening_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filter_inputs", JSONB(), nullable=False),
        sa.Column("proposed_application_ids", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("job_description_id"),
    )
    op.create_index(
        "ix_interview_shortlists_recruiter_id",
        "interview_shortlists",
        ["recruiter_id"],
    )

    op.create_table(
        "interview_shortlist_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "shortlist_id",
            sa.Uuid(),
            sa.ForeignKey("interview_shortlists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assessment_id",
            sa.Uuid(),
            sa.ForeignKey("cv_assessments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score_snapshot", JSONB(), nullable=False),
        sa.Column("rank_snapshot", sa.Integer(), nullable=False),
        sa.Column("manually_added", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "shortlist_id",
            "application_id",
            name="uq_interview_shortlist_application",
        ),
    )
    op.create_index(
        "ix_interview_shortlist_members_shortlist_id",
        "interview_shortlist_members",
        ["shortlist_id"],
    )
    op.create_index(
        "ix_interview_shortlist_members_application_id",
        "interview_shortlist_members",
        ["application_id"],
    )


def downgrade():
    op.drop_table("interview_shortlist_members")
    op.drop_table("interview_shortlists")
