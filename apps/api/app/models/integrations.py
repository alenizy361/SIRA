import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import OrgScopedMixin, TimestampMixin, uuid_pk


class Integration(Base, OrgScopedMixin):
    """One row per integration TYPE (github, vercel, sentry, ...). Whether it
    is actually usable is derived from having an active IntegrationAccount
    with valid credential_metadata - the UI must never show "connected" for
    a row that only exists as a placeholder."""

    __tablename__ = "integrations"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False)
    configured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_agent_keys: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)


class IntegrationAccount(Base, OrgScopedMixin):
    __tablename__ = "integration_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("integrations.id"), nullable=False)
    external_account_ref: Mapped[str] = mapped_column(String(300), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CredentialMetadata(Base, OrgScopedMixin):
    """Metadata ONLY - never the raw secret value. Raw secrets live in OS
    keychain / environment, never in this database or in git."""

    __tablename__ = "credential_metadata"

    id: Mapped[uuid.UUID] = uuid_pk()
    integration_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integration_accounts.id"), nullable=False
    )
    credential_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_location: Mapped[str] = mapped_column(String(150), nullable=False)  # e.g. "os_env:GITHUB_TOKEN"
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Budget(Base, OrgScopedMixin):
    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(64), nullable=False)  # agent|project|org
    scope_ref: Mapped[str] = mapped_column(String(150), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="SAR", nullable=False)
    daily_max: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    monthly_max: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    reserved_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    spent_amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    # Fraction of monthly_max (0-100) at which a BUDGET_THRESHOLD_REACHED
    # warning fires - before the hard stop, not instead of it.
    warn_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=80, nullable=False)
    # Set the first time this budget crosses warn_percent, cleared whenever
    # the cap is (re)configured - a fresh cap deserves a fresh warning cycle
    # rather than staying silent forever because of a stale timestamp.
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BudgetTransaction(Base):
    __tablename__ = "budget_transactions"

    id: Mapped[uuid.UUID] = uuid_pk()
    budget_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("budgets.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # reserve|commit|release
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PurchaseRequest(Base, OrgScopedMixin):
    __tablename__ = "purchase_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    requested_by_agent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_sar: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class CampaignChange(Base, OrgScopedMixin):
    __tablename__ = "campaign_changes"

    id: Mapped[uuid.UUID] = uuid_pk()
    campaign_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    change_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    delta_amount_sar: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    requested_by_agent_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
