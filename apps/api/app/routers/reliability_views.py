from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.governance import AuditLog
from app.models.identity import User
from app.models.integrations import Budget, Integration
from app.models.memory import MemoryItem
from app.models.reliability import Incident

router = APIRouter(tags=["reliability"])

# The optional third-party integrations this build ships CONTRACTS for but
# has no live credentials for (constitution section 11: "a disconnected
# integration must never be represented as active"). Real connection wiring
# is future work - see docs/BUILD_STATUS.md.
KNOWN_INTEGRATION_PROVIDERS = [
    "github", "vercel", "sentry", "posthog", "google_analytics",
    "google_search_console", "google_ads", "email", "support_platform",
    "payment_processor",
]


@router.get("/incidents")
def list_incidents(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    rows = db.query(Incident).filter(Incident.organization_id == user.organization_id).order_by(Incident.created_at.desc()).all()
    return [
        {
            "id": str(i.id),
            "title": i.title,
            "severity": i.severity,
            "state": i.state,
            "opened_by": i.opened_by,
            "created_at": i.created_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        }
        for i in rows
    ]


@router.get("/budgets")
def list_budgets(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    rows = db.query(Budget).filter(Budget.organization_id == user.organization_id).all()
    return [
        {
            "id": str(b.id),
            "scope": b.scope,
            "scope_ref": b.scope_ref,
            "currency": b.currency,
            "daily_max": float(b.daily_max),
            "monthly_max": float(b.monthly_max),
            "reserved_amount": float(b.reserved_amount),
            "spent_amount": float(b.spent_amount),
        }
        for b in rows
    ]


@router.get("/memory")
def list_memory(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    rows = (
        db.query(MemoryItem)
        .filter(MemoryItem.organization_id == user.organization_id)
        .order_by(MemoryItem.created_at.desc())
        .limit(200)
        .all()
    )
    return [
        {
            "id": str(m.id),
            "layer": m.layer,
            "title": m.title,
            "summary": m.summary,
            "confidence": float(m.confidence),
            "sensitivity": m.sensitivity,
            "freshness_date": m.freshness_date.isoformat(),
        }
        for m in rows
    ]


@router.get("/integrations")
def list_integrations(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """Always reflects real DB state; providers with no Integration row yet
    are synthesized as explicitly "not configured" so the UI never shows a
    provider that doesn't exist as if it were silently connected."""
    rows = {i.provider: i for i in db.query(Integration).filter(Integration.organization_id == user.organization_id).all()}
    out = []
    for provider in KNOWN_INTEGRATION_PROVIDERS:
        row = rows.get(provider)
        out.append(
            {
                "provider": provider,
                "configured": bool(row.configured) if row else False,
                "last_sync_at": row.last_sync_at.isoformat() if row and row.last_sync_at else None,
                "last_error": row.last_error if row else None,
            }
        )
    return out


@router.post("/system/emergency-stop")
def emergency_stop(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """Constitution section 22: stop new task assignment, cancel
    safe-to-cancel runs, revoke worker leases, pause external mutation
    tools, preserve evidence. This endpoint flips the organization's
    autonomy_mode to observe_only and revokes all outstanding run leases -
    it does not (yet) forcibly kill in-flight subprocess runs; that requires
    the claude-worker process to poll for this flag, which is a documented
    next step in docs/BUILD_STATUS.md, not something this endpoint fakes."""
    from app.models.identity import Organization
    from app.models.work import RunLease, Task

    org = db.get(Organization, user.organization_id)
    org.autonomy_mode = "observe_only"
    db.add(org)

    leases = (
        db.query(RunLease)
        .join(Task, Task.id == RunLease.task_id)
        .filter(Task.organization_id == user.organization_id)
        .all()
    )
    revoked = len(leases)
    for lease in leases:
        db.delete(lease)

    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_type="user",
            actor_id=str(user.id),
            action="system.emergency_stop",
            result="executed",
            explanation=f"Autonomy mode set to observe_only; {revoked} run lease(s) revoked.",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return {"status": "stopped", "autonomy_mode": "observe_only", "leases_revoked": revoked}
