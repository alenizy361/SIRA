"""A lightweight, always-available conversational surface - deliberately
separate from the Goal -> Plan -> Task pipeline. A casual message ("hi",
"what's your favorite color") should never spin up a full CEO planning run
and a multi-agent task tree; it should just get a fast, cheap reply from one
persona. See claude_worker.worker's _pending_chat_requests/_execute_chat_turn
for the execution side, which uses no_tools=True and a fast/cheap model
(claude_worker.worker.CHAT_MODEL) rather than the task pipeline's defaults."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import uuid_pk


class ChatSession(Base):
    """One persistent casual-chat thread per organization (v1: exactly one -
    the "always-available chat box" is a single ongoing conversation, not a
    list of chats to manage). cli_session_id lets a follow-up message
    --resume the same CLI session, same pattern as TaskMessage/Run for the
    task-reply feature, but with no Task/Goal/Plan involved at all."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, unique=True, index=True
    )
    cli_session_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Minimal claim/lease so two poll ticks (or a slow-running turn plus a
    # new tick) never process the same session's pending message twice -
    # simpler than the full RunLease table since chat has no Task/Run to
    # anchor a lease to. A claim older than CHAT_CLAIM_TTL_SECONDS (see
    # worker.py) is treated as abandoned and reclaimable.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    chat_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "human" | "agent"
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
