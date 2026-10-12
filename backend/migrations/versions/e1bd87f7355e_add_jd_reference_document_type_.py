"""add JD reference document type constraint

Revision ID: e1bd87f7355e
Revises: c6983935e220
Create Date: 2026-10-05 14:00:40.983090

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1bd87f7355e'
down_revision: Union[str, Sequence[str], None] = 'c6983935e220'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_jd_references_document_type",
        "jd_references",
        "document_type IN ('prior_jd', 'company_policy')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_jd_references_document_type",
        "jd_references",
        type_="check",
    )
