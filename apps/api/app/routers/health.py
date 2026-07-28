import json
from datetime import datetime, timezone

import redis
from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine

router = APIRouter(tags=["health"])

# The host claude-worker writes this key every poll tick with a short TTL. If
# it is absent the worker is not running; `authed` says whether its Claude CLI
# session is valid (the difference between "company asleep - nothing will
# happen" and "company running").
WORKER_HEARTBEAT_KEY = "rabit:worker:heartbeat"


@router.get("/health/worker")
def worker_status():
    """Is the company actually running? Reports host-worker liveness + whether
    its Claude CLI session is authenticated, so the dashboard can tell the
    operator plainly instead of silently doing nothing."""
    settings = get_settings()
    try:
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        raw = r.get(WORKER_HEARTBEAT_KEY)
    except Exception as exc:  # noqa: BLE001
        return {"alive": False, "authed": False, "reason": f"redis_unreachable: {exc}"}

    if not raw:
        # No heartbeat within its TTL -> the worker process is down.
        return {
            "alive": False,
            "authed": False,
            "reason": "no_recent_heartbeat",
            "hint": "The host worker is not running. Start it: systemctl restart rabit-claude-worker.service",
        }
    try:
        beat = json.loads(raw)
    except (ValueError, TypeError):
        return {"alive": False, "authed": False, "reason": "bad_heartbeat"}

    authed = bool(beat.get("authed"))
    seconds = None
    try:
        ts = datetime.fromisoformat(beat.get("ts"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        seconds = int((datetime.now(timezone.utc) - ts).total_seconds())
    except (ValueError, TypeError):
        pass
    result = {"alive": True, "authed": authed, "seconds_since_heartbeat": seconds}
    if not authed:
        result["hint"] = "Worker is running but its Claude session is not logged in. Run: sudo -u aicompany -H claude auth login"
    return result


@router.get("/health/live")
def live():
    """Process is up. Never checks dependencies - used for restart loops."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready():
    """Ready to serve traffic: DB + Redis reachable."""
    checks = _dependency_checks()
    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}


@router.get("/health/dependencies")
def dependencies():
    """Detailed per-dependency status for the System Doctor page."""
    return _dependency_checks()


def _dependency_checks() -> dict:
    settings = get_settings()
    checks = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = {"status": "fail", "detail": str(exc)}

    try:
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = {"status": "fail", "detail": str(exc)}

    return checks
