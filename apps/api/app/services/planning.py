"""Real CEO-agent planning: turns a Goal into a Plan + Task graph using the
actual, locally-authenticated Claude Code CLI with structured JSON output
(--json-schema), not a canned template. This is the "CEO agent converts a
goal into a plan" step from constitution section 26 / the product vision.

Deliberately synchronous (the API route awaits this directly) since a
single planning call is short (no code tools, pure reasoning, small
timeout) - background execution belongs to services/claude-worker's poll
loop for actual code-writing tasks, not this one-shot planning step.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[4]
for p in [_REPO_ROOT / "packages", _REPO_ROOT / "services" / "claude-worker", _REPO_ROOT / "services"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from claude_worker.cli_adapter import ClaudeCodeAdapter  # noqa: E402
from claude_worker.task_contract import TaskContract  # noqa: E402
from orchestrator.state_machine import GoalState, transition_goal  # noqa: E402

from app.config import get_settings
from app.models.agents import AgentDefinition
from app.models.company import Goal
from app.models.work import Plan, PlanStep, Task
from app.realtime.publisher import publish
from contracts.events import EntityRef, EventType

PLAN_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["plan_title", "summary", "tasks"],
    "properties": {
        "plan_title": {"type": "string"},
        "summary": {"type": "string"},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["title", "description", "agent_key", "risk_level"],
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "agent_key": {"type": "string"},
                    "risk_level": {"type": "string", "enum": ["R0", "R1", "R2"]},
                },
            },
        },
    },
}


class PlanningError(Exception):
    pass


def _build_ceo_task_contract(goal: Goal, valid_agent_keys: set[str]) -> TaskContract:
    return TaskContract(
        task_id=f"plan-{uuid4().hex[:12]}",
        mission=(
            "You are the CEO agent of Rabit AI Company OS. Convert the goal below into "
            "a short execution plan: a plan_title, a one-paragraph summary, and 2-5 concrete "
            "tasks. Each task must be assigned to exactly one agent_key from this exact list "
            f"(use these snake_case keys verbatim, nothing else): {sorted(valid_agent_keys)}. "
            "Each task's risk_level must be R0, R1, or R2 only (higher-risk work always needs "
            "human approval and cannot be auto-planned here). Respond ONLY with the JSON object "
            "matching the required schema - no prose outside it."
        ),
        context=f"Goal title: {goal.title}\nGoal description: {goal.description}",
        constraints=[
            "Every task's agent_key must be one of the allowed agent keys listed in the mission.",
            "Every task's risk_level must be R0, R1, or R2.",
            "Produce between 2 and 5 tasks.",
        ],
        # Read-only: planning is pure reasoning, but the list must be explicit.
        # An empty list used to mean "send no --allowedTools flag", which left
        # the CLI on its interactive default and made the very first tool it
        # reached for surface as "this command needs approval" - unanswerable
        # in a headless worker, so planning just hung until it timed out.
        allowed_tools=["Read", "Grep", "Glob"],
        prohibited_actions=["deploy_production", "spend_money", "modify_permissions"],
        acceptance_criteria=["Valid JSON matching the required output schema"],
        output_schema=PLAN_OUTPUT_SCHEMA,
        timeout_seconds=180,
        risk_level="R1",
    )


def plan_goal(db, goal: Goal) -> Plan:
    """Invokes the real Claude Code CLI (CEO agent role) to draft a plan for
    `goal`, persists Plan + PlanStep + Task rows, and advances the goal's
    state machine to PLAN_DRAFTED. Raises PlanningError on any failure -
    callers should surface this as a 502 rather than silently falling back
    to fake data (constitution: no placeholder business logic)."""
    settings = get_settings()
    valid_agent_keys = {row.agent_key for row in db.query(AgentDefinition.agent_key).all()}
    if not valid_agent_keys:
        raise PlanningError("No agent definitions loaded - cannot plan without a valid agent_key allowlist")

    adapter = ClaudeCodeAdapter(cli_path=settings.claude_cli_path, workspace_root=settings.claude_worker_workspace_root)
    contract = _build_ceo_task_contract(goal, valid_agent_keys)

    try:
        _handle, wait = adapter.start_run(contract)
        result = wait()
    except Exception as exc:  # noqa: BLE001
        raise PlanningError(f"Claude CLI invocation failed: {exc}") from exc

    if result.exit_code != 0 or result.timed_out:
        raise PlanningError(f"Planning run failed (exit={result.exit_code}, timed_out={result.timed_out}): {result.stderr_tail}")

    try:
        parsed = json.loads(result.result_summary)
    except json.JSONDecodeError as exc:
        raise PlanningError(f"CEO agent did not return valid JSON: {result.result_summary[:500]!r}") from exc

    # --json-schema is only enforced when the CLI supports it; otherwise the
    # response is free-form, so validate the shape here rather than letting a
    # KeyError/AttributeError escape (which would strand the goal mid-plan).
    if not isinstance(parsed, dict):
        raise PlanningError(f"CEO agent returned non-object JSON ({type(parsed).__name__}): {str(parsed)[:300]!r}")

    plan_title = parsed.get("plan_title")
    if not isinstance(plan_title, str) or not plan_title.strip():
        raise PlanningError(f"CEO agent response missing required 'plan_title': {str(parsed)[:300]!r}")
    summary = parsed.get("summary")
    if not isinstance(summary, str):
        summary = ""

    tasks_payload = parsed.get("tasks", [])
    if not isinstance(tasks_payload, list):
        tasks_payload = []
    valid_tasks = [
        t for t in tasks_payload
        if isinstance(t, dict)
        and t.get("agent_key") in valid_agent_keys
        and t.get("risk_level") in ("R0", "R1", "R2")
        and isinstance(t.get("title"), str) and t.get("title").strip()
        and isinstance(t.get("description"), str) and t.get("description").strip()
    ]
    if not valid_tasks:
        raise PlanningError(f"CEO agent returned no valid tasks (agent_key must be one of {sorted(valid_agent_keys)}): {tasks_payload}")

    state = GoalState(goal.state)
    for target in (GoalState.GOAL_CLARIFIED, GoalState.DISCOVERY, GoalState.PLAN_DRAFTED):
        state = transition_goal(state, target)
    goal.state = state.value
    db.add(goal)

    plan = Plan(
        organization_id=goal.organization_id,
        created_by=goal.created_by,
        goal_id=goal.id,
        title=plan_title[:300],
        summary=summary,
        state="drafted",
    )
    db.add(plan)
    db.flush()

    created_tasks = []
    for i, item in enumerate(valid_tasks):
        # title columns are String(300); truncate so a long model title cannot
        # raise StringDataRightTruncation mid-transaction.
        item_title = item["title"][:300]
        db.add(PlanStep(plan_id=plan.id, sequence=i, title=item_title, assigned_agent_key=item["agent_key"]))
        task_row = Task(
            organization_id=goal.organization_id,
            created_by=goal.created_by,
            plan_id=plan.id,
            title=item_title,
            description=item["description"],
            assigned_agent_key=item["agent_key"],
            risk_level=item["risk_level"],
            state="ready",
            idempotency_key=f"{goal.id}-plan-{plan.id}-{i}",
            acceptance_criteria={"criteria": [], "allowed_tools": ["Read", "Grep", "Glob", "Write", "Edit"]},
        )
        db.add(task_row)
        created_tasks.append(task_row)

    db.commit()
    db.refresh(plan)

    # Announce the CEO's actual answer FIRST: title + the one-paragraph summary.
    # Without this the live feed only ever shows "created a task X" and the
    # operator never sees the CEO explain, in words, what it decided to do.
    publish(
        goal.organization_id, EventType.PLAN_CREATED, "ceo", str(plan.id),
        payload={
            "title": plan.title,
            "summary": plan.summary or "",
            "task_count": len(created_tasks),
        },
        entities=[
            EntityRef(type="goal", id=str(goal.id)),
            EntityRef(type="plan", id=str(plan.id)),
        ],
    )

    for task_row in created_tasks:
        db.refresh(task_row)
        publish(
            goal.organization_id, EventType.TASK_CREATED, task_row.assigned_agent_key, str(task_row.id),
            payload={"title": task_row.title, "risk_level": task_row.risk_level},
            entities=[
                EntityRef(type="goal", id=str(goal.id)),
                EntityRef(type="plan", id=str(plan.id)),
                EntityRef(type="task", id=str(task_row.id)),
            ],
        )

    return plan
