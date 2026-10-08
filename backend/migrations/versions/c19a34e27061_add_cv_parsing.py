"""Versioned CV extraction and parsing; download state remains separate."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "c19a34e27061"
down_revision = "b83c1d6e902a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cv_parse_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("file_version_id", sa.Uuid(), sa.ForeignKey("cv_file_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_key", sa.String(64), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("versions", JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("sources", JSONB(), nullable=False),
        sa.Column("extraction_method", sa.String(30)),
        sa.Column("warnings", JSONB(), nullable=False),
        sa.Column("profile", JSONB()),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("file_version_id", "processing_key", name="uq_cv_parse_version_processing"),
        sa.CheckConstraint("status IN ('pending','verifying','extracting','ocr','text_saved','parsing','validating','completed','needs_review','failed')", name="ck_cv_parse_status"),
    )
    op.create_index("ix_cv_parse_results_file_version_id", "cv_parse_results", ["file_version_id"])


def downgrade():
    op.drop_table("cv_parse_results")
