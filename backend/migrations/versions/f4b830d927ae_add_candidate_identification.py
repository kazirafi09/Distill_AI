"""Flexible Form fields and application-scoped face verification."""
from alembic import op
import sqlalchemy as sa

revision = "f4b830d927ae"
down_revision = "cb0472a19e36"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cv_sources", sa.Column("field_mapping", sa.JSON(), nullable=True))
    op.create_table("candidate_identities",
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("reference_file_id", sa.String(255)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent", sa.String(20), nullable=False),
        sa.Column("consent_version", sa.String(40)),
        sa.Column("consent_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(60)),
        sa.Column("similarity", sa.Float()),
        sa.Column("threshold", sa.Float()),
        sa.Column("model_version", sa.String(200)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
    )
    op.create_index("ix_candidate_identities_expires_at", "candidate_identities", ["expires_at"])


def downgrade():
    op.drop_table("candidate_identities")
    op.drop_column("cv_sources", "field_mapping")
