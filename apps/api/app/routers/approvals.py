import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.governance import Approval, AuditLog
from app.models.identity import User

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ResolveApprovalRequest(BaseModel):
    decision: str  # approve | approve_with_conditions | reject | request_revision
    conditions: dict = {}
    comment: str | None = None


@router.get("")
def list_approvals(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    approvals = (
        db.query(Approval)
        .filter(Approval.organization_id == user.organization_id, Approval.state == "pending")
        .order_by(Approval.created_at.asc())
        .all()
    )
    return [_approval_out(a) for a in approvals]


@router.post("/{approval_id}/resolve")
def resolve_approval(
    approval_id: uuid.UUID,
    payload: ResolveApprovalRequest,
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    approval = db.get(Approval, approval_id)
    if not approval or approval.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    if approval.state != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Approval already {approval.state}")

    valid_decisions = {"approve", "approve_with_conditions", "reject", "request_revision"}
    if payload.decision not in valid_decisions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"decision must be one of {valid_decisions}")

    approval.state = "approved" if payload.decision.startswith("approve") else payload.decision
    approval.resolved_by = user.id
    approval.resolved_at = datetime.now(timezone.utc)
    if payload.conditions:
        approval.conditions = payload.conditions
    db.add(approval)

    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_type="user",
            actor_id=str(user.id),
            action=f"approval.{payload.decision}",
            entity_type="approval",
            entity_id=str(approval.id),
            result="executed",
            explanation=payload.comment or "",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    db.refresh(approval)
    return _approval_out(approval)


def _approval_out(a: Approval) -> dict:
    return {
        "id": str(a.id),
        "requested_by_agent_key": a.requested_by_agent_key,
        "action": a.action,
        "risk_level": a.risk_level,
        "reason": a.reason,
        "expected_benefit": a.expected_benefit,
        "cost_sar": float(a.cost_sar) if a.cost_sar is not None else None,
        "reversible": a.reversible,
        "evidence": a.evidence,
        "state": a.state,
        "deadline_at": a.deadline_at.isoformat() if a.deadline_at else None,
        "created_at": a.created_at.isoformat(),
    }
