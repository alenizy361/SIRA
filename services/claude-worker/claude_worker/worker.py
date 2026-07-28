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
from datetime import datetime, timezone
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
from app.models.work import Task, ToolCall  # noqa: E402
from app.realtime.publisher import publish, publish_core_state  # noqa: E402
from contracts.events import CoreState, EntityRef, EventType  # noqa: E402
from orchestrator.leasing import LeaseNotAcquired, acquire_lease, expire_stale_leases, release_lease  # noqa: E402
from orchestrator.progression import advance_ready_pipeline  # noqa: E402
from orchestrator.scheduler import pick_ready_tasks  # noqa: E402
from orchestrator.state_machine import GoalState, InvalidTransition, TaskState, transition_goal, transition_task  # noqa: E402
from sqlalchemy.orm import Session as OrmSession  # noqa: E402

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
        allowed_tools=ac.get("allowed_tools", ["Read", "Grep", "Glob", "Write", "Edit"]),
        prohibited_actions=ac.get("prohibited_actions", []),
        acceptance_criteria=ac.get("criteria", []),
        output_schema=None,
        timeout_seconds=task.timeout_seconds,
        risk_level=task.risk_level,
        workspace_repo=task.workspace_repo,
    )


# The linear happy-path GoalState order (skipping the optional
# APPROVAL_PENDING/PREVIEW_READY branches, which nothing in this codebase ever
# enters) - used to walk a goal forward from wherever it currently sits once
# every one of its tasks has finished.
_GOAL_HAPPY_PATH = [
    GoalState.PLAN_DRAFTED, GoalState.PLAN_REVIEWED, GoalState.RISK_CLASSIFIED,
    GoalState.READY, GoalState.ASSIGNED, GoalState.RUNNING, GoalState.VALIDATING,
    GoalState.REVIEWING, GoalState.MEASURING, GoalState.COMPLETED,
]


def _advance_completed_goals(db, org_id) -> int:
    """Advances a goal to COMPLETED once every task under its plan has reached
    a terminal state (completed/cancelled).

    Goal.state was previously written in exactly two places: planning.py
    (which stops at PLAN_DRAFTED) and the manual /goals/{id}/transition
    endpoint. Nothing ever advanced it further - so a goal whose CEO plan had
    already 100% completed (every task done) sat showing "plan_drafted"
    forever. The dashboard, and the operator, had no way to tell a genuinely
    finished goal apart from one that had barely started. This is DB-only
    (no CLI call), so it runs every tick regardless of Claude auth state, same
    as advance_ready_pipeline.
    """
    from app.models.company import Goal
    from app.models.work import Plan, Task
    from contracts.events import EntityRef, EventType

    _TERMINAL_OK = {TaskState.COMPLETED.value, TaskState.CANCELLED.value}
    _IN_FLIGHT = [s.value for s in _GOAL_HAPPY_PATH[:-1]]  # everything but COMPLETED itself

    goals = (
        db.query(Goal)
        .filter(Goal.organization_id == org_id, Goal.state.in_(_IN_FLIGHT))
        .all()
    )
    advanced = 0
    for goal in goals:
        try:
            current = GoalState(goal.state)
            idx = _GOAL_HAPPY_PATH.index(current)
        except ValueError:
            continue  # goal is in a branch state (approval_pending/preview_ready) - not handled here

        plan_ids = [row[0] for row in db.query(Plan.id).filter(Plan.goal_id == goal.id).all()]
        if not plan_ids:
            continue
        task_states = [row[0] for row in db.query(Task.state).filter(Task.plan_id.in_(plan_ids)).all()]
        if not task_states or not all(s in _TERMINAL_OK for s in task_states):
            continue  # still has work in flight, or nothing was ever created

        state = current
        for target in _GOAL_HAPPY_PATH[idx + 1:]:
            state = transition_goal(state, target)
        goal.state = state.value
        db.add(goal)
        db.commit()
        publish(
            org_id, EventType.GOAL_COMPLETED, "ceo", str(goal.id),
            payload={"title": goal.title}, entities=[EntityRef(type="goal", id=str(goal.id))],
        )
        advanced += 1
    return advanced


# Autonomy modes that permit the poll loop to pick up new work. Emergency
# stop (POST /system/emergency-stop) sets an organization to "observe_only",
# which must actually stop new task assignment here - constitution section
# 22: "Emergency stop must immediately: stop new task assignment..." -
# revoking leases alone (which the API route does) is not enough on its
# own without this check, since a fresh READY task has no lease yet.
AUTONOMOUS_EXECUTION_MODES = {"execute_low_risk", "controlled_autonomous"}


def _write_worker_heartbeat(authed: bool) -> None:
    """Publish a short-TTL liveness beacon to Redis so the API (and thus the
    dashboard) can tell whether the company is actually running, and whether
    the CLI is authenticated. Best-effort - never breaks the poll loop."""
    try:
        import json as _json

        import redis as _redis

        from app.config import get_settings

        client = _redis.from_url(get_settings().redis_url, socket_timeout=3, socket_connect_timeout=3)
        payload = _json.dumps({
            "worker_id": WORKER_ID,
            "authed": bool(authed),
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # TTL a few ticks long: if the worker dies, the key expires and the API
        # reports it down.
        client.set("rabit:worker:heartbeat", payload, ex=int(POLL_INTERVAL_SECONDS * 4) + 10)
    except Exception:  # noqa: BLE001
        logger.debug("heartbeat write failed", exc_info=True)


def run_once(db, adapter: ClaudeCodeAdapter, agent_concurrency_limits: dict[str, int]) -> int:
    """Runs a single poll tick across all organizations. Returns the number
    of tasks executed (0 on an idle tick - this is the common case)."""
    from app.models.identity import Organization

    executed = 0
    # Reclaim leases stranded by a crashed/killed worker so their tasks become
    # schedulable again instead of being stuck forever behind a dead lease
    # (constitution section 6 restart-recovery). Cheap and global; runs once.
    expire_stale_leases(db)

    # One auth probe per tick. If the CLI session has lapsed, DON'T touch the
    # CLI at all: leave requested goals 'requested' and ready tasks READY so
    # they resume the moment an operator re-authenticates - never burn retries
    # or strand work during a login outage.
    authed = True
    try:
        adapter.check_auth()
    except AuthenticationRequiredError:
        authed = False
        logger.warning("Claude CLI not authenticated - skipping CLI work this tick; queued work is preserved.")

    # Beacon liveness + auth so the dashboard can show "company running" vs
    # "asleep - run claude auth login".
    _write_worker_heartbeat(authed)

    for org_id, autonomy_mode in db.query(Organization.id, Organization.autonomy_mode).all():
        if autonomy_mode not in AUTONOMOUS_EXECUTION_MODES:
            continue
        # DB-only progression always runs (no CLI): requeue orphaned tasks from
        # a dead worker, run retry backoff, cancel dead-dependency branches.
        advance_ready_pipeline(db, org_id)
        # Also DB-only: a goal whose every task has finished must actually
        # reach COMPLETED, not sit at plan_drafted forever.
        _advance_completed_goals(db, org_id)
        if not authed:
            continue
        # 1. Goals whose owner asked the CEO agent to plan them (needs the
        #    host CLI session - see request_plan() in routers/goals.py).
        executed += _run_pending_plans(db, org_id)
        # 2. Tasks a plan produced, ready for an engineer/analyst agent to run.
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

    from datetime import datetime, timezone

    ran = 0
    goals = (
        db.query(Goal)
        .filter(Goal.organization_id == org_id, Goal.state == "goal_captured")
        .all()
    )
    # Reset DEAD planning claims first: a goal stamped plan_status='running'
    # whose worker died mid-plan (or whose PLANNING publish raised) is never
    # re-selected and shows the CEO "planning" forever. A running claim older
    # than the planning timeout is dead - hand it back to 'requested'.
    now = datetime.now(timezone.utc)
    STALE_PLAN_SECONDS = 600  # >> planning timeout (180s)
    for goal in goals:
        meta = dict(goal.metadata_json or {})
        if meta.get("plan_status") != "running":
            continue
        claimed = meta.get("plan_claimed_at")
        stale = True
        if claimed:
            try:
                started = datetime.fromisoformat(claimed)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                stale = (now - started).total_seconds() > STALE_PLAN_SECONDS
            except ValueError:
                stale = True
        if stale:
            logger.warning("Resetting dead planning claim on goal %s -> requested", goal.id)
            meta["plan_status"] = "requested"
            goal.metadata_json = meta
            db.add(goal)
            db.commit()

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
        locked_meta["plan_claimed_at"] = now.isoformat()
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


def _forward_worker_event(organization_id, task: Task, run, event: dict, tool_call_ids: dict, engine) -> None:
    """Translates a claude_worker.cli_adapter on_event dict (already sanitized
    - no thinking/chain-of-thought content, secrets redacted) into a
    published realtime event, AND persists a durable ToolCall row - the
    transparent "work log" (what tool was called, with what input, what it
    returned) that industry agent UIs (LangSmith traces, Devin's work log)
    treat as core transparency, not optional. Before this, tool-call data only
    ever existed on the transient Redis pub/sub stream - lost the moment you
    reloaded the page, even though the ToolCall table existed in the schema
    the whole time with nothing ever writing to it.

    IMPORTANT: this runs on the CLI adapter's background stdout-pump THREAD
    (joined with only a 2s timeout, so genuinely concurrent with the caller),
    while the caller's `db` session is a live SQLAlchemy Session - NOT
    thread-safe to share. Every write here opens its own short-lived session -
    bound to the SAME `engine` the caller's session already uses (passed in
    explicitly), never a freshly resolved get_sessionmaker(). Engines (unlike
    Sessions) are safe to share across threads; resolving a second one via
    get_settings() risks silently targeting a DIFFERENT database than the
    caller if DATABASE_URL differs between the two resolution points (a real
    bug this shipped with: it produced a ForeignKeyViolation in tests where
    the test's engine and the global cached one pointed at different
    databases - the caller's own bind removes that class of bug entirely).
    """
    kind = event.get("kind")
    actor = task.assigned_agent_key or "claude-worker"
    entities = _task_entities(task, run)

    if kind == "tool_call":
        publish(
            organization_id, EventType.RUN_TOOL_STARTED, actor, str(task.id),
            payload={"tool_name": event.get("tool_name"), "input": event.get("input", {})},
            entities=entities,
        )
        session = OrmSession(bind=engine)
        try:
            tc = ToolCall(
                run_id=run.id,
                tool_name=event.get("tool_name") or "unknown",
                input_summary=event.get("input") or {},
                output_summary={},
                started_at=datetime.now(timezone.utc),
            )
            session.add(tc)
            session.commit()
            session.refresh(tc)
            tool_use_id = event.get("id")
            if tool_use_id:
                tool_call_ids[tool_use_id] = tc.id
        except Exception:  # noqa: BLE001
            logger.debug("failed to persist ToolCall row", exc_info=True)
            session.rollback()
        finally:
            session.close()
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
        tc_id = tool_call_ids.pop(event.get("tool_use_id"), None) if event.get("tool_use_id") else None
        if tc_id:
            session = OrmSession(bind=engine)
            try:
                tc = session.get(ToolCall, tc_id)
                if tc:
                    tc.output_summary = {"preview": event.get("content_preview", "")}
                    tc.succeeded = not event.get("is_error", False)
                    tc.finished_at = datetime.now(timezone.utc)
                    session.add(tc)
                    session.commit()
            except Exception:  # noqa: BLE001
                logger.debug("failed to update ToolCall row", exc_info=True)
                session.rollback()
            finally:
                session.close()
    elif kind in ("final_text", "text_fallback"):
        publish(
            organization_id, EventType.RUN_OUTPUT_DELTA, actor, str(task.id),
            payload={"text": event.get("text", ""), "final": kind == "final_text"},
            entities=entities,
        )
    # kind == "other" carries no safe/useful content - not forwarded.


def _make_is_cancelled(task_id: uuid.UUID, engine):
    """Returns a closure polled by the adapter's run loop (throttled to
    once/second there) to check whether an operator has requested this task's
    in-flight run be stopped - the "interrupt a specific agent" primitive
    Anthropic's Managed Agents API exposes as `user.interrupt`, which this
    worker had no equivalent of until now (previously a started subprocess
    ran to completion or its own timeout, full stop).

    Opens its own short-lived session bound to the caller's engine (never a
    freshly resolved get_sessionmaker() - see _forward_worker_event's
    docstring for why that class of bug matters) since a Cancellation row is
    written by the API process from an entirely separate request/session.
    """

    def _is_cancelled() -> bool:
        from app.models.work import Cancellation

        session = OrmSession(bind=engine)
        try:
            return (
                session.query(Cancellation.id)
                .filter(Cancellation.task_id == task_id)
                .first()
                is not None
            )
        finally:
            session.close()

    return _is_cancelled


def _org_budget_exceeded(db, org_id) -> bool:
    """True only when an operator has actually configured an org-level
    monthly cap (Budget.scope == "org") AND spend + reserved has reached it.
    No Budget row at all means unconfigured - never gates anything, so this
    is opt-in enforcement, not a silent default limit. Real dollar spend
    comes from the CLI's own reported total_cost_usd (see cli_adapter's
    "cost" event and _commit_run_cost below) - not an estimate."""
    from app.models.integrations import Budget

    budget = (
        db.query(Budget)
        .filter(Budget.organization_id == org_id, Budget.scope == "org")
        .first()
    )
    if budget is None or float(budget.monthly_max) <= 0:
        return False
    return float(budget.spent_amount) + float(budget.reserved_amount) >= float(budget.monthly_max)


def _commit_run_cost(db, org_id, run_id: uuid.UUID, cost_usd) -> None:
    """Records the CLI's real reported spend against the org's Budget row, if
    one is configured. Idempotent per run (idempotency_key derived from
    run_id) so re-processing the same run (e.g. after a crash mid-commit)
    never double-counts its cost."""
    if not cost_usd:
        return
    from app.models.integrations import Budget, BudgetTransaction

    budget = (
        db.query(Budget)
        .filter(Budget.organization_id == org_id, Budget.scope == "org")
        .first()
    )
    if budget is None:
        return
    idempotency_key = f"run-cost-{run_id}"
    already_committed = (
        db.query(BudgetTransaction.id)
        .filter(BudgetTransaction.idempotency_key == idempotency_key)
        .first()
    )
    if already_committed:
        return
    db.add(BudgetTransaction(
        budget_id=budget.id, idempotency_key=idempotency_key, kind="commit",
        amount=cost_usd, created_at=datetime.now(timezone.utc),
    ))
    budget.spent_amount = float(budget.spent_amount) + float(cost_usd)
    db.add(budget)


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

    try:
        # The READY->ASSIGNED step is INSIDE the try/finally now so a failure
        # here (e.g. a Redis publish blip, or a stale pick where another worker
        # already advanced this task past READY) still releases the lease and
        # lands the task in a recoverable state rather than wedging it.
        try:
            task.state = transition_task(TaskState(task.state), TaskState.ASSIGNED).value
        except InvalidTransition:
            # Stale snapshot: this task is no longer READY (another worker took
            # it). Drop our orphan Run + lease and move on.
            logger.info("Task %s no longer READY at assign time - skipping (stale pick)", task.id)
            db.rollback()
            db.delete(run)
            db.commit()
            release_lease(db, task.id, worker_id=WORKER_ID)
            return 0
        db.add(task)
        db.commit()
        publish(org_id, EventType.TASK_ASSIGNED, actor, str(task.id), payload={"title": task.title}, entities=_task_entities(task, run))

        task.state = transition_task(TaskState(task.state), TaskState.RUNNING).value
        run.state = "running"
        db.add(task)
        db.add(run)
        db.commit()
        publish(org_id, EventType.TASK_STARTED, actor, str(task.id), entities=_task_entities(task, run))
        core_state = CoreState.CODING if task.workspace_repo else CoreState.PLANNING
        publish_core_state(org_id, core_state, actor, str(task.id))

        if _org_budget_exceeded(db, org_id):
            # Refuse to spend a single further dollar once the org's monthly
            # cap is hit - the CLI is never invoked. Reuses the same
            # RUNNING->RETRY_WAIT path any other failure takes (auth blip,
            # timeout, ...) rather than inventing a new FSM edge; the
            # progression driver will keep retrying on its normal backoff,
            # which naturally stops blocking once spend resets or the cap is
            # raised. An org with no Budget row configured is never gated -
            # this is opt-in enforcement, not a default limit nobody asked for.
            logger.warning("Task %s blocked - organization %s is over its monthly budget", task.id, org_id)
            run.state = "failed"
            run.finished_at = datetime.now(timezone.utc)
            db.add(run)
            publish(org_id, EventType.TASK_BLOCKED, actor, str(task.id), payload={"reason": "budget_exceeded"}, entities=_task_entities(task, run))
            task.state = transition_task(TaskState(task.state), TaskState.RETRY_WAIT).value
            db.add(task)
            db.commit()
            publish_core_state(org_id, CoreState.IDLE, actor, str(task.id))
            return 0

        contract = _task_to_contract(task)
        # Correlates a tool_call to its later tool_result so the persisted
        # ToolCall row gets updated in place rather than duplicated - local to
        # this one run, not shared across tasks.
        tool_call_ids: dict[str, uuid.UUID] = {}
        handle, wait = adapter.start_run(
            contract,
            run_id=str(run.id),
            on_event=lambda ev: _forward_worker_event(org_id, task, run, ev, tool_call_ids, db.get_bind()),
            is_cancelled=_make_is_cancelled(task.id, db.get_bind()),
        )
        result = wait()

        run.exit_code = result.exit_code
        run.workspace_path = result.workspace_path
        run.branch_name = result.branch_name
        run.baseline_commit = result.baseline_commit
        run.result_summary = result.result_summary
        run.cost_usd = result.cost_usd
        # Spend is real the moment the CLI reports it - a cancelled, timed-out,
        # or failed run can still have burned real tokens, so this is recorded
        # unconditionally rather than only on the success path.
        _commit_run_cost(db, org_id, run.id, result.cost_usd)

        if result.cancelled and not result.timed_out:
            # A genuine operator-requested cancel (as opposed to a timeout,
            # which also calls handle.cancel() and sets .cancelled - the
            # timed_out flag is what disambiguates the two).
            run.state = "cancelled"
            run.finished_at = datetime.now(timezone.utc)
            db.add(run)
            task.state = transition_task(TaskState(task.state), TaskState.CANCELLED).value
            db.add(task)
            db.commit()
            publish(org_id, EventType.TASK_CANCELLED, actor, str(task.id), payload={"title": task.title}, entities=_task_entities(task, run))
            publish_core_state(org_id, CoreState.IDLE, actor, str(task.id))
            return 1

        run.state = "validating" if result.exit_code == 0 and not result.timed_out else "failed"
        db.add(run)

        publish_core_state(org_id, CoreState.REVIEWING, actor, str(task.id))
        if run.state == "validating":
            # SUCCESS: drive the task through validation + review to COMPLETED
            # and announce it. This build has no separate validator/reviewer
            # agent consuming the VALIDATING/REVIEWING states, so a clean run
            # finalizes the task here. Without this the task would sit in
            # VALIDATING forever - the goal would never finish and the
            # dashboard would never see a task actually complete.
            publish(org_id, EventType.TASK_PROGRESS, actor, str(task.id), payload={"stage": "validating"}, entities=_task_entities(task, run))
            for target in (TaskState.VALIDATING, TaskState.REVIEWING, TaskState.COMPLETED):
                task.state = transition_task(TaskState(task.state), target).value
            run.state = "completed"
            run.finished_at = datetime.now(timezone.utc)
            db.add(task)
            db.add(run)
            db.commit()
            publish(org_id, EventType.TASK_COMPLETED, actor, str(task.id), payload={"title": task.title}, entities=_task_entities(task, run))
        else:
            publish(org_id, EventType.TASK_BLOCKED, actor, str(task.id), payload={"reason": "run_failed_or_timed_out"}, entities=_task_entities(task, run))
            task.state = transition_task(TaskState(task.state), TaskState.RETRY_WAIT).value
            db.add(task)
            db.commit()
        publish_core_state(org_id, CoreState.IDLE, actor, str(task.id))
        return 1
    except AuthenticationRequiredError:
        # Land in RETRY_WAIT (not BLOCKED): BLOCKED has no automatic path back
        # to READY, so an auth blip during a run would strand the task forever.
        # RETRY_WAIT is picked up by the backoff driver once the operator
        # re-authenticates. (run_once's per-tick auth gate normally prevents
        # ever reaching here, so retries are rarely burned.)
        logger.error("Claude CLI not authenticated mid-run - requeuing task for retry after login.")
        db.rollback()
        try:
            db.refresh(task)
        except Exception:  # noqa: BLE001
            pass
        _mark_task_recoverable(db, task, run, org_id, actor)
        publish_core_state(org_id, CoreState.PAUSED, actor, str(task.id))
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
