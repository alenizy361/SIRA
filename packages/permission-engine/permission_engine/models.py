"""Typed inputs/outputs for the permission engine.

Kept dependency-free (pydantic only) so it can be imported by apps/api,
services/orchestrator, and services/claude-worker without pulling in a web
framework.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

import sys
from pathlib import Path

# packages/contracts lives one level up in the monorepo; add it to sys.path
# so this package works both as an editable install and when imported
# directly from a checkout (as apps/api does today).
_PACKAGES_ROOT = Path(__file__).resolve().parents[2]
if str(_PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_ROOT))

from contracts.risk_levels import RiskLevel, HARD_PROHIBITED_ACTIONS  # noqa: E402


class PermissionOutcome(str, Enum):
    AUTO_APPROVED = "auto_approved"
    APPROVAL_REQUIRED = "approval_required"
    REJECTED = "rejected"


class AgentPermissionProfile(BaseModel):
    """The subset of an agent-spec YAML file (packages/agent-specs/*.yaml)
    relevant to a single permission decision."""

    agent_id: str
    risk_ceiling: RiskLevel
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    budget_daily_max_sar: Optional[float] = None
    budget_monthly_max_sar: Optional[float] = None
    enabled: bool = True


class ActionRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    organization_id: str
    actor_id: str
    actor_type: str  # "agent" | "user" | "service_account"
    action: str
    tool: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    risk_level: RiskLevel
    reversible: bool = True
    validation_passed: bool = False
    rollback_available: bool = False
    evidence: list[str] = Field(default_factory=list)
    estimated_cost_sar: float = 0.0
    cumulative_spent_today_sar: float = 0.0
    cumulative_spent_month_sar: float = 0.0
    incident_active: bool = False
    is_incident_response_action: bool = False
    reviewer_assigned: bool = False
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PermissionDecision(BaseModel):
    request_id: str
    outcome: PermissionOutcome
    risk_level: RiskLevel
    reason: str
    required_reviewers: list[str] = Field(default_factory=list)
    blocking_conditions: list[str] = Field(default_factory=list)
    policy_version: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_audit_record(self, request: ActionRequest) -> dict:
        """Shape stored in audit_logs - inputs + result + explanation, per
        constitution section 10 ("every decision must be stored with policy
        version, inputs, result, and explanation")."""
        return {
            "request": request.model_dump(mode="json"),
            "decision": self.model_dump(mode="json"),
        }
