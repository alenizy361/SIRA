"""Core risk/permission evaluation logic.

This module enforces constitution rules #10 ("no agent may grant itself
additional permissions"), #11 ("no self-improvement task may weaken security"),
and section 10 (risk levels R0-R5). It is deliberately pure/synchronous and
side-effect free: callers persist the returned PermissionDecision themselves
(apps/api writes it to the audit_logs table).
"""
from __future__ import annotations

import sys
from pathlib import Path

_PACKAGES_ROOT = Path(__file__).resolve().parents[2]
if str(_PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_ROOT))

from contracts.risk_levels import (  # noqa: E402
    RiskLevel,
    HARD_PROHIBITED_ACTIONS,
    risk_index,
)

from .models import (
    ActionRequest,
    AgentPermissionProfile,
    PermissionDecision,
    PermissionOutcome,
)

POLICY_VERSION = "policy-2026.07.28-v1"


class PermissionEngine:
    """Evaluates a single ActionRequest against an AgentPermissionProfile.

    Usage:
        engine = PermissionEngine()
        decision = engine.evaluate(request, profile)
    """

    def __init__(self, policy_version: str = POLICY_VERSION) -> None:
        self.policy_version = policy_version

    def evaluate(
        self, request: ActionRequest, profile: AgentPermissionProfile
    ) -> PermissionDecision:
        reject = self._reject_if(request, profile)
        if reject is not None:
            return reject

        escalate = self._require_approval_if(request, profile)
        if escalate is not None:
            return escalate

        # R0/R1 always auto-approve once no reject/escalation condition fired.
        if request.risk_level in (RiskLevel.R0, RiskLevel.R1):
            return self._decision(
                request,
                PermissionOutcome.AUTO_APPROVED,
                "Risk level within agent's automatic-execution range.",
            )

        # R2 auto-approves only when validated, reversible, rollback-ready,
        # and within tool/action allowlists (already checked above).
        if request.risk_level == RiskLevel.R2:
            missing = []
            if not request.reversible:
                missing.append("action is not reversible")
            if not request.validation_passed:
                missing.append("validation has not passed")
            if not request.rollback_available:
                missing.append("no rollback path available")
            if missing:
                return self._decision(
                    request,
                    PermissionOutcome.APPROVAL_REQUIRED,
                    "R2 action failed automatic-execution conditions: "
                    + "; ".join(missing),
                    blocking_conditions=missing,
                )
            return self._decision(
                request,
                PermissionOutcome.AUTO_APPROVED,
                "R2 action passed validation/reversibility/rollback checks.",
            )

        # Should be unreachable - R3/R4/R5 handled in _reject_if /
        # _require_approval_if, but fail safe rather than fail open.
        return self._decision(
            request,
            PermissionOutcome.REJECTED,
            "Unhandled risk level; failing safe.",
        )

    # -- internal helpers -------------------------------------------------

    def _reject_if(
        self, request: ActionRequest, profile: AgentPermissionProfile
    ) -> PermissionDecision | None:
        if request.action in HARD_PROHIBITED_ACTIONS:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                f"Action '{request.action}' is in the hard-prohibited (R5) list "
                "and cannot be authorized by any policy version.",
            )

        if request.risk_level == RiskLevel.R5:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                "Risk level R5 is always prohibited.",
            )

        if not profile.enabled:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                f"Agent '{profile.agent_id}' is disabled (integration not configured).",
            )

        if request.action in profile.forbidden_actions:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                f"Action '{request.action}' is explicitly forbidden for agent "
                f"'{profile.agent_id}'.",
            )

        if request.tool and request.tool in profile.denied_tools:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                f"Tool '{request.tool}' is explicitly denied for agent "
                f"'{profile.agent_id}'.",
            )

        if request.tool and profile.allowed_tools and request.tool not in profile.allowed_tools:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                f"Tool '{request.tool}' is not in agent "
                f"'{profile.agent_id}''s allowed_tools.",
            )

        if (
            profile.allowed_actions
            and request.action not in profile.allowed_actions
        ):
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                f"Action '{request.action}' is not in agent "
                f"'{profile.agent_id}''s allowed_actions.",
            )

        budget_violation = self._budget_violation(request, profile)
        if budget_violation:
            return self._decision(
                request,
                PermissionOutcome.REJECTED,
                budget_violation,
            )

        return None

    def _require_approval_if(
        self, request: ActionRequest, profile: AgentPermissionProfile
    ) -> PermissionDecision | None:
        # An agent may never auto-execute above its own risk ceiling, even if
        # the action/tool allowlists would otherwise permit it - this is the
        # code-level enforcement backing "no self-improvement task may weaken
        # ... permission policies" (constitution rule #11).
        if risk_index(request.risk_level) > risk_index(profile.risk_ceiling):
            return self._decision(
                request,
                PermissionOutcome.APPROVAL_REQUIRED,
                f"Requested risk level {request.risk_level.value} exceeds agent "
                f"'{profile.agent_id}''s ceiling of {profile.risk_ceiling.value}.",
                required_reviewers=["human_administrator"],
            )

        if request.incident_active and not request.is_incident_response_action:
            return self._decision(
                request,
                PermissionOutcome.APPROVAL_REQUIRED,
                "An incident is active; only incident-response actions may "
                "proceed without approval (emergency-stop policy).",
                required_reviewers=["incident_commander"],
            )

        if request.risk_level == RiskLevel.R3:
            return self._decision(
                request,
                PermissionOutcome.APPROVAL_REQUIRED,
                "R3 (material business impact) always requires human approval.",
                required_reviewers=["human_administrator"],
            )

        if request.risk_level == RiskLevel.R4:
            missing = []
            if not request.reviewer_assigned:
                missing.append("independent reviewer not yet assigned")
            if not request.rollback_available:
                missing.append("no verified rollback plan")
            reason = "R4 (high-risk/irreversible) requires human approval plus independent review and a verified rollback plan."
            if missing:
                reason += " Currently missing: " + "; ".join(missing)
            return self._decision(
                request,
                PermissionOutcome.APPROVAL_REQUIRED,
                reason,
                required_reviewers=["human_administrator", "independent_reviewer"],
                blocking_conditions=missing,
            )

        return None

    def _budget_violation(
        self, request: ActionRequest, profile: AgentPermissionProfile
    ) -> str | None:
        if profile.budget_daily_max_sar is not None:
            projected_daily = request.cumulative_spent_today_sar + request.estimated_cost_sar
            if projected_daily > profile.budget_daily_max_sar:
                return (
                    f"Projected daily spend {projected_daily:.2f} SAR exceeds agent "
                    f"'{profile.agent_id}''s daily budget ceiling of "
                    f"{profile.budget_daily_max_sar:.2f} SAR."
                )
        if profile.budget_monthly_max_sar is not None:
            projected_monthly = request.cumulative_spent_month_sar + request.estimated_cost_sar
            if projected_monthly > profile.budget_monthly_max_sar:
                return (
                    f"Projected monthly spend {projected_monthly:.2f} SAR exceeds agent "
                    f"'{profile.agent_id}''s monthly budget ceiling of "
                    f"{profile.budget_monthly_max_sar:.2f} SAR."
                )
        return None

    def _decision(
        self,
        request: ActionRequest,
        outcome: PermissionOutcome,
        reason: str,
        required_reviewers: list[str] | None = None,
        blocking_conditions: list[str] | None = None,
    ) -> PermissionDecision:
        return PermissionDecision(
            request_id=request.request_id,
            outcome=outcome,
            risk_level=request.risk_level,
            reason=reason,
            required_reviewers=required_reviewers or [],
            blocking_conditions=blocking_conditions or [],
            policy_version=self.policy_version,
        )
