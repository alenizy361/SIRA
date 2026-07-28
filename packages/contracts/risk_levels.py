"""Canonical risk-level definitions shared by the permission engine, orchestrator, and API.

Single source of truth: every service that needs to reason about risk imports this
module (or the mirrored TypeScript copy in risk_levels.ts) instead of redefining levels.
"""
from enum import Enum


class RiskLevel(str, Enum):
    R0 = "R0"  # read-only
    R1 = "R1"  # low-risk reversible
    R2 = "R2"  # controlled reversible
    R3 = "R3"  # material business impact - human approval required
    R4 = "R4"  # high-risk / irreversible - approval + independent review + rollback plan
    R5 = "R5"  # prohibited - always rejected


# Ordering used for comparisons (e.g. "is this action at least R3?")
RISK_ORDER = [RiskLevel.R0, RiskLevel.R1, RiskLevel.R2, RiskLevel.R3, RiskLevel.R4, RiskLevel.R5]


def risk_index(level: RiskLevel) -> int:
    return RISK_ORDER.index(level)


def at_least(level: RiskLevel, floor: RiskLevel) -> bool:
    return risk_index(level) >= risk_index(floor)


DEFAULT_AUTO_APPROVE_CEILING = RiskLevel.R2
APPROVAL_REQUIRED_FROM = RiskLevel.R3
REVIEWER_REQUIRED_FROM = RiskLevel.R4
ALWAYS_REJECTED = RiskLevel.R5

# Actions that are R5 (prohibited) regardless of actor, policy, or configuration.
# These cannot be overridden by any policy_version - enforced in code per constitution rule #11.
HARD_PROHIBITED_ACTIONS = {
    "self.grant_permission",
    "policy.disable_audit_log",
    "policy.bypass_approval",
    "credential.expose_raw_secret",
    "finance.transfer_money",
    "finance.take_loan",
    "finance.make_investment",
    "org.transfer_ownership",
    "org.change_authentication_root",
    "legal.sign_contract",
    "hr.hire",
    "hr.fire",
    "security.destructive_exploitation",
}
