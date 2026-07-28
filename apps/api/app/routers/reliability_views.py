from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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
            "warn_percent": float(b.warn_percent),
            "warned": b.warned_at is not None,
            "remaining": max(0.0, float(b.monthly_max) - float(b.spent_amount) - float(b.reserved_amount))
            if float(b.monthly_max) > 0 else None,
        }
        for b in rows
    ]


class SetBudgetCapRequest(BaseModel):
    scope: str  # "org" | "agent" - "project" is defined in the schema but has
    # no corresponding first-class entity in Rabit yet, so it isn't exposed here.
    agent_key: str | None = None
    monthly_max_usd: float
    daily_max_usd: float = 0
    warn_percent: float = 80


@router.post("/budgets/cap")
def set_budget_cap(
    body: SetBudgetCapRequest,
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    """Upserts a Budget row that claude_worker.worker._budget_gate enforces
    before every task run - scope="org" caps total org spend, scope="agent"
    (with agent_key) caps that one agent's spend independently, and both can
    be configured at once (mirrors Paperclip's per-scope budget_policies).
    Setting monthly_max_usd to 0 turns enforcement back off for that scope
    (matches Budget's "no row / zero cap means unconfigured" convention)
    rather than needing a separate delete path. Any (re)configuration clears
    warned_at so a raised or reset cap gets a fresh warning cycle instead of
    staying silently past its old warning forever."""
    if body.scope not in ("org", "agent"):
        raise HTTPException(status_code=422, detail="scope must be 'org' or 'agent'")
    if body.scope == "agent" and not body.agent_key:
        raise HTTPException(status_code=422, detail="agent_key is required when scope='agent'")
    if body.monthly_max_usd < 0 or body.daily_max_usd < 0:
        raise HTTPException(status_code=422, detail="Budget caps cannot be negative")
    if not (0 <= body.warn_percent <= 100):
        raise HTTPException(status_code=422, detail="warn_percent must be between 0 and 100")

    scope_ref = body.agent_key if body.scope == "agent" else str(user.organization_id)
    budget = (
        db.query(Budget)
        .filter(Budget.organization_id == user.organization_id, Budget.scope == body.scope, Budget.scope_ref == scope_ref)
        .first()
    )
    if budget is None:
        budget = Budget(organization_id=user.organization_id, scope=body.scope, scope_ref=scope_ref, currency="USD")
    budget.currency = "USD"
    budget.monthly_max = body.monthly_max_usd
    budget.daily_max = body.daily_max_usd
    budget.warn_percent = body.warn_percent
    budget.warned_at = None
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return {
        "id": str(budget.id),
        "scope": budget.scope,
        "scope_ref": budget.scope_ref,
        "monthly_max": float(budget.monthly_max),
        "daily_max": float(budget.daily_max),
        "warn_percent": float(budget.warn_percent),
        "spent_amount": float(budget.spent_amount),
    }


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
            entity_type="organization",
            entity_id=str(user.organization_id),
            result="executed",
            explanation=f"Autonomy mode set to observe_only; {revoked} run lease(s) revoked.",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return {"status": "stopped", "autonomy_mode": "observe_only", "leases_revoked": revoked}


# Modes in which the host worker will actually execute work. Must stay in sync
# with claude_worker.worker.AUTONOMOUS_EXECUTION_MODES - a mode outside this set
# means the worker skips the organization entirely.
RESUMABLE_MODES = ("execute_low_risk", "controlled_autonomous")


@router.get("/system/autonomy")
def get_autonomy(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """Current autonomy mode + whether the company will actually act.

    Without this the dashboard could not tell the difference between "running"
    and "stopped": the worker can be alive and authenticated while the org sits
    in observe_only, in which case every goal you send is silently ignored.
    """
    from app.models.identity import Organization

    org = db.get(Organization, user.organization_id)
    mode = org.autonomy_mode if org else "unknown"
    return {
        "autonomy_mode": mode,
        "executing": mode in RESUMABLE_MODES,
    }


@router.post("/system/resume")
def resume(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    """Undo an emergency stop: return the organization to an executing autonomy
    mode so queued goals are picked up again.

    Emergency stop used to be a ONE-WAY DOOR - autonomy_mode was set to
    observe_only and nothing anywhere could set it back, so a single press (or
    a stray API call) froze the company permanently while the dashboard kept
    reporting a healthy worker. Stopping must always be reversible by the same
    operator who can stop it.
    """
    from app.models.identity import Organization

    org = db.get(Organization, user.organization_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    previous = org.autonomy_mode
    if previous in RESUMABLE_MODES:
        # Already running - report it plainly instead of pretending we changed
        # something, so the UI never claims a resume that was a no-op.
        return {"status": "already_running", "autonomy_mode": previous, "changed": False}

    org.autonomy_mode = "execute_low_risk"
    db.add(org)
    db.add(
        AuditLog(
            organization_id=user.organization_id,
            actor_type="user",
            actor_id=str(user.id),
            action="system.resume",
            entity_type="organization",
            entity_id=str(user.organization_id),
            result="executed",
            explanation=f"Autonomy mode restored: {previous} -> execute_low_risk.",
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return {"status": "resumed", "autonomy_mode": "execute_low_risk", "previous": previous, "changed": True}
