"""Add isolated F11 diagnostic storage; existing feature tables are unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "f6d921e3c845"
down_revision = "f4b830d927ae"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("wind_tunnel_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("analysis_version", sa.String(60), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column("versions", JSONB(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("findings", JSONB(), nullable=False),
        sa.Column("resources", JSONB(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "request_key", name="uq_wind_tunnel_request"),
        sa.CheckConstraint("trigger IN ('approval', 'more')", name="ck_wind_tunnel_trigger"),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'completed_with_issues', 'failed', 'deleting', 'cleanup_failed')", name="ck_wind_tunnel_status"))
    op.create_table("wind_tunnel_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("wind_tunnel_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("persona_id", sa.String(60), nullable=False),
        sa.Column("suffix", sa.String(5), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "persona_id", name="uq_wind_tunnel_persona"),
        sa.UniqueConstraint("run_id", "id", name="uq_wind_tunnel_document_scope"),
        sa.CheckConstraint("suffix IN ('.pdf', '.docx')", name="ck_wind_tunnel_document_type"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_wind_tunnel_document_size"))
    op.create_table("wind_tunnel_assessments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("wind_tunnel_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("repetition", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id", "document_id"], ["wind_tunnel_documents.run_id", "wind_tunnel_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "phase", "repetition", name="uq_wind_tunnel_replicate"),
        sa.CheckConstraint("phase IN ('baseline', 'proposed') AND repetition BETWEEN 1 AND 3", name="ck_wind_tunnel_replicate"),
        sa.CheckConstraint("revision >= 0", name="ck_wind_tunnel_revision"))
    op.create_table("wind_tunnel_suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("wind_tunnel_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_key", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "proposal_key", name="uq_wind_tunnel_proposal"),
        sa.CheckConstraint("status IN ('ready', 'applied', 'stale')", name="ck_wind_tunnel_suggestion_status"))
    for table, columns in {"wind_tunnel_runs": ("owner_id", "job_id"), "wind_tunnel_documents": ("run_id",),
                            "wind_tunnel_assessments": ("run_id",), "wind_tunnel_suggestions": ("run_id",)}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ("wind_tunnel_suggestions", "wind_tunnel_assessments", "wind_tunnel_documents", "wind_tunnel_runs"):
        op.drop_table(table)
