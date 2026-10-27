"""add stable job description numbers

Revision ID: 7044535ec51f
Revises: 092b6997f617
Create Date: 2026-10-07 01:44:29.198490

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7044535ec51f'
down_revision: Union[str, Sequence[str], None] = '092b6997f617'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "job_descriptions",
        sa.Column(
            "number",
            sa.BigInteger(),
            sa.Identity(always=False, start=1),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_job_descriptions_number",
        "job_descriptions",
        ["number"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_job_descriptions_number",
        "job_descriptions",
        type_="unique",
    )
    op.drop_column("job_descriptions", "number")
