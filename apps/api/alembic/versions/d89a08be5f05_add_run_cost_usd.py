"""add run cost_usd

Revision ID: d89a08be5f05
Revises: 46e5488156cd
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd89a08be5f05'
down_revision: Union[str, Sequence[str], None] = '46e5488156cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('runs', sa.Column('cost_usd', sa.Numeric(10, 4), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('runs', 'cost_usd')
