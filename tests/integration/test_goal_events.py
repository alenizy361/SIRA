"""Verifies POST /goals publishes a real goal.created event over Redis
pub/sub - the API-side half of "wire core.state.changed/goal events into
the WebSocket bus" (worker.py's half is covered by test_worker_events.py)."""
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = "postgresql+psycopg://rabit:rabit_dev_local_only@localhost:5432/rabit_os_test"
for p in [REPO_ROOT / "apps" / "api", REPO_ROOT / "packages", REPO_ROOT / "packages" / "permission-engine"]:
    sys.path.insert(0, str(p))

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.realtime.bus import EventBus  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from sqlalchemy import text  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def _clean():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    for t in ["audit_logs", "goals", "sessions", "user_roles", "users", "organizations"]:
        db.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
    db.commit()
    db.close()
    yield


def test_goal_created_publishes_real_event(client):
    onboard = client.post(
        "/auth/onboard",
        json={
            "organization_name": "Event Test Co",
            "admin_email": "events@rabit.sa",
            "admin_password": "correct-horse-battery-staple",
            "admin_display_name": "Events Admin",
        },
    )
    assert onboard.status_code == 201
    org_id = onboard.json()["organization_id"]

    bus = EventBus(get_settings().redis_url)
    pubsub = bus.pubsub(org_id)
    pubsub.get_message(timeout=0.1)

    resp = client.post("/goals", json={"title": "Test goal", "description": "Verify events fire"})
    assert resp.status_code == 201
    goal_id = resp.json()["id"]

    received = []
    for _ in range(20):
        msg = pubsub.get_message(timeout=0.2)
        if msg and msg.get("type") == "message":
            received.append(json.loads(msg["data"]))
        if received:
            break
    pubsub.close()

    assert len(received) == 1
    event = received[0]
    assert event["type"] == "goal.created"
    assert event["organization_id"] == org_id
    assert event["correlation_id"] == goal_id
    assert event["payload"]["title"] == "Test goal"
    assert event["entities"] == [{"type": "goal", "id": goal_id}]
