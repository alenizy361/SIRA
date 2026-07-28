import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import OrgScopedMixin, TimestampMixin, uuid_pk


class AgentDefinition(Base, TimestampMixin):
    """One row per packages/agent-specs/*.yaml file, loaded+validated at
    startup by app.agents.loader. Global (not org-scoped): the contract is
    the same everywhere; org-scoped enable/disable + budgets live on
    AgentInstance."""

    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # e.g. "ceo"
    display_name_en: Mapped[str] = mapped_column(String(150), nullable=False)
    display_name_ar: Mapped[str] = mapped_column(String(150), nullable=False)
    mission: Mapped[str] = mapped_column(String(2000), nullable=False)
    risk_ceiling: Mapped[str] = mapped_column(String(4), nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    spec_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)


class AgentInstance(Base, OrgScopedMixin):
    """A definition activated for a specific organization."""

    __tablename__ = "agent_instances"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    current_state: Mapped[str] = mapped_column(String(40), default="idle", nullable=False)


class AgentCapability(Base, TimestampMixin):
    __tablename__ = "agent_capabilities"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False
    )
    capability: Mapped[str] = mapped_column(String(150), nullable=False)


class AgentToolGrant(Base, OrgScopedMixin):
    __tablename__ = "agent_tool_grants"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_instances.id"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentBudgetLimit(Base, OrgScopedMixin):
    __tablename__ = "agent_budget_limits"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_instances.id"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), default="SAR", nullable=False)
    daily_max: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    monthly_max: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    concurrency_max: Mapped[int] = mapped_column(default=1, nullable=False)


class AgentHealth(Base):
    __tablename__ = "agent_health"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_instances.id"), nullable=False
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class AgentPerformanceMetric(Base):
    __tablename__ = "agent_performance_metrics"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_instances.id"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tasks_completed: Mapped[int] = mapped_column(default=0, nullable=False)
    tasks_failed: Mapped[int] = mapped_column(default=0, nullable=False)
    rework_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    avg_duration_seconds: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
