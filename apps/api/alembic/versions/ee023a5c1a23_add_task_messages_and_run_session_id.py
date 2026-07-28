"""add task_messages and run cli_session_id

Revision ID: ee023a5c1a23
Revises: 9b30b392126b
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ee023a5c1a23'
down_revision: Union[str, Sequence[str], None] = '9b30b392126b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('runs', sa.Column('cli_session_id', sa.String(length=100), nullable=True))

    op.create_table(
        'task_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tasks.id'), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('runs.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_task_messages_task_id', 'task_messages', ['task_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_task_messages_task_id', table_name='task_messages')
    op.drop_table('task_messages')
    op.drop_column('runs', 'cli_session_id')
