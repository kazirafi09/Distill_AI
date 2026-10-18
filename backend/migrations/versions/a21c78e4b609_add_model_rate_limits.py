"""Share Gemini request pacing between backend processes."""
from alembic import op
import sqlalchemy as sa

revision = "a21c78e4b609"
down_revision = "9b6d24a8e031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_rate_limits",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("next_request_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("model_rate_limits")
