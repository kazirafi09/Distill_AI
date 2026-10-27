"""Save recruiter-confirmed final candidates and reviewed score snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c9f235e71b40"
down_revision = "b8e124d60a39"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("final_candidate_selections",
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidates", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    op.drop_table("final_candidate_selections")
