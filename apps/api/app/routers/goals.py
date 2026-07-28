import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

_SERVICES_ROOT = Path(__file__).resolve().parents[4] / "services"
if str(_SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICES_ROOT))

from orchestrator.state_machine import (  # noqa: E402
    GoalState,
    InvalidTransition,
    TaskState,
    transition_goal,
    transition_task,
)

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.company import Goal
from app.models.governance import AuditLog
from app.models.identity import User
from app.realtime.publisher import publish, publish_core_state
from contracts.events import CoreState, EntityRef, EventType

router = APIRouter(prefix="/goals", tags=["goals"])


class CreateGoalRequest(BaseModel):
    title: str
    description: str
    source: str = "user"


class TransitionGoalRequest(BaseModel):
    target_state: str


def _audit(db: DbSession, user: User, action: str, entity_id: str, result: str, explanation: str = ""):
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_type="user",
            actor_id=str(user.id),
            action=action,
            entity_type="goal",
            entity_id=entity_id,
            result=result,
            explanation=explanation,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


@router.post("", status_code=status.HTTP_201_CREATED)
def create_goal(payload: CreateGoalRequest, user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    goal = Goal(
        organization_id=user.organization_id,
        created_by=user.id,
        title=payload.title,
        description=payload.description,
        source=payload.source,
        state=GoalState.GOAL_CAPTURED.value,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    _audit(db, user, "goal.create", str(goal.id), "executed", f"Goal '{payload.title}' captured")
    publish(
        user.organization_id, EventType.GOAL_CREATED, str(user.id), str(goal.id),
        payload={"title": goal.title}, entities=[EntityRef(type="goal", id=str(goal.id))],
    )
    return _goal_out(goal)


@router.get("")
def list_goals(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    goals = db.query(Goal).filter(Goal.organization_id == user.organization_id).order_by(Goal.created_at.desc()).all()
    return [_goal_out(g) for g in goals]


@router.get("/{goal_id}")
def get_goal(goal_id: uuid.UUID, user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    goal = db.get(Goal, goal_id)
    if not goal or goal.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    return _goal_out(goal)


@router.post("/{goal_id}/transition")
def transition(
    goal_id: uuid.UUID,
    payload: TransitionGoalRequest,
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    goal = db.get(Goal, goal_id)
    if not goal or goal.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    try:
        target = GoalState(payload.target_state)
        new_state = transition_goal(GoalState(goal.state), target)
    except (ValueError, InvalidTransition) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    goal.state = new_state.value
    db.add(goal)

    cancelled_tasks = 0
    if new_state == GoalState.CANCELLED:
        cancelled_tasks = _cancel_goal_tasks(db, goal)

    db.commit()
    explanation = f"-> {new_state.value}"
    if cancelled_tasks:
        explanation += f" ({cancelled_tasks} task(s) cancelled)"
    _audit(db, user, "goal.transition", str(goal.id), "executed", explanation)
    return _goal_out(goal)


def _cancel_goal_tasks(db: DbSession, goal: Goal) -> int:
    """Cancelling a goal must stop its work: transition every non-terminal task
    of the goal's plans to CANCELLED and delete their leases, so the worker
    stops leasing them and no new claude runs are launched (and money spent)
    after the goal is cancelled."""
    from app.models.work import Plan, RunLease, Task

    _TERMINAL = {
        TaskState.COMPLETED.value, TaskState.CANCELLED.value,
        TaskState.FAILED.value, TaskState.INCIDENT_OPENED.value,
    }
    plan_ids = [row[0] for row in db.query(Plan.id).filter(Plan.goal_id == goal.id).all()]
    if not plan_ids:
        return 0
    tasks = db.query(Task).filter(Task.plan_id.in_(plan_ids)).all()
    count = 0
    for task in tasks:
        if task.state in _TERMINAL:
            continue
        try:
            task.state = transition_task(TaskState(task.state), TaskState.CANCELLED).value
        except InvalidTransition:
            # Force the terminal state even from an unusual state - a cancelled
            # goal must never leave a runnable task behind.
            task.state = TaskState.CANCELLED.value
        db.add(task)
        db.query(RunLease).filter(RunLease.task_id == task.id).delete(synchronize_session=False)
        count += 1
    return count


@router.post("/{goal_id}/plan", status_code=status.HTTP_202_ACCEPTED)
def request_plan(goal_id: uuid.UUID, user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """ENQUEUES CEO-agent planning - it does NOT run it here.

    Planning makes a real `claude` CLI call, and the CLI's authenticated
    session lives on the HOST (the aicompany user), not inside this API
    container. So the API only records "planning was requested" on the goal;
    the host-level claude-worker (services/claude-worker/worker.py) picks it
    up on its next poll, runs the CEO agent, and writes the Plan + Task graph
    while streaming live events. This returns 202 immediately - the dashboard
    watches the goal's state and the event stream for the result.
    """
    goal = db.get(Goal, goal_id)
    if not goal or goal.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    if goal.state != GoalState.GOAL_CAPTURED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Goal already in state '{goal.state}'")

    meta = dict(goal.metadata_json or {})
    if meta.get("plan_status") == "requested":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Planning already requested for this goal")
    meta["plan_status"] = "requested"
    meta["plan_requested_by"] = str(user.id)
    goal.metadata_json = meta
    db.add(goal)
    db.commit()

    _audit(db, user, "goal.plan_requested", str(goal.id), "executed", "CEO-agent planning enqueued for the host worker")
    publish(
        user.organization_id, EventType.GOAL_CREATED, "ceo", str(goal.id),
        payload={"title": goal.title, "plan_status": "requested"},
        entities=[EntityRef(type="goal", id=str(goal.id))],
    )
    publish_core_state(user.organization_id, CoreState.PLANNING, "ceo", str(goal.id))
    return {"goal": _goal_out(goal), "plan_status": "requested"}


@router.get("/{goal_id}/plan")
def get_goal_plan(goal_id: uuid.UUID, user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """The durable 'response' to a goal: the CEO's plan (title + summary) and
    the tasks it produced, each with its assigned agent, risk and live state.
    This is what the dashboard reads to SHOW the answer - the WS event stream
    is only the live narration; this endpoint is the record that survives a
    reload and tells the operator plainly what the company decided to do."""
    from app.models.work import Plan, Task

    goal = db.get(Goal, goal_id)
    if not goal or goal.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")

    meta = dict(goal.metadata_json or {})
    plan = (
        db.query(Plan)
        .filter(Plan.goal_id == goal.id)
        .order_by(Plan.created_at.desc())
        .first()
    )
    if not plan:
        # No plan yet - tell the UI whether the CEO is actively working
        # (requested/running) or was never asked, so it shows the right state
        # instead of a bare "nothing here".
        return {"plan": None, "tasks": [], "plan_status": meta.get("plan_status")}

    tasks = (
        db.query(Task)
        .filter(Task.plan_id == plan.id)
        .order_by(Task.created_at.asc())
        .all()
    )
    return {
        "plan": {
            "id": str(plan.id),
            "title": plan.title,
            "summary": plan.summary or "",
            "state": plan.state,
        },
        "tasks": [
            {
                "id": str(t.id),
                "title": t.title,
                "description": t.description,
                "agent_key": t.assigned_agent_key,
                "risk_level": t.risk_level,
                "state": t.state,
            }
            for t in tasks
        ],
        "plan_status": meta.get("plan_status"),
    }


def _goal_out(goal: Goal) -> dict:
    return {
        "id": str(goal.id),
        "title": goal.title,
        "description": goal.description,
        "state": goal.state,
        "source": goal.source,
        "priority": goal.priority,
        "created_at": goal.created_at.isoformat(),
    }
