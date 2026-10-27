"""Add recruiter-owned question bank documents."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e4b8c2907a15"
down_revision = "a31f920bc876"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "question_banks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recruiter_id", sa.Uuid(), nullable=False),

        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),

        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),

        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("index_version", sa.String(100), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False),

        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),

        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["recruiter_id"], ["users.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "recruiter_id", "file_sha256",
            name="uq_question_banks_recruiter_file",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_question_banks_status",
        ),
        sa.CheckConstraint(
            "file_size_bytes > 0", name="ck_question_banks_positive_size",
        ),
    )
    op.create_index(
        "ix_question_banks_recruiter_id", "question_banks", ["recruiter_id"],
    )


def downgrade():
    op.drop_table("question_banks")
