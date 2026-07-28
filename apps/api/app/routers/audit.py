from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.governance import AuditLog
from app.models.identity import User

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    # `limit` is bounded to [1, 500] by Query() so a crafted ?limit=-1 can no
    # longer reach Postgres as a negative LIMIT (which 500s).
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.organization_id == user.organization_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "result": r.result,
            "explanation": r.explanation,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
