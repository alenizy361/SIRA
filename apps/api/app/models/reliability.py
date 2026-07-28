import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import OrgScopedMixin, uuid_pk


class Incident(Base, OrgScopedMixin):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # sev1..sev4
    state: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
    opened_by: Mapped[str] = mapped_column(String(150), nullable=False)  # agent_key or user id
    related_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    actor: Mapped[str] = mapped_column(String(150), nullable=False)
    action: Mapped[str] = mapped_column(String(150), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Alert(Base, OrgScopedMixin):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = uuid_pk()
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class HealthCheck(Base):
    __tablename__ = "health_checks"

    id: Mapped[uuid.UUID] = uuid_pk()
    component: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pass|warn|fail
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SystemMetric(Base):
    __tablename__ = "system_metrics"

    id: Mapped[uuid.UUID] = uuid_pk()
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    labels: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Backup(Base, OrgScopedMixin):
    __tablename__ = "backups"

    id: Mapped[uuid.UUID] = uuid_pk()
    location: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # database|config|full
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RestoreTest(Base, OrgScopedMixin):
    __tablename__ = "restore_tests"

    id: Mapped[uuid.UUID] = uuid_pk()
    backup_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("backups.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # pass|fail
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
