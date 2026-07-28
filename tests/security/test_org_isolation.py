"""Constitution: "Every consequential action must be attributable to a
user... " and organization scoping throughout the data model (section 5).
This build's onboarding endpoint is deliberately single-tenant-bootstrap
(POST /auth/onboard 403s once any org exists), so to test real
cross-organization isolation we insert a second organization directly via
SQL (simulating a second tenant that would exist in a multi-org deployment)
and confirm org A's authenticated session cannot see org B's data through
any endpoint - the isolation must come from the `organization_id` filter in
every query, not from there only being one org in the demo."""
import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = "postgresql+psycopg://rabit:rabit_dev_local_only@localhost:5432/rabit_os_test"
for p in [REPO_ROOT / "apps" / "api", REPO_ROOT / "packages", REPO_ROOT / "packages" / "permission-engine"]:
    sys.path.insert(0, str(p))

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.auth.security import create_session, hash_password  # noqa: E402
from app.db import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models.company import Goal  # noqa: E402
from app.models.identity import Organization, User  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def two_orgs_with_goals():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()

    for suffix in ("a", "b"):
        db.execute(text(f"DELETE FROM goals WHERE title = 'Org {suffix} secret goal'"))
    db.commit()

    org_a = Organization(name=f"Org A {uuid.uuid4().hex[:6]}")
    org_b = Organization(name=f"Org B {uuid.uuid4().hex[:6]}")
    db.add_all([org_a, org_b])
    db.commit()
    db.refresh(org_a)
    db.refresh(org_b)

    user_a = User(
        organization_id=org_a.id,
        email=f"a-{uuid.uuid4().hex[:8]}@rabit.sa",
        password_hash=hash_password("correct-horse-battery-staple"),
        display_name="User A",
    )
    user_b = User(
        organization_id=org_b.id,
        email=f"b-{uuid.uuid4().hex[:8]}@rabit.sa",
        password_hash=hash_password("correct-horse-battery-staple"),
        display_name="User B",
    )
    db.add_all([user_a, user_b])
    db.commit()
    db.refresh(user_a)
    db.refresh(user_b)

    goal_b = Goal(
        organization_id=org_b.id,
        created_by=user_b.id,
        title="Org b secret goal",
        description="Should never be visible to org A.",
        state="goal_captured",
    )
    db.add(goal_b)
    db.commit()
    db.refresh(goal_b)

    cookie_a, _ = create_session(user_a, db, "127.0.0.1", "pytest")
    cookie_b, _ = create_session(user_b, db, "127.0.0.1", "pytest")

    yield {
        "org_a": org_a, "org_b": org_b,
        "user_a": user_a, "user_b": user_b,
        "goal_b": goal_b,
        "cookie_a": cookie_a, "cookie_b": cookie_b,
    }

    db.execute(text("DELETE FROM goals WHERE organization_id IN (:a, :b)"), {"a": str(org_a.id), "b": str(org_b.id)})
    db.execute(text("DELETE FROM chat_messages WHERE organization_id IN (:a, :b)"), {"a": str(org_a.id), "b": str(org_b.id)})
    db.execute(text("DELETE FROM chat_sessions WHERE organization_id IN (:a, :b)"), {"a": str(org_a.id), "b": str(org_b.id)})
    db.execute(text("DELETE FROM sessions WHERE organization_id IN (:a, :b)"), {"a": str(org_a.id), "b": str(org_b.id)})
    db.execute(text("DELETE FROM users WHERE organization_id IN (:a, :b)"), {"a": str(org_a.id), "b": str(org_b.id)})
    db.execute(text("DELETE FROM organizations WHERE id IN (:a, :b)"), {"a": str(org_a.id), "b": str(org_b.id)})
    db.commit()
    db.close()


def _cookie_header(settings, value):
    return {"Cookie": f"{settings.session_cookie_name}={value}"}


def test_org_a_cannot_list_org_bs_goals(client, two_orgs_with_goals):
    settings = get_settings()
    resp = client.get("/goals", headers=_cookie_header(settings, two_orgs_with_goals["cookie_a"]))
    assert resp.status_code == 200
    titles = [g["title"] for g in resp.json()]
    assert "Org b secret goal" not in titles


def test_org_a_cannot_fetch_org_bs_goal_by_id(client, two_orgs_with_goals):
    settings = get_settings()
    goal_b_id = str(two_orgs_with_goals["goal_b"].id)
    resp = client.get(f"/goals/{goal_b_id}", headers=_cookie_header(settings, two_orgs_with_goals["cookie_a"]))
    assert resp.status_code == 404


def test_org_b_can_see_its_own_goal(client, two_orgs_with_goals):
    settings = get_settings()
    goal_b_id = str(two_orgs_with_goals["goal_b"].id)
    resp = client.get(f"/goals/{goal_b_id}", headers=_cookie_header(settings, two_orgs_with_goals["cookie_b"]))
    assert resp.status_code == 200
    assert resp.json()["title"] == "Org b secret goal"


def test_org_a_cannot_transition_org_bs_goal(client, two_orgs_with_goals):
    settings = get_settings()
    goal_b_id = str(two_orgs_with_goals["goal_b"].id)
    resp = client.post(
        f"/goals/{goal_b_id}/transition",
        json={"target_state": "cancelled"},
        headers=_cookie_header(settings, two_orgs_with_goals["cookie_a"]),
    )
    assert resp.status_code == 404


def test_org_a_chat_never_surfaces_org_bs_chat_messages(client, two_orgs_with_goals):
    """The casual-chat surface (apps/api/app/routers/chat_views.py) keys its
    single get-or-create ChatSession on organization_id - confirm org A
    posting/listing never touches org B's session or messages, same as every
    other org-scoped resource."""
    settings = get_settings()

    resp = client.post(
        "/chat/messages", json={"content": "org b secret chat message"},
        headers=_cookie_header(settings, two_orgs_with_goals["cookie_b"]),
    )
    assert resp.status_code == 200, resp.text

    resp = client.get("/chat/messages", headers=_cookie_header(settings, two_orgs_with_goals["cookie_a"]))
    assert resp.status_code == 200
    bodies = [m["body"] for m in resp.json()]
    assert "org b secret chat message" not in bodies

    resp = client.get("/chat/messages", headers=_cookie_header(settings, two_orgs_with_goals["cookie_b"]))
    assert resp.status_code == 200
    bodies = [m["body"] for m in resp.json()]
    assert "org b secret chat message" in bodies
