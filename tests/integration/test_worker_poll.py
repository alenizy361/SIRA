"""Integration tests for claude_worker.worker's poll-tick logic against the
real local Postgres. The actual `claude` CLI subprocess is monkeypatched out
here (ClaudeCodeAdapter.start_run) so these tests are free/fast and exercise
only the worker's own DB/state-machine/leasing wiring - the real CLI
invocation is separately proven in test_claude_worker_e2e.py."""
import uuid
from types import SimpleNamespace

from claude_worker.worker import run_once


class _FakeRunResult(SimpleNamespace):
    pass


def _fake_start_run(self, contract, run_id=None, on_event=None, is_cancelled=None):
    class _Handle:
        cancelled = False

        def cancel(self):
            pass

    def _wait():
        return _FakeRunResult(
            task_id=contract.task_id,
            run_id=run_id,
            exit_code=0,
            timed_out=False,
            cancelled=False,
            cost_usd=None,
            workspace_path="/tmp/fake",
            branch_name="agent/fake",
            baseline_commit="abc123",
            final_commit="def456",
            result_summary="Fixed it.",
            changed_files=["src/funnel/step3.py"],
        )

    return _Handle(), _wait


def _make_ready_task(db, org_id, agent_key="frontend_engineer"):
    from app.models.work import Task

    task = Task(
        organization_id=org_id,
        title="Improve step 3 CTA copy",
        description="Change the step-3 label to something more encouraging.",
        assigned_agent_key=agent_key,
        state="ready",
        idempotency_key=f"task-{uuid.uuid4()}",
        acceptance_criteria={"criteria": ["label changed"], "allowed_tools": ["Read", "Edit"]},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_poll_tick_is_a_noop_when_no_ready_tasks(db, org_id, monkeypatch):
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    executed = run_once(db, adapter, agent_concurrency_limits={})
    assert executed == 0


def test_poll_tick_executes_ready_task_and_advances_state(db, org_id, monkeypatch):
    from app.models.work import Task
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 1

    db.refresh(task)
    # A clean run must drive the task all the way to a terminal COMPLETED - not
    # strand it in VALIDATING (there is no separate reviewer agent to finish it,
    # so a task left "validating" would never complete and the goal would hang).
    assert task.state == "completed"


def test_unexpected_run_error_lands_task_in_retry_wait_not_stuck_running(db, org_id, monkeypatch):
    """Regression for the "task wedged in RUNNING forever" bug: any error from
    start_run/wait other than AuthenticationRequiredError must leave the task
    in a recoverable state and release its lease, never RUNNING."""
    from app.models.work import RunLease
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    def _boom_start_run(self, contract, run_id=None, on_event=None, is_cancelled=None):
        raise FileNotFoundError("claude CLI not found on host")

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _boom_start_run)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 0

    db.refresh(task)
    assert task.state == "retry_wait"
    # Lease must be released so the task is reschedulable.
    assert db.query(RunLease).filter(RunLease.task_id == task.id).count() == 0


def test_poll_tick_skips_task_over_concurrency_ceiling(db, org_id, monkeypatch):
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 0})
    assert executed == 0


def test_emergency_stop_autonomy_mode_blocks_new_task_assignment(db, org_id, monkeypatch):
    """Regression test for the emergency-stop gap: POST /system/emergency-stop
    sets autonomy_mode to observe_only, and a freshly-created READY task
    (which has no lease yet) must not be picked up by the poll loop while
    that mode is set - revoking existing leases alone is not sufficient."""
    from app.models.identity import Organization
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    org = db.get(Organization, org_id)
    org.autonomy_mode = "observe_only"
    db.add(org)
    db.commit()

    _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 0

    org.autonomy_mode = "execute_low_risk"
    db.add(org)
    db.commit()
    executed_after_resume = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed_after_resume == 1


def test_pending_plan_request_is_claimed_and_processed(db, org_id, monkeypatch):
    """A goal flagged plan_status=requested must be picked up by the worker,
    handed to CEO planning (mocked here), and marked done - and never
    double-claimed on a second tick."""
    import claude_worker.worker as worker_mod
    from app.models.company import Goal

    calls = {"n": 0}

    def _fake_plan_goal(db_, goal_):
        calls["n"] += 1
        # emulate what real plan_goal does to the goal's state
        goal_.state = "plan_drafted"
        db_.add(goal_)
        db_.commit()
        return SimpleNamespace(id=uuid.uuid4(), title="Mock plan", summary="s", state="drafted")

    # plan_goal is imported lazily inside _run_pending_plans from
    # app.services.planning, so patch it there.
    import app.services.planning as planning_mod
    monkeypatch.setattr(planning_mod, "plan_goal", _fake_plan_goal)

    goal = Goal(
        organization_id=org_id,
        created_by=None,
        title="Improve funnel",
        description="Fix the biggest drop-off",
        state="goal_captured",
        metadata_json={"plan_status": "requested"},
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)

    ran = worker_mod._run_pending_plans(db, org_id)
    assert ran == 1
    assert calls["n"] == 1

    db.refresh(goal)
    assert goal.metadata_json.get("plan_status") == "done"
    assert goal.state == "plan_drafted"

    # Second tick must not re-run planning for an already-done goal.
    ran_again = worker_mod._run_pending_plans(db, org_id)
    assert ran_again == 0
    assert calls["n"] == 1


def test_pending_plan_failure_marks_goal_failed(db, org_id, monkeypatch):
    import claude_worker.worker as worker_mod
    import app.services.planning as planning_mod
    from app.models.company import Goal
    from app.services.planning import PlanningError

    def _boom(db_, goal_):
        raise PlanningError("CEO agent returned no valid tasks")

    monkeypatch.setattr(planning_mod, "plan_goal", _boom)

    goal = Goal(
        organization_id=org_id,
        created_by=None,
        title="G",
        description="d",
        state="goal_captured",
        metadata_json={"plan_status": "requested"},
    )
    db.add(goal)
    db.commit()

    ran = worker_mod._run_pending_plans(db, org_id)
    assert ran == 0
    db.refresh(goal)
    assert goal.metadata_json.get("plan_status") == "failed"
    assert "plan_error" in goal.metadata_json


def test_goal_advances_to_completed_once_all_its_tasks_finish(db, org_id):
    """Regression: Goal.state was only ever written by planning.py (stops at
    plan_drafted) and the manual /transition endpoint - nothing ever advanced
    it further, so a goal whose plan had 100% completed tasks sat showing
    'plan_drafted' forever. The operator (and the dashboard) had no way to
    tell a finished goal apart from a barely-started one."""
    import json

    from app.config import get_settings
    from app.models.company import Goal
    from app.models.work import Plan, Task
    from app.realtime.bus import EventBus
    from claude_worker.worker import _advance_completed_goals

    goal = Goal(
        organization_id=org_id,
        title="Ship the landing page",
        description="A fast Arabic landing page.",
        state="plan_drafted",
    )
    db.add(goal)
    db.flush()
    plan = Plan(organization_id=org_id, goal_id=goal.id, title="Landing page delivery", state="drafted")
    db.add(plan)
    db.flush()
    t1 = Task(
        organization_id=org_id, plan_id=plan.id, title="Build hero", description="d",
        assigned_agent_key="frontend_engineer", risk_level="R1", state="completed",
        idempotency_key=f"t1-{uuid.uuid4()}", acceptance_criteria={},
    )
    t2 = Task(
        organization_id=org_id, plan_id=plan.id, title="QA pass", description="d",
        assigned_agent_key="qa", risk_level="R0", state="cancelled",
        idempotency_key=f"t2-{uuid.uuid4()}", acceptance_criteria={},
    )
    db.add_all([t1, t2])
    db.commit()

    bus = EventBus(get_settings().redis_url)
    pubsub = bus.pubsub(str(org_id))
    pubsub.get_message(timeout=0.1)

    advanced = _advance_completed_goals(db, org_id)
    assert advanced == 1

    db.refresh(goal)
    assert goal.state == "completed"

    msg = pubsub.get_message(timeout=1.0)
    while msg and msg.get("type") != "message":
        msg = pubsub.get_message(timeout=1.0)
    pubsub.close()
    assert msg is not None
    payload = json.loads(msg["data"])
    assert payload["type"] == "goal.completed"
    assert payload["entities"][0]["id"] == str(goal.id)


def test_goal_stays_put_while_any_task_is_still_in_flight(db, org_id):
    """A single non-terminal task must block auto-completion - otherwise a
    goal could be falsely marked done while real work is still running."""
    from app.models.company import Goal
    from app.models.work import Plan, Task
    from claude_worker.worker import _advance_completed_goals

    goal = Goal(
        organization_id=org_id, title="Multi-step goal", description="d", state="plan_drafted",
    )
    db.add(goal)
    db.flush()
    plan = Plan(organization_id=org_id, goal_id=goal.id, title="Plan", state="drafted")
    db.add(plan)
    db.flush()
    db.add(Task(
        organization_id=org_id, plan_id=plan.id, title="Done part", description="d",
        assigned_agent_key="qa", risk_level="R0", state="completed",
        idempotency_key=f"t1-{uuid.uuid4()}", acceptance_criteria={},
    ))
    db.add(Task(
        organization_id=org_id, plan_id=plan.id, title="Still running", description="d",
        assigned_agent_key="qa", risk_level="R0", state="running",
        idempotency_key=f"t2-{uuid.uuid4()}", acceptance_criteria={},
    ))
    db.commit()

    advanced = _advance_completed_goals(db, org_id)
    assert advanced == 0
    db.refresh(goal)
    assert goal.state == "plan_drafted"


def test_worker_stops_a_running_task_and_marks_it_cancelled_when_an_operator_requests_it(db, org_id, engine, monkeypatch):
    """The point of the whole is_cancelled wiring: a Cancellation row written
    via a SEPARATE DB connection (exactly what POST /tasks/{id}/cancel does
    for a RUNNING task - work_views.cancel_task never touches task.state
    itself for that case) must be visible to the worker's own poll and drive
    the task to CANCELLED, not left running to its own timeout."""
    import uuid as _uuid
    from datetime import datetime, timezone

    from sqlalchemy.orm import Session as OrmSession

    from app.models.identity import User
    from app.models.work import Cancellation, Run
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    user = User(
        organization_id=org_id, email=f"canceller-{_uuid.uuid4().hex[:8]}@rabit.sa",
        password_hash="x", display_name="Canceller",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    def _fake_start_run_honoring_cancel(self, contract, run_id=None, on_event=None, is_cancelled=None):
        class _Handle:
            cancelled = False

            def cancel(self):
                pass

        def _wait():
            # Simulate the operator's cancel request landing mid-run, via a
            # connection independent of the worker's own `db` session.
            session = OrmSession(bind=engine)
            session.add(Cancellation(
                task_id=_uuid.UUID(contract.task_id), requested_by=user.id,
                created_at=datetime.now(timezone.utc),
            ))
            session.commit()
            session.close()

            assert is_cancelled() is True, "is_cancelled() must see the Cancellation row from the other session"

            return _FakeRunResult(
                task_id=contract.task_id, run_id=run_id, exit_code=None, timed_out=False,
                cancelled=True, cost_usd=None, workspace_path="/tmp/fake",
                branch_name="agent/fake", baseline_commit="abc123", final_commit=None,
                result_summary="", changed_files=[],
            )

        return _Handle(), _wait

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run_honoring_cancel)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 1

    db.refresh(task)
    assert task.state == "cancelled"
    run = db.query(Run).filter(Run.task_id == task.id).one()
    assert run.state == "cancelled"


def test_task_is_blocked_without_invoking_the_cli_once_org_budget_is_exceeded(db, org_id, monkeypatch):
    """Regression target: a Budget row existed in the schema with nothing
    ever enforcing it - an org could burn unlimited real dollars through the
    CLI subprocess. Once spend + reserved reaches monthly_max, the CLI must
    never even be invoked for a new task."""
    from app.models.integrations import Budget
    from app.models.work import Run, RunLease
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    db.add(Budget(
        organization_id=org_id, scope="org", scope_ref=str(org_id),
        monthly_max=10, spent_amount=10,
    ))
    db.commit()

    def _start_run_must_not_be_called(self, contract, run_id=None, on_event=None, is_cancelled=None):
        raise AssertionError("the CLI must not be invoked once the org is over budget")

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _start_run_must_not_be_called)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 0

    db.refresh(task)
    assert task.state == "retry_wait"
    assert db.query(RunLease).filter(RunLease.task_id == task.id).count() == 0
    run = db.query(Run).filter(Run.task_id == task.id).one()
    assert run.state == "failed"


def test_real_run_cost_is_committed_against_the_configured_budget(db, org_id, monkeypatch):
    """The CLI's own reported total_cost_usd must land as real spend against
    the org's Budget row - not just be discarded after the run finishes."""
    from app.models.integrations import Budget, BudgetTransaction
    from app.models.work import Run
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    budget = Budget(organization_id=org_id, scope="org", scope_ref=str(org_id), monthly_max=100, spent_amount=0)
    db.add(budget)
    db.commit()
    db.refresh(budget)

    def _fake_start_run_with_cost(self, contract, run_id=None, on_event=None, is_cancelled=None):
        class _Handle:
            cancelled = False

            def cancel(self):
                pass

        def _wait():
            return _FakeRunResult(
                task_id=contract.task_id, run_id=run_id, exit_code=0, timed_out=False,
                cancelled=False, cost_usd=2.5, workspace_path="/tmp/fake",
                branch_name="agent/fake", baseline_commit="abc123", final_commit="def456",
                result_summary="Done.", changed_files=[],
            )

        return _Handle(), _wait

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run_with_cost)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 1

    db.refresh(task)
    assert task.state == "completed"
    run = db.query(Run).filter(Run.task_id == task.id).one()
    assert float(run.cost_usd) == 2.5

    db.refresh(budget)
    assert float(budget.spent_amount) == 2.5
    txn = db.query(BudgetTransaction).filter(BudgetTransaction.idempotency_key == f"run-cost-{run.id}").one()
    assert float(txn.amount) == 2.5
