"""DB-backed leases: guarantee at-most-one active worker per task.

Uses PostgreSQL row locking (`SELECT ... FOR UPDATE SKIP LOCKED`) so two
worker processes racing to claim the same task never both succeed -
constitution section 6: "Database-backed leases so two workers cannot
execute the same task."
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.models.work import RunLease  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


class LeaseNotAcquired(Exception):
    pass


def acquire_lease(
    db: Session,
    task_id: uuid.UUID,
    run_id: uuid.UUID,
    worker_id: str,
    ttl_seconds: int = 300,
) -> RunLease:
    """Raises LeaseNotAcquired if another worker already holds a live lease."""
    now = datetime.now(timezone.utc)

    existing = (
        db.query(RunLease)
        .filter(RunLease.task_id == task_id)
        .with_for_update(skip_locked=True)
        .one_or_none()
    )

    if existing is not None:
        if existing.expires_at > now:
            raise LeaseNotAcquired(
                f"Task {task_id} already leased by worker '{existing.worker_id}' "
                f"until {existing.expires_at.isoformat()}"
            )
        # Expired lease - reclaim it in place.
        existing.run_id = run_id
        existing.worker_id = worker_id
        existing.acquired_at = now
        existing.expires_at = now + timedelta(seconds=ttl_seconds)
        existing.heartbeat_at = now
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    lease = RunLease(
        task_id=task_id,
        run_id=run_id,
        worker_id=worker_id,
        acquired_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        heartbeat_at=now,
    )
    db.add(lease)
    db.commit()
    db.refresh(lease)
    return lease


def renew_heartbeat(db: Session, task_id: uuid.UUID, worker_id: str, ttl_seconds: int = 300) -> RunLease | None:
    now = datetime.now(timezone.utc)
    lease = db.query(RunLease).filter(RunLease.task_id == task_id, RunLease.worker_id == worker_id).one_or_none()
    if lease is None:
        return None
    lease.heartbeat_at = now
    lease.expires_at = now + timedelta(seconds=ttl_seconds)
    db.add(lease)
    db.commit()
    db.refresh(lease)
    return lease


def release_lease(db: Session, task_id: uuid.UUID, worker_id: str) -> None:
    lease = db.query(RunLease).filter(RunLease.task_id == task_id, RunLease.worker_id == worker_id).one_or_none()
    if lease is not None:
        db.delete(lease)
        db.commit()


def expire_stale_leases(db: Session) -> int:
    """Recovery after restart: remove leases whose TTL passed without a
    heartbeat, so their tasks become eligible for re-scheduling instead of
    being stuck forever behind a dead worker."""
    now = datetime.now(timezone.utc)
    stale = db.query(RunLease).filter(RunLease.expires_at <= now).all()
    count = len(stale)
    for lease in stale:
        db.delete(lease)
    db.commit()
    return count
