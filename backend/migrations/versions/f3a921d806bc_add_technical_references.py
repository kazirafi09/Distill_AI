"""Allow technical reference documents for interview accuracy evaluation."""
from alembic import op

revision = "f3a921d806bc"
down_revision = "d93a5c702816"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_jd_references_document_type", "jd_references", type_="check")
    op.create_check_constraint("ck_jd_references_document_type", "jd_references",
        "document_type IN ('prior_jd', 'company_policy', 'technical_reference')")


def downgrade():
    # Preserve documents: downgrade is blocked until technical references are reclassified.
    op.drop_constraint("ck_jd_references_document_type", "jd_references", type_="check")
    op.create_check_constraint("ck_jd_references_document_type", "jd_references",
        "document_type IN ('prior_jd', 'company_policy')")
