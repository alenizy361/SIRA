"""Poll loop entrypoint: `python -m claude_worker.worker`.

Event-driven in spirit (constitution: "must not continuously call Claude
while idle") - this polls the database for READY tasks on a short interval,
but only ever invokes the `claude` CLI when a real leased task exists. An
empty queue costs one cheap SQL query per tick, not a Claude invocation.

This is the process infra/systemd/rabit-claude-worker.service runs as the
non-root `aicompany` user in a real deployment.
"""
import logging
import os
import sys
import time
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
for p in [
    _REPO_ROOT / "apps" / "api",
    _REPO_ROOT / "packages",
    _REPO_ROOT / "packages" / "permission-engine",
    _REPO_ROOT / "services",
]:
    sys.path.insert(0, str(p))

from app.config import get_settings  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.models.work import Task  # noqa: E402
from app.realtime.publisher import publish, publish_core_state  # noqa: E402
from contracts.events import CoreState, EntityRef, EventType  # noqa: E402
from orchestrator.leasing import LeaseNotAcquired, acquire_lease, expire_stale_leases, release_lease  # noqa: E402
from orchestrator.progression import advance_ready_pipeline  # noqa: E402
from orchestrator.scheduler import pick_ready_tasks  # noqa: E402
from orchestrator.state_machine import InvalidTransition, TaskState, transition_task  # noqa: E402

from .cli_adapter import AuthenticationRequiredError, ClaudeCodeAdapter
from .task_contract import TaskContract

logger = logging.getLogger("claude_worker")

POLL_INTERVAL_SECONDS = float(os.environ.get("CLAUDE_WORKER_POLL_INTERVAL", "5"))
WORKER_ID = os.environ.get("CLAUDE_WORKER_ID", f"claude-worker-{uuid.uuid4().hex[:8]}")


def _task_to_contract(task: Task) -> TaskContract:
    ac = task.acceptance_criteria or {}
    return TaskContract(
        task_id=str(task.id),
        mission=task.description,
        context=f"Task title: {task.title}",
        constraints=ac.get("constraints", []),
        allowed_tools=ac.get("allowed_tools", ["Read", "Edit"]),
        prohibited_actions=ac.get("prohibited_actions", []),
        acceptance_criteria=ac.get("criteria", []),
        output_schema=None,
        timeout_seconds=task.timeout_seconds,
        risk_level=task.risk_level,
        workspace_repo=task.workspace_repo,
    )


# Autonomy modes that permit the poll loop to pick up new work. Emergency
# stop (POST /system/emergency-stop) sets an organization to "observe_only",
# which must actually stop new task assignment here - constitution section
# 22: "Emergency stop must immediately: stop new task assignment..." -
# revoking leases alone (which the API route does) is not enough on its
# own without this check, since a fresh READY task has no lease yet.
AUTONOMOUS_EXECUTION_MODES = {"execute_low_risk", "controlled_autonomous"}


def run_once(db, adapter: ClaudeCodeAdapter, agent_concurrency_limits: dict[str, int]) -> int:
    """Runs a single poll tick across all organizations. Returns the number
    of tasks executed (0 on an idle tick - this is the common case)."""
    from app.models.identity import Organization

    executed = 0
    # Reclaim leases stranded by a crashed/killed worker so their tasks become
    # schedulable again instead of being stuck forever behind a dead lease
    # (constitution section 6 restart-recovery). Cheap and global; runs once.
    expire_stale_leases(db)
    for org_id, autonomy_mode in db.query(Organization.id, Organization.autonomy_mode).all():
        if autonomy_mode not in AUTONOMOUS_EXECUTION_MODES:
            continue
        # 1. Goals whose owner asked the CEO agent to plan them. This runs on
        #    the host (not the API container) precisely because it needs the
        #    `claude` CLI's authenticated session - see request_plan() in
        #    apps/api/app/routers/goals.py.
        executed += _run_pending_plans(db, org_id)
        # 2. Move stranded tasks forward: retry-backoff RETRY_WAIT->READY and
        #    cancel dependents of a dead prerequisite. Without this a single
        #    transient failure parks a task in RETRY_WAIT permanently. Not
        #    counted as "executed" - it triggers no CLI invocation itself.
        advance_ready_pipeline(db, org_id)
        # 3. Tasks a plan produced, ready for an engineer/analyst agent to run.
        ready = pick_ready_tasks(db, org_id, agent_concurrency_limits)
        for task in ready:
            executed += _execute_task(db, adapter, task)
    return executed


def _run_pending_plans(db, org_id) -> int:
    """Finds goals flagged plan_status=requested and runs CEO planning for
    each, on the host where the claude CLI session lives."""
    from app.models.company import Goal
    from app.services.planning import PlanningError, plan_goal
    from contracts.events import EntityRef, EventType

    ran = 0
    goals = (
        db.query(Goal)
        .filter(Goal.organization_id == org_id, Goal.state == "goal_captured")
        .all()
    )
    for goal in goals:
        meta = dict(goal.metadata_json or {})
        if meta.get("plan_status") != "requested":
            continue
        # Claim it atomically so a second worker tick (or a second worker)
        # never double-plans the same goal. A plain read-modify-write is racy;
        # take a row lock and RE-CHECK the flag under the lock (on Postgres
        # this is a genuine mutual exclusion; on a single-threaded test DB it
        # degrades harmlessly to the same check).
        locked = db.query(Goal).filter(Goal.id == goal.id).with_for_update().one()
        locked_meta = dict(locked.metadata_json or {})
        if locked_meta.get("plan_status") != "requested":
            db.commit()  # release the row lock; someone else claimed it
            continue
        locked_meta["plan_status"] = "running"
        locked.metadata_json = locked_meta
        db.add(locked)
        db.commit()  # commit releases the lock and publishes the claim
        goal = locked

        publish_core_state(org_id, CoreState.PLANNING, "ceo", str(goal.id))
        try:
            plan = plan_goal(db, goal)
        except Exception as exc:  # noqa: BLE001 - any failure must not strand the goal
            # Previously only PlanningError was caught, so a KeyError/AttributeError
            # from a malformed CEO response left the goal pinned at
            # plan_status='running' forever (never re-selected, never failed).
            logger.exception("Planning failed for goal %s", goal.id)
            db.rollback()
            db.refresh(goal)
            meta = dict(goal.metadata_json or {})
            meta["plan_status"] = "failed"
            meta["plan_error"] = str(exc)[:500]
            goal.metadata_json = meta
            db.add(goal)
            db.commit()
            publish_core_state(org_id, CoreState.WARNING, "ceo", str(goal.id))
            continue

        db.refresh(goal)
        meta = dict(goal.metadata_json or {})
        meta["plan_status"] = "done"
        goal.metadata_json = meta
        db.add(goal)
        db.commit()
        publish(
            org_id, EventType.PLAN_CREATED, "ceo", str(goal.id),
            payload={"title": plan.title},
            entities=[EntityRef(type="goal", id=str(goal.id)), EntityRef(type="plan", id=str(plan.id))],
        )
        publish_core_state(org_id, CoreState.IDLE, "ceo", str(goal.id))
        ran += 1
    return ran


def _task_entities(task: Task, run=None) -> list[EntityRef]:
    entities = [EntityRef(type="task", id=str(task.id))]
    if run is not None:
        entities.append(EntityRef(type="run", id=str(run.id)))
    return entities


def _forward_worker_event(organization_id, task: Task, run, event: dict) -> None:
    """Translates a claude_worker.cli_adapter on_event dict (already sanitized
    - no thinking/chain-of-thought content, secrets redacted) into a
    published realtime event. This is the wiring that lets the dashboard's
    3D core and live activity feed react to real agent activity instead of
    just idling - constitution section 15's run.tool.started/completed and
    run.output.delta events."""
    kind = event.get("kind")
    actor = task.assigned_agent_key or "claude-worker"
    entities = _task_entities(task, run)

    if kind == "tool_call":
        publish(
            organization_id, EventType.RUN_TOOL_STARTED, actor, str(task.id),
            payload={"tool_name": event.get("tool_name"), "input": event.get("input", {})},
            entities=entities,
        )
    elif kind == "tool_result":
        publish(
            organization_id, EventType.RUN_TOOL_COMPLETED, actor, str(task.id),
            payload={
                "tool_use_id": event.get("tool_use_id"),
                "is_error": event.get("is_error", False),
                "content_preview": event.get("content_preview", ""),
            },
            entities=entities,
        )
    elif kind in ("final_text", "text_fallback"):
        publish(
            organization_id, EventType.RUN_OUTPUT_DELTA, actor, str(task.id),
            payload={"text": event.get("text", ""), "final": kind == "final_text"},
            entities=entities,
        )
    # kind == "other" carries no safe/useful content - not forwarded.


def _execute_task(db, adapter: ClaudeCodeAdapter, task: Task) -> int:
    from app.models.work import Run

    org_id = task.organization_id
    actor = task.assigned_agent_key or "claude-worker"

    run = Run(organization_id=task.organization_id, task_id=task.id, agent_key=task.assigned_agent_key)
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        acquire_lease(db, task.id, run.id, worker_id=WORKER_ID, ttl_seconds=task.timeout_seconds + 60)
    except LeaseNotAcquired:
        db.delete(run)
        db.commit()
        return 0

    task.state = transition_task(TaskState(task.state), TaskState.ASSIGNED).value
    db.add(task)
    db.commit()
    publish(org_id, EventType.TASK_ASSIGNED, actor, str(task.id), payload={"title": task.title}, entities=_task_entities(task, run))

    try:
        task.state = transition_task(TaskState(task.state), TaskState.RUNNING).value
        run.state = "running"
        db.add(task)
        db.add(run)
        db.commit()
        publish(org_id, EventType.TASK_STARTED, actor, str(task.id), entities=_task_entities(task, run))
        core_state = CoreState.CODING if task.workspace_repo else CoreState.PLANNING
        publish_core_state(org_id, core_state, actor, str(task.id))

        contract = _task_to_contract(task)
        handle, wait = adapter.start_run(
            contract,
            run_id=str(run.id),
            on_event=lambda ev: _forward_worker_event(org_id, task, run, ev),
        )
        result = wait()

        run.exit_code = result.exit_code
        run.workspace_path = result.workspace_path
        run.branch_name = result.branch_name
        run.baseline_commit = result.baseline_commit
        run.result_summary = result.result_summary
        run.state = "validating" if result.exit_code == 0 and not result.timed_out else "failed"
        db.add(run)

        publish_core_state(org_id, CoreState.REVIEWING, actor, str(task.id))
        if run.state == "validating":
            next_state = TaskState.VALIDATING
            publish(org_id, EventType.TASK_PROGRESS, actor, str(task.id), payload={"stage": "validating"}, entities=_task_entities(task, run))
        else:
            next_state = TaskState.RETRY_WAIT
            publish(org_id, EventType.TASK_BLOCKED, actor, str(task.id), payload={"reason": "run_failed_or_timed_out"}, entities=_task_entities(task, run))
        task.state = transition_task(TaskState(task.state), next_state).value
        db.add(task)
        db.commit()
        publish_core_state(org_id, CoreState.IDLE, actor, str(task.id))
        return 1
    except AuthenticationRequiredError:
        logger.error("Claude CLI not authenticated - pausing this task, will retry once an operator logs in.")
        db.rollback()
        db.refresh(task)
        task.state = transition_task(TaskState(task.state), TaskState.BLOCKED).value
        db.add(task)
        db.commit()
        publish_core_state(org_id, CoreState.PAUSED, actor, str(task.id))
        publish(
            org_id, EventType.TASK_BLOCKED, actor, str(task.id),
            payload={"reason": "claude_cli_not_authenticated"}, entities=_task_entities(task, run),
        )
        return 0
    except Exception:  # noqa: BLE001 - never leave a task stuck in RUNNING
        # A missing CLI, a subprocess timeout, a worktree collision, a Redis
        # publish blip - anything other than an auth error - would otherwise
        # propagate out with the task committed as RUNNING and no lease-free
        # path back to READY. Land it in RETRY_WAIT (so the progression driver
        # can retry it) or FAILED if the current state can't retry.
        logger.exception("Unexpected error executing task %s - marking recoverable", task.id)
        db.rollback()
        try:
            db.refresh(task)
        except Exception:  # noqa: BLE001
            pass
        _mark_task_recoverable(db, task, run, org_id, actor)
        return 0
    finally:
        release_lease(db, task.id, worker_id=WORKER_ID)


def _mark_task_recoverable(db, task: "Task", run, org_id, actor: str) -> None:
    """Best-effort transition of a task out of RUNNING after an unexpected
    failure: RETRY_WAIT if allowed, else FAILED, else left as-is with the run
    marked failed. Swallows secondary errors so the poll loop keeps running."""
    for target in (TaskState.RETRY_WAIT, TaskState.FAILED):
        try:
            task.state = transition_task(TaskState(task.state), target).value
            break
        except InvalidTransition:
            continue
    try:
        if run is not None:
            run.state = "failed"
            db.add(run)
        db.add(task)
        db.commit()
        publish(
            org_id, EventType.TASK_BLOCKED, actor, str(task.id),
            payload={"reason": "worker_execution_error"}, entities=_task_entities(task, run),
        )
        publish_core_state(org_id, CoreState.WARNING, actor, str(task.id))
    except Exception:  # noqa: BLE001
        logger.exception("Failed to record recoverable state for task %s", task.id)
        db.rollback()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    adapter = ClaudeCodeAdapter(cli_path=settings.claude_cli_path, workspace_root=settings.claude_worker_workspace_root)

    try:
        auth = adapter.check_auth()
        logger.info("Claude CLI authenticated: %s", auth)
    except AuthenticationRequiredError as exc:
        logger.warning("Starting in degraded mode - %s", exc)

    SessionLocal = get_sessionmaker()
    logger.info("claude-worker %s starting poll loop (interval=%ss)", WORKER_ID, POLL_INTERVAL_SECONDS)
    while True:
        db = SessionLocal()
        try:
            executed = run_once(db, adapter, agent_concurrency_limits={})
            if executed:
                logger.info("Executed %d task(s) this tick", executed)
        except Exception:  # noqa: BLE001
            logger.exception("Error during poll tick")
        finally:
            db.close()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
