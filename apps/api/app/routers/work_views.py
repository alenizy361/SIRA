"""Read-only listing endpoints for plans/tasks - real queries against real
tables, not mocks, plus the one write path that exists so far: cancelling an
in-flight task (constitution/Managed-Agents-parity: "interrupt a specific
agent" - see cli_adapter.start_run's is_cancelled param and worker.py's
_make_is_cancelled for the worker-side half of this). Further write paths
(create/transition) will come with the full orchestrator wiring into the API
in a later iteration - see docs/BUILD_STATUS.md."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.identity import User
from app.models.work import Cancellation, Plan, Run, Task, TaskMessage
from app.realtime.publisher import publish
from contracts.events import EventType
from orchestrator.state_machine import InvalidTransition, TaskState, transition_task

router = APIRouter(tags=["work"])

_TERMINAL_TASK_STATES = {
    TaskState.COMPLETED.value,
    TaskState.FAILED.value,
    TaskState.CANCELLED.value,
    TaskState.INCIDENT_OPENED.value,
}


@router.get("/plans")
def list_plans(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    plans = db.query(Plan).filter(Plan.organization_id == user.organization_id).order_by(Plan.created_at.desc()).all()
    return [
        {
            "id": str(p.id),
            "goal_id": str(p.goal_id),
            "title": p.title,
            "summary": p.summary,
            "state": p.state,
            "created_at": p.created_at.isoformat(),
        }
        for p in plans
    ]


@router.get("/tasks")
def list_tasks(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    tasks = db.query(Task).filter(Task.organization_id == user.organization_id).order_by(Task.created_at.desc()).all()
    return [
        {
            "id": str(t.id),
            "title": t.title,
            "description": t.description,
            "assigned_agent_key": t.assigned_agent_key,
            "risk_level": t.risk_level,
            "state": t.state,
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]


class CancelTaskRequest(BaseModel):
    reason: str | None = None


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: uuid.UUID,
    body: CancelTaskRequest = CancelTaskRequest(),
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id, Task.organization_id == user.organization_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.state in _TERMINAL_TASK_STATES:
        raise HTTPException(status_code=409, detail=f"Task is already {task.state} - nothing to cancel")

    latest_run = db.query(Run).filter(Run.task_id == task.id).order_by(Run.created_at.desc()).first()
    db.add(Cancellation(
        task_id=task.id,
        run_id=latest_run.id if latest_run else None,
        requested_by=user.id,
        reason=body.reason,
        created_at=datetime.now(timezone.utc),
    ))

    if task.state == TaskState.RUNNING.value:
        # A subprocess is genuinely in flight. Don't flip task.state here -
        # that would race the worker's own transition. The worker polls this
        # Cancellation row (throttled to once/second, see
        # claude_worker.worker._make_is_cancelled), stops the subprocess
        # itself, and drives the task to CANCELLED once it actually exits.
        db.commit()
        return {"id": str(task.id), "state": task.state, "cancellation_requested": True}

    # Nothing is running yet (READY/ASSIGNED/RETRY_WAIT/BLOCKED/
    # HUMAN_INPUT_REQUIRED) - no worker loop is polling for this Cancellation
    # row, so cancel immediately rather than leaving an idle task waiting on
    # a signal nothing will ever check.
    try:
        task.state = transition_task(TaskState(task.state), TaskState.CANCELLED).value
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.add(task)
    db.commit()
    publish(
        task.organization_id, EventType.TASK_CANCELLED, str(user.id), str(task.id),
        payload={"title": task.title},
    )
    return {"id": str(task.id), "state": task.state, "cancellation_requested": True}


def _serialize_message(m: TaskMessage) -> dict:
    return {
        "id": str(m.id),
        "task_id": str(m.task_id),
        "role": m.role,
        "body": m.body,
        "run_id": str(m.run_id) if m.run_id else None,
        "created_at": m.created_at.isoformat(),
    }


@router.get("/tasks/{task_id}/messages")
def list_task_messages(task_id: uuid.UUID, user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id, Task.organization_id == user.organization_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    rows = (
        db.query(TaskMessage)
        .filter(TaskMessage.task_id == task_id, TaskMessage.organization_id == user.organization_id)
        .order_by(TaskMessage.created_at.asc())
        .all()
    )
    return [_serialize_message(m) for m in rows]


class SendTaskMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


@router.post("/tasks/{task_id}/messages")
def send_task_message(
    task_id: uuid.UUID,
    body: SendTaskMessageRequest,
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    """Posts the human side of a real, continued conversation with this
    task's agent. claude_worker.worker's poll loop (_pending_reply_requests/
    _execute_reply) picks this up and resumes the task's actual CLI session
    (Run.cli_session_id) for the reply - not a summary, the same session
    with full context of everything it already did."""
    task = db.query(Task).filter(Task.id == task_id, Task.organization_id == user.organization_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    has_session = (
        db.query(Run.id)
        .filter(Run.task_id == task_id, Run.cli_session_id.isnot(None))
        .first()
    )
    if has_session is None:
        raise HTTPException(
            status_code=409,
            detail="This task has no completed run yet - there is no session to reply into.",
        )

    message = TaskMessage(
        organization_id=user.organization_id, task_id=task_id, role="human",
        body=body.content, created_at=datetime.now(timezone.utc),
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    publish(
        user.organization_id, EventType.TASK_MESSAGE, str(user.id), str(task_id),
        payload={"role": "human", "body": body.content},
    )
    return _serialize_message(message)
