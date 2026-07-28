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

from orchestrator.state_machine import GoalState, InvalidTransition, transition_goal  # noqa: E402

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.company import Goal
from app.models.governance import AuditLog
from app.models.identity import User
from app.realtime.publisher import publish, publish_core_state
from app.services.planning import PlanningError, plan_goal
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
    db.commit()
    _audit(db, user, "goal.transition", str(goal.id), "executed", f"-> {new_state.value}")
    return _goal_out(goal)


@router.post("/{goal_id}/plan")
def create_plan(goal_id: uuid.UUID, user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """Invokes the real CEO-agent planning flow (app.services.planning) -
    this makes an actual Claude Code CLI call, so it is synchronous and can
    take up to ~1-3 minutes. Constitution: no placeholder business logic -
    a planning failure surfaces as a 502, it never falls back to fake data.
    """
    goal = db.get(Goal, goal_id)
    if not goal or goal.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found")
    if goal.state != GoalState.GOAL_CAPTURED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Goal already in state '{goal.state}'")

    publish_core_state(user.organization_id, CoreState.PLANNING, "ceo", str(goal.id))
    try:
        plan = plan_goal(db, goal)
    except PlanningError as exc:
        publish_core_state(user.organization_id, CoreState.WARNING, "ceo", str(goal.id))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    _audit(db, user, "goal.plan", str(goal.id), "executed", f"Plan '{plan.title}' drafted by CEO agent")
    publish(
        user.organization_id, EventType.PLAN_CREATED, "ceo", str(goal.id),
        payload={"title": plan.title}, entities=[EntityRef(type="goal", id=str(goal.id)), EntityRef(type="plan", id=str(plan.id))],
    )
    publish_core_state(user.organization_id, CoreState.IDLE, "ceo", str(goal.id))
    return {
        "goal": _goal_out(goal),
        "plan": {"id": str(plan.id), "title": plan.title, "summary": plan.summary, "state": plan.state},
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
