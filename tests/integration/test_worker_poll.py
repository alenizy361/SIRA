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


def _fake_start_run(self, contract, run_id=None, on_event=None):
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
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    executed = run_once(db, adapter, agent_concurrency_limits={})
    assert executed == 0


def test_poll_tick_executes_ready_task_and_advances_state(db, org_id, monkeypatch):
    from app.models.work import Task
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run)
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 1

    db.refresh(task)
    assert task.state == "validating"


def test_poll_tick_skips_task_over_concurrency_ceiling(db, org_id, monkeypatch):
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run)
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
