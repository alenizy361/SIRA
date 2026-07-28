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
# Casual chat is a separate, lightweight surface from the goal/plan/task
# pipeline (constitution: a plain "hi" should not spin up a full CEO planning
# run and a multi-agent task tree). Cheapest/fastest current model, and
# no_tools=True on the contract means the CLI can't be coaxed into touching
# the filesystem for what is meant to be pure conversation.
CHAT_MODEL = os.environ.get("CLAUDE_WORKER_CHAT_MODEL", "claude-haiku-4-5")
# How long a claimed-but-unfinished chat turn is still treated as "in
# flight" before another poll tick is allowed to reclaim it - guards against
# a crashed worker leaving a session permanently stuck claimed.
CHAT_CLAIM_TTL_SECONDS = 120
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


def _reply_to_contract(task: Task, message_body: str) -> TaskContract:
    """A follow-up turn in an existing conversation, not a new task: the
    mission is the human's own message, not the original task description.
    Reuses the task's own tool allow-list/risk level/workspace, since the
    session being resumed already carries the original mission's full
    context - restating it here would be redundant, and could actively
    confuse a model mid-conversation."""
    ac = task.acceptance_criteria or {}
    return TaskContract(
        task_id=str(task.id),
        mission=message_body,
        context=f"Follow-up message on task: {task.title}",
        constraints=ac.get("constraints", []),
        allowed_tools=ac.get("allowed_tools", ["Read", "Grep", "Glob", "Write", "Edit"]),
        prohibited_actions=ac.get("prohibited_actions", []),
        acceptance_criteria=[],
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


def _chat_contract(message_body: str, chat_session_id: uuid.UUID) -> TaskContract:
    """A single turn of the lightweight, always-available casual-chat
    surface - deliberately NOT a task: no goal, no plan, no multi-agent
    tree, no filesystem access (no_tools=True), and the cheapest/fastest
    current model rather than whatever the task pipeline would pick. This
    is the "just reply" path the CEO itself recommended after repeatedly
    seeing plain greetings routed through the full planning pipeline."""
    return TaskContract(
        task_id=f"chat-{chat_session_id}",
        mission=message_body,
        context="A casual conversational message - not a work request. Reply naturally and briefly.",
        constraints=["Do not use any tools.", "Keep the reply conversational and brief."],
        allowed_tools=[],
        prohibited_actions=[],
        acceptance_criteria=[],
        output_schema=None,
        timeout_seconds=60,
        risk_level="R0",
        model=CHAT_MODEL,
        no_tools=True,
    )


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
        # 3. Human follow-up messages awaiting a reply - resumes the task's
        #    existing CLI session rather than starting a fresh task.
        for task, message in _pending_reply_requests(db, org_id):
            executed += _execute_reply(db, adapter, task, message)
        # 4. Pending casual-chat turns - the separate, always-available
        #    conversational surface (no goal/plan/task involved at all).
        for session, message in _pending_chat_requests(db, org_id):
            executed += _execute_chat_turn(db, adapter, session, message)
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


def _relevant_budgets(db, org_id, agent_key: str | None):
    """Every Budget row that gates this task: the org-wide cap plus (if
    configured) a cap scoped to this specific agent. Modeled on Paperclip's
    budget_policies, which key on (company, scope_type, scope_ref) so
    company-wide and per-agent caps can both be active at once rather than
    forcing a single flat limit."""
    from app.models.integrations import Budget

    filters = [Budget.organization_id == org_id, Budget.scope == "org"]
    query = db.query(Budget).filter(*filters)
    budgets = list(query.all())
    if agent_key:
        agent_budget = (
            db.query(Budget)
            .filter(Budget.organization_id == org_id, Budget.scope == "agent", Budget.scope_ref == agent_key)
            .first()
        )
        if agent_budget is not None:
            budgets.append(agent_budget)
    return budgets


def _budget_gate(db, org_id, agent_key: str | None):
    """Checks every relevant Budget row and returns (blocking_budget, warned)
    - blocking_budget is the first Budget at/over its monthly cap (None if
    none are), warned is the list of budgets newly crossing warn_percent this
    call (each already flipped to warned_at=now so a later call won't
    re-report the same crossing). No Budget row configured for a scope means
    that scope is never gated - this is opt-in enforcement, not a silent
    default limit."""
    warned = []
    blocking = None
    for budget in _relevant_budgets(db, org_id, agent_key):
        if float(budget.monthly_max) <= 0:
            continue
        used = float(budget.spent_amount) + float(budget.reserved_amount)
        cap = float(budget.monthly_max)
        if blocking is None and used >= cap:
            blocking = budget
            continue
        if budget.warned_at is None and cap > 0 and used >= cap * (float(budget.warn_percent) / 100.0):
            budget.warned_at = datetime.now(timezone.utc)
            db.add(budget)
            warned.append(budget)
    return blocking, warned


def _commit_run_cost(db, org_id, agent_key: str | None, run_id: uuid.UUID, cost_usd) -> None:
    """Records the CLI's real reported spend against every relevant Budget
    row (org-wide and, if configured, this agent's own). Idempotent per
    (budget, run) pair so re-processing the same run (e.g. after a crash
    mid-commit) never double-counts its cost, and a run charged against two
    scopes doesn't collide on one idempotency key."""
    if not cost_usd:
        return
    from app.models.integrations import BudgetTransaction

    for budget in _relevant_budgets(db, org_id, agent_key):
        idempotency_key = f"run-cost-{run_id}-{budget.id}"
        already_committed = (
            db.query(BudgetTransaction.id)
            .filter(BudgetTransaction.idempotency_key == idempotency_key)
            .first()
        )
        if already_committed:
            continue
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

        blocking_budget, warned_budgets = _budget_gate(db, org_id, task.assigned_agent_key)
        for wb in warned_budgets:
            publish(
                org_id, EventType.BUDGET_THRESHOLD_REACHED, actor, str(task.id),
                payload={
                    "scope": wb.scope, "scope_ref": wb.scope_ref,
                    "spent_amount": float(wb.spent_amount), "monthly_max": float(wb.monthly_max),
                    "warn_percent": float(wb.warn_percent),
                },
                entities=_task_entities(task, run),
            )
        db.commit()

        if blocking_budget is not None:
            # Refuse to spend a single further dollar once a relevant cap
            # (org-wide or this agent's own) is hit - the CLI is never
            # invoked. Reuses the same RUNNING->RETRY_WAIT path any other
            # failure takes (auth blip, timeout, ...) rather than inventing a
            # new FSM edge; the progression driver will keep retrying on its
            # normal backoff, which naturally stops blocking once spend
            # resets or the cap is raised. A scope with no Budget row
            # configured is never gated - this is opt-in enforcement, not a
            # default limit nobody asked for.
            logger.warning(
                "Task %s blocked - %s budget '%s' is over its monthly cap",
                task.id, blocking_budget.scope, blocking_budget.scope_ref,
            )
            run.state = "failed"
            run.finished_at = datetime.now(timezone.utc)
            db.add(run)
            publish(
                org_id, EventType.TASK_BLOCKED, actor, str(task.id),
                payload={"reason": "budget_exceeded", "scope": blocking_budget.scope, "scope_ref": blocking_budget.scope_ref},
                entities=_task_entities(task, run),
            )
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
        run.cli_session_id = result.cli_session_id
        # Spend is real the moment the CLI reports it - a cancelled, timed-out,
        # or failed run can still have burned real tokens, so this is recorded
        # unconditionally rather than only on the success path.
        _commit_run_cost(db, org_id, task.assigned_agent_key, run.id, result.cost_usd)

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


def _pending_reply_requests(db, org_id):
    """Tasks whose most recent TaskMessage is a human message still awaiting
    a reply. A task's most-recent message being role="human" IS the
    "unanswered" condition - the worker always writes the agent's reply as
    its own row right after producing it, so nothing but a fresh human
    message can leave a task in this state. Skips any task with a currently
    held RunLease (its original run, or an earlier reply, is still in
    flight) so two poll ticks can never process the same conversation at
    once."""
    from sqlalchemy import and_, func

    from app.models.work import RunLease, Task, TaskMessage

    latest_ts = (
        db.query(TaskMessage.task_id, func.max(TaskMessage.created_at).label("max_ts"))
        .filter(TaskMessage.organization_id == org_id)
        .group_by(TaskMessage.task_id)
        .subquery()
    )
    pending_messages = (
        db.query(TaskMessage)
        .join(latest_ts, and_(TaskMessage.task_id == latest_ts.c.task_id, TaskMessage.created_at == latest_ts.c.max_ts))
        .filter(TaskMessage.role == "human")
        .all()
    )
    if not pending_messages:
        return []
    leased_task_ids = {row[0] for row in db.query(RunLease.task_id).all()}
    out = []
    for message in pending_messages:
        if message.task_id in leased_task_ids:
            continue
        task = db.query(Task).filter(Task.id == message.task_id, Task.organization_id == org_id).first()
        if task is not None:
            out.append((task, message))
    return out


def _execute_reply(db, adapter: ClaudeCodeAdapter, task: Task, message) -> int:
    """Processes one pending human TaskMessage as the next turn of the
    task's existing CLI conversation (see _reply_to_contract and
    cli_adapter.start_run's resume_session_id) - a real continued dialogue,
    not a new one-shot task. Never touches task.state: a reply is a
    conversation turn, not a step in the goal/task FSM."""
    from app.models.work import Run, TaskMessage

    org_id = task.organization_id
    actor = task.assigned_agent_key or "claude-worker"

    last_run = (
        db.query(Run)
        .filter(Run.task_id == task.id, Run.cli_session_id.isnot(None))
        .order_by(Run.created_at.desc())
        .first()
    )
    if last_run is None:
        # No prior session to resume - the original run never captured one
        # (e.g. an older run predating this feature, or it failed before the
        # CLI ever emitted its init message). Nothing safe to do but skip;
        # an operator has to re-run the original task first.
        logger.warning("Task %s has a pending reply but no prior CLI session to resume - skipping", task.id)
        return 0
    resume_session_id = last_run.cli_session_id

    run = Run(organization_id=org_id, task_id=task.id, agent_key=task.assigned_agent_key, state="running")
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
        blocking_budget, warned_budgets = _budget_gate(db, org_id, task.assigned_agent_key)
        for wb in warned_budgets:
            publish(
                org_id, EventType.BUDGET_THRESHOLD_REACHED, actor, str(task.id),
                payload={
                    "scope": wb.scope, "scope_ref": wb.scope_ref,
                    "spent_amount": float(wb.spent_amount), "monthly_max": float(wb.monthly_max),
                    "warn_percent": float(wb.warn_percent),
                },
            )
        db.commit()
        if blocking_budget is not None:
            run.state = "failed"
            run.finished_at = datetime.now(timezone.utc)
            db.add(run)
            db.commit()
            return 0

        contract = _reply_to_contract(task, message.body)
        tool_call_ids: dict[str, uuid.UUID] = {}
        handle, wait = adapter.start_run(
            contract,
            run_id=str(run.id),
            on_event=lambda ev: _forward_worker_event(org_id, task, run, ev, tool_call_ids, db.get_bind()),
            is_cancelled=_make_is_cancelled(task.id, db.get_bind()),
            resume_session_id=resume_session_id,
        )
        result = wait()

        run.exit_code = result.exit_code
        run.result_summary = result.result_summary
        run.cost_usd = result.cost_usd
        run.cli_session_id = result.cli_session_id or resume_session_id
        run.finished_at = datetime.now(timezone.utc)
        _commit_run_cost(db, org_id, task.assigned_agent_key, run.id, result.cost_usd)

        if result.exit_code == 0 and not result.timed_out and not result.cancelled:
            run.state = "completed"
            reply_body = result.result_summary or "(the agent produced no textual reply)"
        else:
            run.state = "failed"
            reply_body = f"(the agent failed to reply: exit={result.exit_code}, timed_out={result.timed_out})"
        db.add(run)
        db.add(TaskMessage(
            organization_id=org_id, task_id=task.id, role="agent", body=reply_body,
            run_id=run.id, created_at=datetime.now(timezone.utc),
        ))
        db.commit()

        publish(
            org_id, EventType.TASK_MESSAGE, actor, str(task.id),
            payload={"role": "agent", "body": reply_body},
            entities=_task_entities(task, run),
        )
        return 1
    except Exception:  # noqa: BLE001 - never let one bad reply crash the poll tick
        logger.exception("Unexpected error replying to task %s - leaving message unanswered for retry", task.id)
        db.rollback()
        try:
            db.refresh(run)
            run.state = "failed"
            db.add(run)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return 0
    finally:
        release_lease(db, task.id, worker_id=WORKER_ID)


def _pending_chat_requests(db, org_id):
    """ChatSessions whose latest ChatMessage is an unanswered human message
    and are not currently claimed (or whose claim has gone stale past
    CHAT_CLAIM_TTL_SECONDS - guards a crashed worker leaving a session
    permanently stuck claimed). Mirrors _pending_reply_requests, but chat has
    no Task/RunLease to anchor a lease to, so the claim lives directly on
    ChatSession.claimed_at/claimed_by instead."""
    from sqlalchemy import and_, func

    from app.models.chat import ChatMessage, ChatSession

    latest_ts = (
        db.query(ChatMessage.chat_session_id, func.max(ChatMessage.created_at).label("max_ts"))
        .filter(ChatMessage.organization_id == org_id)
        .group_by(ChatMessage.chat_session_id)
        .subquery()
    )
    pending_messages = (
        db.query(ChatMessage)
        .join(latest_ts, and_(
            ChatMessage.chat_session_id == latest_ts.c.chat_session_id,
            ChatMessage.created_at == latest_ts.c.max_ts,
        ))
        .filter(ChatMessage.role == "human")
        .all()
    )
    if not pending_messages:
        return []

    now = datetime.now(timezone.utc)
    out = []
    for message in pending_messages:
        session = (
            db.query(ChatSession)
            .filter(ChatSession.id == message.chat_session_id, ChatSession.organization_id == org_id)
            .first()
        )
        if session is None:
            continue
        if session.claimed_at is not None:
            claimed_at = session.claimed_at
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            if (now - claimed_at).total_seconds() < CHAT_CLAIM_TTL_SECONDS:
                continue  # another tick (or a still-running worker) already has this
        out.append((session, message))
    return out


def _execute_chat_turn(db, adapter: ClaudeCodeAdapter, session, message) -> int:
    """Processes one pending human ChatMessage as the next turn of the org's
    single casual-chat session - resumes the existing CLI session
    (session.cli_session_id) if one exists, the same --resume pattern as
    _execute_reply, but with no Task/Goal/Plan involved at all. Never touches
    the goal/task FSM; this is a parallel, independent conversational
    surface."""
    from app.models.chat import ChatMessage, ChatSession

    org_id = session.organization_id

    # Claim atomically under a row lock, re-checking under the lock (mirrors
    # the pattern in _run_pending_plans) so a second poll tick can never pick
    # up the same pending message while this one is mid-flight.
    locked = db.query(ChatSession).filter(ChatSession.id == session.id).with_for_update().one()
    now = datetime.now(timezone.utc)
    if locked.claimed_at is not None:
        claimed_at = locked.claimed_at
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=timezone.utc)
        if (now - claimed_at).total_seconds() < CHAT_CLAIM_TTL_SECONDS:
            db.commit()  # release the row lock; someone else already has this
            return 0
    locked.claimed_at = now
    locked.claimed_by = WORKER_ID
    db.add(locked)
    db.commit()
    session = locked

    try:
        blocking_budget, warned_budgets = _budget_gate(db, org_id, None)
        for wb in warned_budgets:
            publish(
                org_id, EventType.BUDGET_THRESHOLD_REACHED, "chat", str(session.id),
                payload={
                    "scope": wb.scope, "scope_ref": wb.scope_ref,
                    "spent_amount": float(wb.spent_amount), "monthly_max": float(wb.monthly_max),
                    "warn_percent": float(wb.warn_percent),
                },
            )
        db.commit()
        if blocking_budget is not None:
            return 0

        contract = _chat_contract(message.body, session.id)
        handle, wait = adapter.start_run(
            contract,
            run_id=f"chat-{session.id}-{message.id}",
            on_event=lambda ev: None,  # no_tools=True: nothing tool-shaped to forward
            is_cancelled=lambda: False,  # chat has no Cancellation row to poll
            resume_session_id=session.cli_session_id,
        )
        result = wait()

        session.cli_session_id = result.cli_session_id or session.cli_session_id
        # Chat has no Run row to key cost idempotency on - the message id is
        # just as unique per turn, and serves the same purpose here.
        _commit_run_cost(db, org_id, None, message.id, result.cost_usd)

        if result.exit_code == 0 and not result.timed_out and not result.cancelled:
            reply_body = result.result_summary or "(the agent produced no textual reply)"
        else:
            reply_body = f"(the agent failed to reply: exit={result.exit_code}, timed_out={result.timed_out})"

        db.add(ChatMessage(
            organization_id=org_id, chat_session_id=session.id, role="agent", body=reply_body,
            created_at=datetime.now(timezone.utc),
        ))
        db.add(session)
        db.commit()

        publish(
            org_id, EventType.CHAT_MESSAGE, "chat", str(session.id),
            payload={"role": "agent", "body": reply_body},
            entities=[EntityRef(type="chat_session", id=str(session.id))],
        )
        return 1
    except Exception:  # noqa: BLE001 - never let one bad chat turn crash the poll tick
        logger.exception("Unexpected error executing chat turn for session %s - leaving unanswered for retry", session.id)
        db.rollback()
        return 0
    finally:
        try:
            db.refresh(session)
            session.claimed_at = None
            session.claimed_by = None
            db.add(session)
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()


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
