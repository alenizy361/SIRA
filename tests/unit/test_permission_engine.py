"""Unit tests for packages/permission-engine.

Run with: pytest tests/unit/test_permission_engine.py
(PYTHONPATH must include packages/, packages/permission-engine - see
tests/unit/conftest.py which wires this up automatically.)
"""
import pytest

from contracts.risk_levels import RiskLevel
from permission_engine import (
    ActionRequest,
    AgentPermissionProfile,
    PermissionEngine,
    PermissionOutcome,
)


@pytest.fixture
def engine():
    return PermissionEngine()


@pytest.fixture
def frontend_profile():
    return AgentPermissionProfile(
        agent_id="frontend_engineer",
        risk_ceiling=RiskLevel.R2,
        allowed_tools=["git", "test_runner", "playwright_browser"],
        denied_tools=["postgres_migration"],
        allowed_actions=[
            "edit_frontend_file", "run_tests", "create_preview",
            "deploy_staging", "deploy_production_hotfix",
        ],
        forbidden_actions=["deploy_production", "change_database_schema"],
        budget_daily_max_sar=0,
        budget_monthly_max_sar=0,
    )


def make_request(**overrides):
    base = dict(
        organization_id="org-1",
        actor_id="agent-frontend-1",
        actor_type="agent",
        action="edit_frontend_file",
        tool="git",
        risk_level=RiskLevel.R1,
    )
    base.update(overrides)
    return ActionRequest(**base)


def test_r0_r1_auto_approved(engine, frontend_profile):
    req = make_request(risk_level=RiskLevel.R0)
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.AUTO_APPROVED

    req2 = make_request(risk_level=RiskLevel.R1)
    decision2 = engine.evaluate(req2, frontend_profile)
    assert decision2.outcome == PermissionOutcome.AUTO_APPROVED


def test_r2_requires_validation_reversibility_rollback(engine, frontend_profile):
    req = make_request(
        risk_level=RiskLevel.R2,
        action="create_preview",
        reversible=True,
        validation_passed=False,
        rollback_available=True,
    )
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.APPROVAL_REQUIRED
    assert "validation has not passed" in decision.blocking_conditions


def test_r2_auto_approves_when_all_conditions_met(engine, frontend_profile):
    req = make_request(
        risk_level=RiskLevel.R2,
        action="create_preview",
        reversible=True,
        validation_passed=True,
        rollback_available=True,
    )
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.AUTO_APPROVED


def test_r3_always_requires_human_approval(engine, frontend_profile):
    # Even though this agent's ceiling is R2 (so this would already escalate
    # for that reason), verify the R3-specific message/reviewer applies when
    # the ceiling happens to be R3 too.
    profile = frontend_profile.model_copy(update={"risk_ceiling": RiskLevel.R3})
    req = make_request(risk_level=RiskLevel.R3, action="deploy_staging")
    decision = engine.evaluate(req, profile)
    assert decision.outcome == PermissionOutcome.APPROVAL_REQUIRED
    assert "human_administrator" in decision.required_reviewers


def test_r4_requires_reviewer_and_rollback(engine):
    profile = AgentPermissionProfile(
        agent_id="database_engineer",
        risk_ceiling=RiskLevel.R4,
        allowed_actions=["run_migration"],
    )
    req = make_request(
        action="run_migration",
        risk_level=RiskLevel.R4,
        reviewer_assigned=False,
        rollback_available=False,
    )
    decision = engine.evaluate(req, profile)
    assert decision.outcome == PermissionOutcome.APPROVAL_REQUIRED
    assert "independent_reviewer" in decision.required_reviewers
    assert "independent reviewer not yet assigned" in decision.blocking_conditions
    assert "no verified rollback plan" in decision.blocking_conditions


def test_r5_always_rejected_even_with_high_ceiling(engine):
    profile = AgentPermissionProfile(
        agent_id="ceo",
        risk_ceiling=RiskLevel.R4,  # hypothetically high; must not matter
        allowed_actions=["anything"],
    )
    req = make_request(action="anything", risk_level=RiskLevel.R5)
    decision = engine.evaluate(req, profile)
    assert decision.outcome == PermissionOutcome.REJECTED


def test_hard_prohibited_action_rejected_regardless_of_risk_level_label(engine):
    """Guards against a caller mislabeling risk_level - the action-name
    blocklist is a second, independent line of defense."""
    profile = AgentPermissionProfile(
        agent_id="ceo",
        risk_ceiling=RiskLevel.R4,
        allowed_actions=["self.grant_permission"],
    )
    req = make_request(action="self.grant_permission", risk_level=RiskLevel.R1)
    decision = engine.evaluate(req, profile)
    assert decision.outcome == PermissionOutcome.REJECTED
    assert "hard-prohibited" in decision.reason


def test_agent_cannot_exceed_its_own_risk_ceiling(engine, frontend_profile):
    req = make_request(risk_level=RiskLevel.R3, action="deploy_production_hotfix")
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.APPROVAL_REQUIRED
    assert "exceeds agent" in decision.reason


def test_forbidden_action_rejected(engine, frontend_profile):
    req = make_request(action="deploy_production", risk_level=RiskLevel.R1)
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.REJECTED


def test_denied_tool_rejected(engine, frontend_profile):
    req = make_request(tool="postgres_migration", risk_level=RiskLevel.R1)
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.REJECTED


def test_disabled_agent_rejected(engine):
    profile = AgentPermissionProfile(
        agent_id="marketing_growth",
        risk_ceiling=RiskLevel.R2,
        enabled=False,
    )
    req = make_request(action="create_campaign_draft", risk_level=RiskLevel.R1)
    decision = engine.evaluate(req, profile)
    assert decision.outcome == PermissionOutcome.REJECTED
    assert "disabled" in decision.reason


def test_incident_active_blocks_non_incident_actions(engine, frontend_profile):
    req = make_request(
        risk_level=RiskLevel.R1,
        incident_active=True,
        is_incident_response_action=False,
    )
    decision = engine.evaluate(req, frontend_profile)
    assert decision.outcome == PermissionOutcome.APPROVAL_REQUIRED
    assert "incident_commander" in decision.required_reviewers


def test_budget_ceiling_enforced():
    engine = PermissionEngine()
    profile = AgentPermissionProfile(
        agent_id="marketing_growth",
        risk_ceiling=RiskLevel.R2,
        allowed_actions=["experimental_spend"],
        budget_daily_max_sar=100.0,
        budget_monthly_max_sar=2000.0,
    )
    req = make_request(
        action="experimental_spend",
        risk_level=RiskLevel.R1,
        estimated_cost_sar=50.0,
        cumulative_spent_today_sar=60.0,
    )
    decision = engine.evaluate(req, profile)
    assert decision.outcome == PermissionOutcome.REJECTED
    assert "daily budget ceiling" in decision.reason


def test_decision_carries_policy_version_for_audit(engine, frontend_profile):
    req = make_request(risk_level=RiskLevel.R0)
    decision = engine.evaluate(req, frontend_profile)
    assert decision.policy_version
    audit_record = decision.to_audit_record(req)
    assert audit_record["decision"]["outcome"] == "auto_approved"
    assert audit_record["request"]["action"] == "edit_frontend_file"
