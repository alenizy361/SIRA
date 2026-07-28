import redis
from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine

router = APIRouter(tags=["health"])


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
