"""Read-only listing endpoints for plans/tasks - real queries against real
tables, not mocks. Write paths (create/transition) for these will come with
the full orchestrator wiring into the API in a later iteration - see
docs/BUILD_STATUS.md."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.identity import User
from app.models.work import Plan, Task

router = APIRouter(tags=["work"])


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
