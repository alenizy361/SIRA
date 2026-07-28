"""Task-progression driver (constitution section 6 retry/backoff).

Moves tasks that no execution step would otherwise advance, so a transient
failure or a dead prerequisite never strands a task forever:

  * RETRY_WAIT -> READY once the exponential-backoff window elapses and the
    task is still under max_retries; -> FAILED once retries are exhausted.
  * any non-terminal task whose prerequisite ended in a dead state
    (failed / cancelled / incident) -> CANCELLED, so a dead branch does not
    deadlock its dependents.

Called once per organization on each worker poll tick (see
claude_worker.worker.run_once). Kept deliberately separate from
scheduler.pick_ready_tasks, which is read-only.
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.models.work import RetryRecord, Run, RunLease, Task, TaskDependency  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from .retry import compute_backoff_seconds  # noqa: E402
from .state_machine import InvalidTransition, TaskState, transition_task  # noqa: E402

# Prerequisite states from which a task can never reach COMPLETED, so a
# dependent waiting on it must be cancelled rather than left READY forever.
_DEAD_DEPENDENCY_STATES = {
    TaskState.FAILED.value,
    TaskState.CANCELLED.value,
    TaskState.INCIDENT_OPENED.value,
}

# Task states that are not terminal and can still be cancelled if a
# prerequisite dies underneath them.
_NON_TERMINAL_STATES = {
    TaskState.READY.value,
    TaskState.RETRY_WAIT.value,
    TaskState.BLOCKED.value,
    TaskState.HUMAN_INPUT_REQUIRED.value,
}


def _retry_attempts(db: Session, task_id: uuid.UUID) -> int:
    return db.query(RetryRecord).filter(RetryRecord.task_id == task_id).count()


def _entered_at(task: Task, now: datetime) -> datetime:
    entered = task.updated_at or task.created_at or now
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=timezone.utc)
    return entered


def advance_ready_pipeline(db: Session, organization_id: uuid.UUID) -> int:
    """Advances stranded tasks for one organization. Returns the number of
    tasks whose state changed."""
    now = datetime.now(timezone.utc)
    changed = 0

    # 0. Reclaim ORPHANED tasks: a worker that died/was-restarted mid-run
    #    (systemctl restart on deploy, OOM, host reboot) leaves its task
    #    committed as ASSIGNED/RUNNING while its lease is deleted by
    #    expire_stale_leases - and nothing else ever moves it. Requeue any
    #    ASSIGNED/RUNNING task that has no LIVE lease so the retry path below
    #    picks it up. (A task being actively worked still holds an unexpired
    #    lease, so this never touches a healthy run.)
    live_lease_task_ids = {
        row[0] for row in db.query(RunLease.task_id).filter(RunLease.expires_at > now).all()
    }
    orphaned = (
        db.query(Task)
        .filter(
            Task.organization_id == organization_id,
            Task.state.in_([TaskState.ASSIGNED.value, TaskState.RUNNING.value]),
        )
        .all()
    )
    for task in orphaned:
        if task.id in live_lease_task_ids:
            continue
        try:
            task.state = transition_task(TaskState(task.state), TaskState.RETRY_WAIT).value
        except InvalidTransition:
            continue
        # Mark the abandoned run failed so it isn't left dangling "running".
        for run in db.query(Run).filter(Run.task_id == task.id, Run.state == "running").all():
            run.state = "failed"
            db.add(run)
        db.add(task)
        changed += 1

    # 1. Retry backoff: RETRY_WAIT -> READY (cooled down, retries left) / FAILED.
    #    Lock the rows (skip if another worker holds them) so two ticks never
    #    both write a RetryRecord for the same attempt and inflate the count.
    retry_tasks = (
        db.query(Task)
        .filter(Task.organization_id == organization_id, Task.state == TaskState.RETRY_WAIT.value)
        .with_for_update(skip_locked=True)
        .all()
    )
    for task in retry_tasks:
        attempts = _retry_attempts(db, task.id)
        if attempts >= task.max_retries:
            task.state = transition_task(TaskState(task.state), TaskState.FAILED).value
            db.add(task)
            changed += 1
            continue
        attempt_number = attempts + 1
        backoff = compute_backoff_seconds(attempt_number)
        if now - _entered_at(task, now) < timedelta(seconds=backoff):
            continue  # still cooling down; try again on a later tick
        db.add(
            RetryRecord(
                task_id=task.id,
                attempt_number=attempt_number,
                backoff_seconds=int(backoff),
                reason="run_failed_or_timed_out",
                created_at=now,
            )
        )
        task.state = transition_task(TaskState(task.state), TaskState.READY).value
        db.add(task)
        changed += 1

    # 2. Dead-dependency propagation: cancel dependents of a dead prerequisite.
    dependents = (
        db.query(Task)
        .filter(Task.organization_id == organization_id, Task.state.in_(list(_NON_TERMINAL_STATES)))
        .all()
    )
    for task in dependents:
        deps = db.query(TaskDependency).filter(TaskDependency.task_id == task.id).all()
        if not deps:
            continue
        dep_ids = [d.depends_on_task_id for d in deps]
        dead = (
            db.query(Task)
            .filter(Task.id.in_(dep_ids), Task.state.in_(list(_DEAD_DEPENDENCY_STATES)))
            .count()
        )
        if not dead:
            continue
        try:
            task.state = transition_task(TaskState(task.state), TaskState.CANCELLED).value
        except InvalidTransition:
            continue
        db.add(task)
        changed += 1

    if changed:
        db.commit()
    return changed
