"""Add response sheet mappings, candidates and job applications.

Revision ID: a74d2bf5c901
Revises: 5974ab54cb1b
"""
from alembic import op
import sqlalchemy as sa

revision = "a74d2bf5c901"
down_revision = "5974ab54cb1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cv_sources", sa.Column("spreadsheet_id", sa.String(255), nullable=True))
    op.add_column("cv_sources", sa.Column("sheet_tab_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_cv_sources_sheet_tab", "cv_sources", ["spreadsheet_id", "sheet_tab_id"])
    op.create_check_constraint(
        "ck_cv_sources_sheet_mapping", "cv_sources",
        "(spreadsheet_id IS NULL AND sheet_tab_id IS NULL) OR "
        "(spreadsheet_id IS NOT NULL AND sheet_tab_id IS NOT NULL AND sheet_tab_id >= 0)",
    )
    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recruiter_id", sa.Uuid(), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["recruiter_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recruiter_id", "email", name="uq_candidates_recruiter_email"),
    )
    op.create_table(
        "candidate_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("job_description_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("submitted_at_text", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_description_id"], ["job_descriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["cv_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("candidate_id", "job_description_id", name="uq_candidate_applications_candidate_job"),
    )
    op.create_index("ix_candidate_applications_job_description_id", "candidate_applications", ["job_description_id"])
    op.add_column("cv_documents", sa.Column("application_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_cv_documents_application", "cv_documents", "candidate_applications", ["application_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_cv_documents_application_id", "cv_documents", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_cv_documents_application_id", table_name="cv_documents")
    op.drop_constraint("fk_cv_documents_application", "cv_documents", type_="foreignkey")
    op.drop_column("cv_documents", "application_id")
    op.drop_index("ix_candidate_applications_job_description_id", table_name="candidate_applications")
    op.drop_table("candidate_applications")
    op.drop_table("candidates")
    op.drop_constraint("ck_cv_sources_sheet_mapping", "cv_sources", type_="check")
    op.drop_constraint("uq_cv_sources_sheet_tab", "cv_sources", type_="unique")
    op.drop_column("cv_sources", "sheet_tab_id")
    op.drop_column("cv_sources", "spreadsheet_id")
