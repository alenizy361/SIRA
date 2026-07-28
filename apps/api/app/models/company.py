import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import OrgScopedMixin, uuid_pk


class CompanyProfile(Base, OrgScopedMixin):
    __tablename__ = "company_profiles"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    industry: Mapped[str | None] = mapped_column(String(150), nullable=True)
    primary_product: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Mission(Base, OrgScopedMixin):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = uuid_pk()
    statement: Mapped[str] = mapped_column(Text, nullable=False)


class Strategy(Base, OrgScopedMixin):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = uuid_pk()
    mission_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("missions.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_horizon: Mapped[str] = mapped_column(String(32), default="quarterly", nullable=False)


class Objective(Base, OrgScopedMixin):
    __tablename__ = "objectives"

    id: Mapped[uuid.UUID] = uuid_pk()
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("strategies.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class KeyResult(Base, OrgScopedMixin):
    __tablename__ = "key_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    objective_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("objectives.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    target_value: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    current_value: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Goal(Base, OrgScopedMixin):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = uuid_pk()
    objective_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("objectives.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="user", nullable=False)  # user|voice|scheduled|agent
    priority: Mapped[int] = mapped_column(default=3, nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="goal_captured", nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    acceptance_criteria: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class GoalDependency(Base):
    __tablename__ = "goal_dependencies"

    id: Mapped[uuid.UUID] = uuid_pk()
    goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False)
    depends_on_goal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("goals.id"), nullable=False)
