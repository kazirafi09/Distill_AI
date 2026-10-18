"""Allow separate CV submissions to share a candidate email address."""

from alembic import op

revision = "9b6d24a8e031"
down_revision = "f5c810d2b934"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_candidates_recruiter_email", "candidates", type_="unique")


def downgrade() -> None:
    # PostgreSQL rejects this rollback if shared emails exist, preserving records
    # rather than silently merging or deleting candidates and their applications.
    op.create_unique_constraint("uq_candidates_recruiter_email", "candidates", ["recruiter_id", "email"])
