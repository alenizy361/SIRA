"""Regression: the CEO's planning contract used to see ONLY the current
goal's title+description - zero visibility into anything the company had
done before. Every "conversation" was really a sequence of unrelated,
memory-less one-shot commands, so a follow-up referencing earlier work
("did the previous request finish?") had nothing to actually continue.
_recent_company_history gives the CEO a short digest of recent goals so
planning has continuity."""
import uuid

from app.models.company import Goal
from app.models.work import Plan
from app.services.planning import _build_ceo_task_contract, _recent_company_history


def _make_goal(db, org_id, title, state="completed"):
    goal = Goal(organization_id=org_id, title=title, description=title, state=state)
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


def test_recent_company_history_is_empty_for_the_very_first_goal(db, org_id):
    goal = _make_goal(db, org_id, "First ever goal")
    history = _recent_company_history(db, org_id, exclude_goal_id=goal.id)
    assert "first request" in history.lower()


def test_recent_company_history_surfaces_prior_goals_and_their_plan_summaries(db, org_id):
    older = _make_goal(db, org_id, "Fix the checkout bug", state="completed")
    plan = Plan(
        organization_id=org_id, goal_id=older.id, title="Checkout fix",
        summary="Patched a null pointer in the payment handler and added a regression test.",
        state="drafted",
    )
    db.add(plan)
    db.commit()

    new_goal = _make_goal(db, org_id, "Did that checkout fix actually ship?")
    history = _recent_company_history(db, org_id, exclude_goal_id=new_goal.id)

    assert "Fix the checkout bug" in history
    assert "Patched a null pointer" in history
    assert "[completed]" in history
    # The goal being planned right now must never appear in its own history.
    assert "Did that checkout fix actually ship?" not in history


def test_recent_company_history_excludes_the_goal_being_planned(db, org_id):
    goal = _make_goal(db, org_id, "Only goal")
    history = _recent_company_history(db, org_id, exclude_goal_id=goal.id)
    assert "Only goal" not in history


def test_recent_company_history_respects_the_limit(db, org_id):
    for i in range(8):
        _make_goal(db, org_id, f"Goal number {i}")
    current = _make_goal(db, org_id, "The one being planned now")
    history = _recent_company_history(db, org_id, exclude_goal_id=current.id, limit=5)
    assert history.count("Goal number") == 5


def test_ceo_contract_embeds_the_history_in_its_context(org_id):
    goal = Goal(organization_id=org_id, title="Ask a follow-up", description="Continue please")
    contract = _build_ceo_task_contract(
        goal, valid_agent_keys={"ceo", "qa"}, recent_history="- [completed] Prior goal - did the thing"
    )
    assert "Ask a follow-up" in contract.context
    assert "Prior goal - did the thing" in contract.context
