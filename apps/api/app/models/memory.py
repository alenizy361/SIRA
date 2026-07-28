import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import OrgScopedMixin, TimestampMixin, uuid_pk


class MemoryItem(Base, OrgScopedMixin):
    """A single durable memory record (episodic/semantic/policy/artifact -
    working memory is task-local and lives in Redis, not this table)."""

    __tablename__ = "memory_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    layer: Mapped[str] = mapped_column(String(32), nullable=False)  # episodic|semantic|policy|artifact
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(300), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), default=0.5, nullable=False)
    freshness_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner_agent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    retention_class: Mapped[str] = mapped_column(String(32), default="standard", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contradiction_flag: Mapped[bool] = mapped_column(default=False, nullable=False)


class MemoryLink(Base):
    __tablename__ = "memory_links"

    id: Mapped[uuid.UUID] = uuid_pk()
    memory_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("memory_items.id"), nullable=False)
    linked_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)  # task|decision|incident|artifact
    linked_entity_id: Mapped[str] = mapped_column(String(150), nullable=False)


class Decision(Base, OrgScopedMixin):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_agent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    related_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)


class Lesson(Base, OrgScopedMixin):
    __tablename__ = "lessons"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    what_happened: Mapped[str] = mapped_column(Text, nullable=False)
    what_to_do_differently: Mapped[str] = mapped_column(Text, nullable=False)
    related_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)
    related_incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class KnowledgeSource(Base, OrgScopedMixin):
    __tablename__ = "knowledge_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # repo|doc|analytics|manual
    reference: Mapped[str] = mapped_column(String(500), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), default="unverified", nullable=False)


class Summary(Base, OrgScopedMixin):
    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = uuid_pk()
    period: Mapped[str] = mapped_column(String(16), nullable=False)  # daily|weekly|monthly|quarterly
    agent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class RetentionRule(Base, OrgScopedMixin):
    __tablename__ = "retention_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    retention_class: Mapped[str] = mapped_column(String(32), nullable=False)
    max_age_days: Mapped[int] = mapped_column(nullable=False)
    applies_to_layer: Mapped[str] = mapped_column(String(32), nullable=False)
