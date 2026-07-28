"""add budget warn threshold

Revision ID: 9b30b392126b
Revises: d89a08be5f05
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b30b392126b'
down_revision: Union[str, Sequence[str], None] = 'd89a08be5f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('budgets', sa.Column('warn_percent', sa.Numeric(5, 2), nullable=False, server_default='80'))
    op.add_column('budgets', sa.Column('warned_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('budgets', 'warned_at')
    op.drop_column('budgets', 'warn_percent')
