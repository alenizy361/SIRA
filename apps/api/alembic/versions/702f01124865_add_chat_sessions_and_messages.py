"""add chat_sessions and chat_messages

Revision ID: 702f01124865
Revises: ee023a5c1a23
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '702f01124865'
down_revision: Union[str, Sequence[str], None] = 'ee023a5c1a23'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'chat_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False, unique=True),
        sa.Column('cli_session_id', sa.String(length=100), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_by', sa.String(length=150), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_chat_sessions_organization_id', 'chat_sessions', ['organization_id'])

    op.create_table(
        'chat_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('chat_session_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('chat_sessions.id'), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_chat_messages_chat_session_id', 'chat_messages', ['chat_session_id'])
    op.create_index('ix_chat_messages_organization_id', 'chat_messages', ['organization_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chat_messages_organization_id', table_name='chat_messages')
    op.drop_index('ix_chat_messages_chat_session_id', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('ix_chat_sessions_organization_id', table_name='chat_sessions')
    op.drop_table('chat_sessions')
