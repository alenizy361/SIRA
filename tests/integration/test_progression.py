"""Integration tests for the task-progression driver (orchestrator.progression)
against the real local Postgres. See tests/integration/conftest.py.

These lock in the fixes for the "tasks are stranded forever" class of bugs:
a transient failure parked in RETRY_WAIT with nothing to retry it, and a
dependent deadlocked behind a prerequisite that can never complete.
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from orchestrator.progression import advance_ready_pipeline


def _make_task(db, org_id, state="ready", max_retries=3):
    from app.models.work import Task

    task = Task(
        organization_id=org_id,
        title="Improve funnel step 3",
        description="Investigate drop-off",
        assigned_agent_key="frontend_engineer",
        state=state,
        max_retries=max_retries,
        idempotency_key=f"task-{uuid.uuid4()}",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _backdate_updated_at(db, task, seconds):
    ts = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    db.execute(text("UPDATE tasks SET updated_at = :ts WHERE id = :id"), {"ts": ts, "id": str(task.id)})
    db.commit()
    db.refresh(task)


def test_retry_wait_task_returns_to_ready_after_backoff(db, org_id):
    from app.models.work import RetryRecord

    task = _make_task(db, org_id, state="retry_wait")
    _backdate_updated_at(db, task, seconds=600)  # well past any attempt-1 backoff

    changed = advance_ready_pipeline(db, org_id)
    assert changed >= 1

    db.refresh(task)
    assert task.state == "ready"
    assert db.query(RetryRecord).filter(RetryRecord.task_id == task.id).count() == 1


def test_retry_wait_task_stays_put_while_cooling_down(db, org_id):
    task = _make_task(db, org_id, state="retry_wait")  # updated_at ~ now

    advance_ready_pipeline(db, org_id)

    db.refresh(task)
    assert task.state == "retry_wait"  # backoff window has not elapsed yet


def test_retry_wait_task_fails_once_retries_exhausted(db, org_id):
    from app.models.work import RetryRecord

    task = _make_task(db, org_id, state="retry_wait", max_retries=2)
    now = datetime.now(timezone.utc)
    for i in range(1, 3):  # 2 prior attempts == max_retries
        db.add(RetryRecord(task_id=task.id, attempt_number=i, backoff_seconds=2, reason="x", created_at=now))
    db.commit()
    _backdate_updated_at(db, task, seconds=600)

    advance_ready_pipeline(db, org_id)

    db.refresh(task)
    assert task.state == "failed"


def test_dependent_is_cancelled_when_prerequisite_is_dead(db, org_id):
    from app.models.work import TaskDependency

    blocker = _make_task(db, org_id, state="failed")
    dependent = _make_task(db, org_id, state="ready")
    db.add(TaskDependency(task_id=dependent.id, depends_on_task_id=blocker.id))
    db.commit()

    changed = advance_ready_pipeline(db, org_id)
    assert changed >= 1

    db.refresh(dependent)
    assert dependent.state == "cancelled"


def test_dependent_stays_ready_while_prerequisite_still_running(db, org_id):
    from app.models.work import TaskDependency

    blocker = _make_task(db, org_id, state="running")
    dependent = _make_task(db, org_id, state="ready")
    db.add(TaskDependency(task_id=dependent.id, depends_on_task_id=blocker.id))
    db.commit()

    advance_ready_pipeline(db, org_id)

    db.refresh(dependent)
    assert dependent.state == "ready"  # prerequisite may still complete
