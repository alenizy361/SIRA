"""Integration tests against a real local Postgres for the orchestrator's
leasing and scheduling logic. See tests/integration/conftest.py."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.leasing import LeaseNotAcquired, acquire_lease, expire_stale_leases, release_lease, renew_heartbeat
from orchestrator.scheduler import pick_ready_tasks


def _make_task(db, org_id, agent_key="frontend_engineer", state="ready"):
    from app.models.work import Task

    task = Task(
        organization_id=org_id,
        title="Fix conversion funnel drop-off",
        description="Investigate and fix the largest drop-off step in the CV builder funnel.",
        assigned_agent_key=agent_key,
        state=state,
        idempotency_key=f"task-{uuid.uuid4()}",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _make_run(db, org_id, task):
    from app.models.work import Run

    run = Run(organization_id=org_id, task_id=task.id, agent_key=task.assigned_agent_key)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def test_lease_prevents_double_assignment(db, org_id):
    task = _make_task(db, org_id)
    run1 = _make_run(db, org_id, task)
    run2 = _make_run(db, org_id, task)

    acquire_lease(db, task.id, run1.id, worker_id="worker-A")

    with pytest.raises(LeaseNotAcquired):
        acquire_lease(db, task.id, run2.id, worker_id="worker-B")


def test_lease_reclaimed_after_expiry_simulating_restart_recovery(db, org_id):
    task = _make_task(db, org_id)
    run1 = _make_run(db, org_id, task)
    run2 = _make_run(db, org_id, task)

    lease = acquire_lease(db, task.id, run1.id, worker_id="worker-A", ttl_seconds=1)
    # Simulate the lease having expired (worker crashed without releasing).
    lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(lease)
    db.commit()

    reclaimed = acquire_lease(db, task.id, run2.id, worker_id="worker-B")
    assert reclaimed.worker_id == "worker-B"


def test_release_then_reacquire(db, org_id):
    task = _make_task(db, org_id)
    run1 = _make_run(db, org_id, task)
    run2 = _make_run(db, org_id, task)

    acquire_lease(db, task.id, run1.id, worker_id="worker-A")
    release_lease(db, task.id, worker_id="worker-A")
    lease = acquire_lease(db, task.id, run2.id, worker_id="worker-B")
    assert lease.worker_id == "worker-B"


def test_heartbeat_extends_expiry(db, org_id):
    task = _make_task(db, org_id)
    run = _make_run(db, org_id, task)
    lease = acquire_lease(db, task.id, run.id, worker_id="worker-A", ttl_seconds=60)
    original_expiry = lease.expires_at

    renewed = renew_heartbeat(db, task.id, worker_id="worker-A", ttl_seconds=600)
    assert renewed.expires_at > original_expiry


def test_expire_stale_leases_frees_task_for_rescheduling(db, org_id):
    task = _make_task(db, org_id)
    run = _make_run(db, org_id, task)
    lease = acquire_lease(db, task.id, run.id, worker_id="worker-A", ttl_seconds=1)
    lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.add(lease)
    db.commit()

    removed = expire_stale_leases(db)
    assert removed >= 1

    ready = pick_ready_tasks(db, org_id, agent_concurrency_limits={"frontend_engineer": 5})
    assert task.id in {t.id for t in ready}


def test_scheduler_respects_concurrency_ceiling(db, org_id):
    task_a = _make_task(db, org_id, agent_key="frontend_engineer")
    task_b = _make_task(db, org_id, agent_key="frontend_engineer")
    _make_task(db, org_id, agent_key="frontend_engineer", state="assigned")

    ready = pick_ready_tasks(db, org_id, agent_concurrency_limits={"frontend_engineer": 1})
    # One slot already consumed by the "assigned" task above -> ceiling of 1 means 0 more picked.
    assert ready == []

    ready_with_room = pick_ready_tasks(db, org_id, agent_concurrency_limits={"frontend_engineer": 3})
    picked_ids = {t.id for t in ready_with_room}
    assert task_a.id in picked_ids and task_b.id in picked_ids


def test_scheduler_skips_task_with_unmet_dependency(db, org_id):
    from app.models.work import TaskDependency

    blocker = _make_task(db, org_id, state="ready")
    dependent = _make_task(db, org_id, state="ready")
    db.add(TaskDependency(task_id=dependent.id, depends_on_task_id=blocker.id))
    db.commit()

    ready = pick_ready_tasks(db, org_id, agent_concurrency_limits={"frontend_engineer": 10})
    picked_ids = {t.id for t in ready}
    assert blocker.id in picked_ids
    assert dependent.id not in picked_ids

    blocker.state = "completed"
    db.add(blocker)
    db.commit()

    ready_after_completion = pick_ready_tasks(db, org_id, agent_concurrency_limits={"frontend_engineer": 10})
    assert dependent.id in {t.id for t in ready_after_completion}
