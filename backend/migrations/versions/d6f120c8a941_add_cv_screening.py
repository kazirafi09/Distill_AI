"""Durable CV indexing, screening runs and reproducible assessments."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d6f120c8a941"
down_revision = "c19a34e27061"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("cv_indexes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parse_result_id", sa.Uuid(), sa.ForeignKey("cv_parse_results.id", ondelete="CASCADE"), nullable=False),
        sa.Column("processing_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("versions", JSONB(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("parse_result_id", "processing_key", name="uq_cv_index_processing"))
    op.create_index("ix_cv_indexes_parse_result_id", "cv_indexes", ["parse_result_id"])
    op.create_table("cv_screening_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recruiter_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("input_key", sa.String(64), nullable=False),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("warnings", JSONB(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for field in ("job_id", "recruiter_id"):
        op.create_index(f"ix_cv_screening_runs_{field}", "cv_screening_runs", [field])
    op.create_table("cv_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("cv_screening_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("candidate_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_version_ids", JSONB(), nullable=False),
        sa.Column("parse_result_ids", JSONB(), nullable=False),
        sa.Column("index_ids", JSONB(), nullable=False),
        sa.Column("processing_key", sa.String(64)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("matches", JSONB(), nullable=False),
        sa.Column("score", JSONB()),
        sa.Column("warnings", JSONB(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("reused_from", sa.String(36)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "application_id", name="uq_cv_assessment_run_application"))
    for field in ("run_id", "application_id", "processing_key"):
        op.create_index(f"ix_cv_assessments_{field}", "cv_assessments", [field])


def downgrade():
    op.drop_table("cv_assessments")
    op.drop_table("cv_screening_runs")
    op.drop_table("cv_indexes")
