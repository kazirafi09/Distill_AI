"""Enable independent interview extensions and append-only finding reviews."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "ca9174e803b2"
down_revision = "c9a810b4d237"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_assessment_run_kind", "assessment_runs", type_="check")
    op.create_check_constraint("ck_assessment_run_kind", "assessment_runs", "kind IN ('f5', 'f6', 'f8', 'f9', 'f10')")
    op.create_table("assessment_finding_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("assessment_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finding_id", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_assessment_finding_reviews_run_id", "assessment_finding_reviews", ["run_id"])


def downgrade():
    # Keep the database's check constraint as the guard against dropping populated feature support.
    op.drop_constraint("ck_assessment_run_kind", "assessment_runs", type_="check")
    op.create_check_constraint("ck_assessment_run_kind", "assessment_runs", "kind IN ('f5', 'f6')")
    op.drop_index("ix_assessment_finding_reviews_run_id", table_name="assessment_finding_reviews")
    op.drop_table("assessment_finding_reviews")
