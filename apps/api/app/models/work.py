import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import OrgScopedMixin, TimestampMixin, uuid_pk


class Plan(Base, OrgScopedMixin):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(40), default="drafted", nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class PlanStep(Base, TimestampMixin):
    __tablename__ = "plan_steps"

    id: Mapped[uuid.UUID] = uuid_pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    assigned_agent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Task(Base, OrgScopedMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"), nullable=True)
    plan_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("plan_steps.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_agent_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    risk_level: Mapped[str] = mapped_column(String(4), default="R1", nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="ready", nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800, nullable=False)
    stop_conditions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    acceptance_criteria: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    workspace_repo: Mapped[str | None] = mapped_column(String(300), nullable=True)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)


class TaskAttempt(Base, TimestampMixin):
    __tablename__ = "task_attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Run(Base, OrgScopedMixin):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    task_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("task_attempts.id"), nullable=True)
    agent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    worker: Mapped[str] = mapped_column(String(64), default="claude-worker", nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="assigned", nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    baseline_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The CLI's own reported spend for this run (its stream-json `result`
    # message carries `total_cost_usd`) - real dollars, not an estimate. Null
    # until the run finishes; a failed/timed-out run can still have incurred
    # real cost, so this is set independently of run.state.
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)


class RunLease(Base):
    """Guarantees at-most-one active worker per task (constitution section 6)."""

    __tablename__ = "run_leases"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), unique=True, nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(150), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    output_summary: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class Artifact(Base, OrgScopedMixin):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)  # diff|screenshot|test_report|log|other
    path_or_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Validation(Base, TimestampMixin):
    __tablename__ = "validations"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False)
    check_name: Mapped[str] = mapped_column(String(150), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class Review(Base, OrgScopedMixin):
    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=True)
    reviewer_agent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # approve|request_changes|reject
    reasons: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class RetryRecord(Base):
    __tablename__ = "retry_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Cancellation(Base):
    __tablename__ = "cancellations"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=True)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
