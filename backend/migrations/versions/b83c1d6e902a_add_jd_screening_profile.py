"""Persist accepted JD screening profiles without changing legacy JDs.

Revision ID: b83c1d6e902a
Revises: a74d2bf5c901
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b83c1d6e902a"
down_revision = "a74d2bf5c901"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("job_descriptions", sa.Column("screening_profile", postgresql.JSONB(), nullable=True))
    op.add_column("job_descriptions", sa.Column("profile_version", sa.Integer(), nullable=True))
    op.add_column("job_descriptions", sa.Column("google_form_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("job_descriptions", "google_form_url")
    op.drop_column("job_descriptions", "profile_version")
    op.drop_column("job_descriptions", "screening_profile")
