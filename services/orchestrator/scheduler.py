"""Dependency-aware, concurrency-limited task scheduling.

Deliberately simple (single SELECT + Python-side filtering) rather than a
single giant SQL query, since task volumes on a single-VPS deployment are
small enough that clarity beats a marginal performance gain here.
"""
import sys
import uuid
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from app.models.work import Run, RunLease, Task, TaskDependency  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from .state_machine import TaskState  # noqa: E402


def _dependencies_satisfied(db: Session, task_id: uuid.UUID) -> bool:
    deps = db.query(TaskDependency).filter(TaskDependency.task_id == task_id).all()
    if not deps:
        return True
    dep_task_ids = [d.depends_on_task_id for d in deps]
    completed_count = (
        db.query(Task)
        .filter(Task.id.in_(dep_task_ids), Task.state == TaskState.COMPLETED.value)
        .count()
    )
    return completed_count == len(dep_task_ids)


def _active_count_for_agent(db: Session, agent_key: str) -> int:
    return (
        db.query(Task)
        .filter(
            Task.assigned_agent_key == agent_key,
            Task.state.in_([TaskState.ASSIGNED.value, TaskState.RUNNING.value]),
        )
        .count()
    )


def pick_ready_tasks(
    db: Session,
    organization_id: uuid.UUID,
    agent_concurrency_limits: dict[str, int],
    max_tasks: int = 10,
) -> list[Task]:
    """Returns tasks eligible for immediate assignment: state=ready,
    dependencies satisfied, not already leased, and under the given agent's
    concurrency ceiling. Caller is responsible for actually leasing
    (leasing.acquire_lease) each returned task - this function does not
    mutate state, so it is safe to call repeatedly/read-only."""
    candidates = (
        db.query(Task)
        .filter(Task.organization_id == organization_id, Task.state == TaskState.READY.value)
        .order_by(Task.created_at.asc())
        .all()
    )

    leased_task_ids = {row[0] for row in db.query(RunLease.task_id).all()}
    picked: list[Task] = []
    running_count_by_agent: dict[str, int] = {}

    for task in candidates:
        if len(picked) >= max_tasks:
            break
        if task.id in leased_task_ids:
            continue
        if not task.assigned_agent_key:
            continue
        if not _dependencies_satisfied(db, task.id):
            continue

        ceiling = agent_concurrency_limits.get(task.assigned_agent_key)
        if ceiling is not None:
            current = running_count_by_agent.setdefault(
                task.assigned_agent_key, _active_count_for_agent(db, task.assigned_agent_key)
            )
            if current >= ceiling:
                continue
            running_count_by_agent[task.assigned_agent_key] += 1

        picked.append(task)

    return picked
