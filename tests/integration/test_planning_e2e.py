"""REAL end-to-end proof of the CEO-agent planning flow
(app.services.planning.plan_goal): an actual `claude` CLI call with
--json-schema turns a Goal into a persisted Plan + Task graph.

Like test_claude_worker_e2e.py, this spends real Claude usage - kept to one
call - and is skipped automatically when the CLI isn't authenticated.
"""
import json
import subprocess
import uuid

import pytest


def _cli_authenticated() -> bool:
    try:
        result = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout).get("loggedIn", False)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _cli_authenticated(), reason="claude CLI not authenticated in this environment")


def test_ceo_agent_plans_a_real_goal(db, org_id):
    from app.models.company import Goal
    from app.services.planning import plan_goal

    goal = Goal(
        organization_id=org_id,
        title="Improve CV builder funnel conversion",
        description="Review the cv.rabit.sa CV builder flow and fix the largest conversion drop-off.",
        state="goal_captured",
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)

    plan = plan_goal(db, goal)

    assert plan.title
    assert plan.summary
    db.refresh(goal)
    assert goal.state == "plan_drafted"

    from app.models.work import PlanStep, Task

    steps = db.query(PlanStep).filter(PlanStep.plan_id == plan.id).all()
    tasks = db.query(Task).filter(Task.plan_id == plan.id).all()
    assert 2 <= len(steps) <= 5
    assert len(tasks) == len(steps)

    valid_agent_keys = {row.agent_key for row in db.execute(
        __import__("sqlalchemy").text("SELECT agent_key FROM agent_definitions")
    ).all()} or {
        "ceo", "cto", "product_manager", "research", "ux_research", "product_design",
        "frontend_engineer", "backend_engineer", "database_engineer", "devops_sre", "qa",
        "security", "independent_reviewer", "seo_geo", "marketing_growth", "analytics",
        "customer_success", "customer_support", "finance_procurement", "operations",
        "legal_compliance", "memory_librarian", "incident_commander",
    }
    for task in tasks:
        assert task.assigned_agent_key in valid_agent_keys
        assert task.risk_level in ("R0", "R1", "R2")
        assert task.state == "ready"
