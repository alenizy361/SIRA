"""Proves worker.py's event wiring actually reaches Redis pub/sub - not
just that publish() was called, but that a real subscriber on the real
channel receives real CoreState/EventType-shaped messages, exactly as
apps/api/app/realtime/ws_router.py's WebSocket handler would."""
import json
import uuid

from app.realtime.bus import EventBus
from claude_worker.worker import run_once


class _FakeRunResult:
    def __init__(self, task_id, run_id):
        self.task_id = task_id
        self.run_id = run_id
        self.exit_code = 0
        self.timed_out = False
        self.cancelled = False
        self.cost_usd = None
        self.cli_session_id = None
        self.workspace_path = "/tmp/fake"
        self.branch_name = "agent/fake"
        self.baseline_commit = "abc123"
        self.final_commit = "def456"
        self.result_summary = "Fixed the copy on step 3."
        self.changed_files = ["src/funnel/step3.py"]


def _fake_start_run_emitting_events(self, contract, run_id=None, on_event=None, is_cancelled=None, resume_session_id=None):
    class _Handle:
        cancelled = False

        def cancel(self):
            pass

    def _wait():
        if on_event:
            on_event({"kind": "tool_call", "tool_name": "Edit", "input": {"file": "step3.py"}})
            on_event({"kind": "tool_result", "tool_use_id": "t1", "is_error": False, "content_preview": "ok"})
            on_event({"kind": "final_text", "text": "Done."})
        return _FakeRunResult(contract.task_id, run_id)

    return _Handle(), _wait


def _make_ready_task(db, org_id):
    from app.models.work import Task

    task = Task(
        organization_id=org_id,
        title="Improve step 3 CTA copy",
        description="Change the step-3 label to something more encouraging.",
        assigned_agent_key="frontend_engineer",
        state="ready",
        idempotency_key=f"task-{uuid.uuid4()}",
        acceptance_criteria={"criteria": ["label changed"], "allowed_tools": ["Read", "Edit"]},
        workspace_repo="sample-cv-app",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_task_execution_publishes_real_events_over_redis_pubsub(db, org_id, monkeypatch):
    from claude_worker.cli_adapter import ClaudeCodeAdapter
    from app.config import get_settings

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run_emitting_events)
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    bus = EventBus(get_settings().redis_url)
    pubsub = bus.pubsub(str(org_id))
    pubsub.get_message(timeout=0.1)  # discard the subscribe-confirmation message

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 1

    received = []
    for _ in range(40):
        msg = pubsub.get_message(timeout=0.2)
        if msg and msg.get("type") == "message":
            received.append(json.loads(msg["data"]))
        if len(received) >= 10:
            break
    pubsub.close()

    types_seen = [e["type"] for e in received]
    assert "task.assigned" in types_seen
    assert "task.started" in types_seen
    assert "core.state.changed" in types_seen
    assert "run.tool.started" in types_seen
    assert "run.tool.completed" in types_seen
    assert "run.output.delta" in types_seen
    assert "task.progress" in types_seen
    # A clean run must announce that the task actually COMPLETED - the dashboard
    # (and the neural board's per-agent "done" state) depends on this event.
    assert "task.completed" in types_seen

    core_state_events = [e for e in received if e["type"] == "core.state.changed"]
    core_states = [e["payload"]["state"] for e in core_state_events]
    assert "coding" in core_states
    assert "reviewing" in core_states
    assert "idle" in core_states

    tool_started = next(e for e in received if e["type"] == "run.tool.started")
    assert tool_started["payload"]["tool_name"] == "Edit"
    assert tool_started["entities"][0] == {"type": "task", "id": str(task.id)}

    # Sequence numbers must be monotonically increasing per organization -
    # this is what lets a reconnecting client replay-since-last-seq.
    sequences = [e["sequence"] for e in received]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def _fake_start_run_with_correlated_tool_call(self, contract, run_id=None, on_event=None, is_cancelled=None, resume_session_id=None):
    class _Handle:
        cancelled = False

        def cancel(self):
            pass

    def _wait():
        if on_event:
            on_event({"kind": "tool_call", "id": "toolu_01abc", "tool_name": "Read", "input": {"file_path": "app.py"}})
            on_event({
                "kind": "tool_result", "tool_use_id": "toolu_01abc",
                "is_error": False, "content_preview": "def main(): ...",
            })
            on_event({"kind": "final_text", "text": "Read the file."})
        return _FakeRunResult(contract.task_id, run_id)

    return _Handle(), _wait


def test_tool_calls_are_persisted_as_a_durable_work_log(db, org_id, monkeypatch):
    """Regression: a ToolCall table existed in the schema with nothing ever
    writing to it - tool-call data only ever lived on the transient WS
    stream, lost on reload. Every tool_call/tool_result pair must land as a
    matched, durable ToolCall row - correlated by the tool_use id, not just
    two disconnected rows."""
    from app.models.work import Run, ToolCall
    from claude_worker.cli_adapter import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "start_run", _fake_start_run_with_correlated_tool_call)
    monkeypatch.setattr(ClaudeCodeAdapter, "check_auth", lambda self: {"loggedIn": True})
    adapter = ClaudeCodeAdapter(cli_path="claude", workspace_root="/tmp/fake-workspace")

    task = _make_ready_task(db, org_id)
    executed = run_once(db, adapter, agent_concurrency_limits={"frontend_engineer": 5})
    assert executed == 1

    run = db.query(Run).filter(Run.task_id == task.id).one()
    calls = db.query(ToolCall).filter(ToolCall.run_id == run.id).all()
    assert len(calls) == 1, "tool_call and tool_result must merge into ONE correlated row, not two"

    call = calls[0]
    assert call.tool_name == "Read"
    assert call.input_summary == {"file_path": "app.py"}
    assert call.output_summary == {"preview": "def main(): ..."}
    assert call.succeeded is True
    assert call.finished_at is not None
