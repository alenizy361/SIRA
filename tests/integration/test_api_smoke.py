"""API-level integration tests using FastAPI's TestClient against a REAL,
dedicated Postgres database (rabit_os_test - separate from the interactive
rabit_os dev database so onboarding's "only once" rule doesn't collide with
manual testing). No mocks: every request hits real routers, real SQLAlchemy
models, real Postgres."""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ["DATABASE_URL"] = "postgresql+psycopg://rabit:rabit_dev_local_only@localhost:5432/rabit_os_test"

for p in [
    REPO_ROOT / "apps" / "api",
    REPO_ROOT / "packages",
    REPO_ROOT / "packages" / "permission-engine",
    REPO_ROOT / "services",
]:
    sys.path.insert(0, str(p))

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True, scope="module")
def _clean_test_db():
    """Truncate everything before this module's tests run, so re-runs don't
    hit the onboarding-already-completed 403."""
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    tables = [
        "audit_logs", "approvals", "run_leases", "runs", "tasks", "plans",
        "goals", "sessions", "user_roles", "users", "agent_instances", "organizations",
    ]
    for t in tables:
        db.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
    db.commit()
    db.close()
    yield


def test_health_endpoints_real(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready").json()
    assert ready["checks"]["postgres"]["status"] == "ok"
    assert ready["checks"]["redis"]["status"] == "ok"


def test_worker_health_reflects_heartbeat(client):
    """The dashboard's "is the company running?" banner is driven entirely by
    this endpoint, so it must faithfully report: down (no heartbeat), asleep
    (heartbeat present but not authed), running (heartbeat + authed)."""
    import json

    import redis

    from app.config import get_settings
    from app.routers.health import WORKER_HEARTBEAT_KEY

    r = redis.from_url(get_settings().redis_url)

    # No heartbeat -> worker down, with a restart hint.
    r.delete(WORKER_HEARTBEAT_KEY)
    down = client.get("/health/worker").json()
    assert down["alive"] is False and down["authed"] is False
    assert "hint" in down

    # Heartbeat present but not authenticated -> asleep, with a login hint.
    from datetime import datetime, timezone

    r.set(
        WORKER_HEARTBEAT_KEY,
        json.dumps({"worker_id": "test", "authed": False, "ts": datetime.now(timezone.utc).isoformat()}),
        ex=60,
    )
    asleep = client.get("/health/worker").json()
    assert asleep["alive"] is True and asleep["authed"] is False
    assert "claude auth login" in asleep["hint"]

    # Heartbeat present and authenticated -> running, no hint needed.
    r.set(
        WORKER_HEARTBEAT_KEY,
        json.dumps({"worker_id": "test", "authed": True, "ts": datetime.now(timezone.utc).isoformat()}),
        ex=60,
    )
    running = client.get("/health/worker").json()
    assert running["alive"] is True and running["authed"] is True
    assert running.get("seconds_since_heartbeat") is not None
    r.delete(WORKER_HEARTBEAT_KEY)


def test_agents_seeded_at_startup(client):
    resp = client.get("/agents")
    # Unauthenticated - should be 401 since /agents requires a session.
    assert resp.status_code == 401


def test_full_onboarding_login_goal_approval_flow(client):
    onboard = client.post(
        "/auth/onboard",
        json={
            "organization_name": "Smoke Test Co",
            "admin_email": "smoke@rabit.sa",
            "admin_password": "correct-horse-battery-staple",
            "admin_display_name": "Smoke Admin",
        },
    )
    assert onboard.status_code == 201, onboard.text

    second = client.post(
        "/auth/onboard",
        json={
            "organization_name": "Second Co",
            "admin_email": "second@rabit.sa",
            "admin_password": "correct-horse-battery-staple",
            "admin_display_name": "Second Admin",
        },
    )
    assert second.status_code == 403

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "smoke@rabit.sa"

    agents = client.get("/agents").json()
    assert len(agents) == 23
    ceo = next(a for a in agents if a["agent_key"] == "ceo")
    assert ceo["enabled"] is True
    marketing = next(a for a in agents if a["agent_key"] == "marketing_growth")
    assert marketing["enabled"] is False
    assert marketing["disabled_reason"]

    goal = client.post(
        "/goals",
        json={"title": "Improve funnel", "description": "Fix the biggest drop-off step."},
    )
    assert goal.status_code == 201
    assert goal.json()["state"] == "goal_captured"

    goals = client.get("/goals").json()
    assert len(goals) == 1

    audit = client.get("/audit-logs").json()
    assert any(a["action"] == "goal.create" for a in audit)

    for path in ["/plans", "/tasks", "/budgets", "/incidents", "/integrations", "/memory"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text}"

    integrations = client.get("/integrations").json()
    assert all(i["configured"] is False for i in integrations), "no integration should show as connected in this build"

    stop = client.post("/system/emergency-stop")
    assert stop.status_code == 200
    assert stop.json()["autonomy_mode"] == "observe_only"

    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


def test_goal_plan_endpoint_returns_ceo_response(client):
    """The dashboard reads /goals/{id}/plan to SHOW the CEO's answer. It must
    report 'no plan yet' honestly before planning, then return the plan summary
    and its tasks once they exist - this is the durable response the operator
    sees instead of a silent nothing."""
    import uuid as _uuid

    from app.db import get_sessionmaker
    from app.models.company import Goal
    from app.models.identity import Organization
    from app.models.work import Plan, Task

    # Org already onboarded by the prior test; log back in.
    login = client.post("/auth/login", json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"})
    assert login.status_code == 200, login.text

    goal = client.post(
        "/goals",
        json={"title": "Ship the landing page", "description": "A fast Arabic landing page."},
    ).json()
    goal_id = goal["id"]

    # No plan yet -> honest empty response, not a 404 or fake data.
    empty = client.get(f"/goals/{goal_id}/plan")
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["plan"] is None
    assert body["tasks"] == []

    # Insert a real Plan + Task the way planning.py would, then read it back.
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    org_id = db.query(Organization.id).scalar()
    plan = Plan(
        organization_id=org_id,
        goal_id=_uuid.UUID(goal_id),
        title="Landing page delivery",
        summary="Build a responsive Arabic-first landing page and verify it on mobile.",
        state="drafted",
    )
    db.add(plan)
    db.flush()
    db.add(
        Task(
            organization_id=org_id,
            plan_id=plan.id,
            title="Build the hero section",
            description="Implement the hero with the primary call to action.",
            assigned_agent_key="frontend_engineer",
            risk_level="R1",
            state="ready",
            idempotency_key=f"{goal_id}-plan-{plan.id}-0",
            acceptance_criteria={"criteria": [], "allowed_tools": ["Read", "Edit"]},
        )
    )
    db.commit()
    db.close()

    resp = client.get(f"/goals/{goal_id}/plan").json()
    assert resp["plan"]["summary"].startswith("Build a responsive Arabic-first")
    assert len(resp["tasks"]) == 1
    task = resp["tasks"][0]
    assert task["agent_key"] == "frontend_engineer"
    assert task["risk_level"] == "R1"
    assert task["title"] == "Build the hero section"


def test_emergency_stop_is_reversible(client):
    """Emergency stop used to be a ONE-WAY DOOR: autonomy_mode was set to
    observe_only and nothing anywhere could set it back, so one press froze the
    company forever while the dashboard still reported a healthy worker and
    every goal you sent was silently ignored by the host worker (which skips
    any org whose mode is not an executing one). Stopping must be reversible,
    and the UI must be able to SEE the stopped state."""
    login = client.post(
        "/auth/login",
        json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200, login.text

    # Establish a known baseline rather than assuming one: an earlier test in
    # this module presses emergency stop and never resumes, which is exactly
    # the real-world trap this endpoint exists to escape.
    client.post("/system/resume")
    before = client.get("/system/autonomy").json()
    assert before["executing"] is True, before

    stop = client.post("/system/emergency-stop")
    assert stop.status_code == 200
    stopped = client.get("/system/autonomy").json()
    assert stopped["autonomy_mode"] == "observe_only"
    # This is the flag the dashboard needs to stop claiming "running".
    assert stopped["executing"] is False

    resumed = client.post("/system/resume")
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["changed"] is True
    assert body["previous"] == "observe_only"

    after = client.get("/system/autonomy").json()
    assert after["executing"] is True
    assert after["autonomy_mode"] == "execute_low_risk"

    # Resuming an already-running company is a no-op, and says so honestly
    # rather than reporting a change that did not happen.
    again = client.post("/system/resume").json()
    assert again["changed"] is False and again["status"] == "already_running"

    audit = client.get("/audit-logs").json()
    assert any(a["action"] == "system.resume" for a in audit)


def test_goal_plan_endpoint_surfaces_each_agents_actual_result(client):
    """The operator wants to read what each agent actually reported after
    running - not just a bare state label. /goals/{id}/plan must surface the
    latest Run.result_summary per task, and all_done must be true only once
    every task has reached a terminal state."""
    import uuid as _uuid

    from app.db import get_sessionmaker
    from app.models.identity import Organization
    from app.models.work import Plan, Run, Task

    login = client.post("/auth/login", json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"})
    assert login.status_code == 200, login.text

    goal = client.post(
        "/goals",
        json={"title": "Fix the checkout bug", "description": "Users can't complete checkout."},
    ).json()
    goal_id = goal["id"]

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    org_id = db.query(Organization.id).scalar()
    plan = Plan(organization_id=org_id, goal_id=_uuid.UUID(goal_id), title="Checkout fix", state="drafted")
    db.add(plan)
    db.flush()
    task = Task(
        organization_id=org_id, plan_id=plan.id, title="Patch the payment handler",
        description="d", assigned_agent_key="backend_engineer", risk_level="R1",
        state="completed", idempotency_key=f"{goal_id}-t0", acceptance_criteria={},
    )
    db.add(task)
    db.flush()
    # An older, superseded run must not win over the latest one.
    db.add(Run(
        organization_id=org_id, task_id=task.id, agent_key="backend_engineer",
        state="failed", result_summary="First attempt failed on a null check.",
    ))
    db.flush()
    db.add(Run(
        organization_id=org_id, task_id=task.id, agent_key="backend_engineer",
        state="completed", result_summary="Fixed the null check in payment_handler.py; added a regression test.",
    ))
    db.commit()
    db.close()

    resp = client.get(f"/goals/{goal_id}/plan").json()
    assert resp["all_done"] is True
    t = resp["tasks"][0]
    assert t["result"] == "Fixed the null check in payment_handler.py; added a regression test."


def test_goal_plan_endpoint_surfaces_the_transparent_work_log(client):
    """A ToolCall table existed with nothing ever writing to it - tool-call
    data only ever lived on the transient WS stream. /goals/{id}/plan must
    surface each task's matched tool_call/tool_result pairs as a durable,
    ordered work_log - the step-by-step transparency real agent UIs
    (LangSmith traces, Devin's work log) treat as core, not optional."""
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from app.db import get_sessionmaker
    from app.models.identity import Organization
    from app.models.work import Plan, Run, Task, ToolCall

    login = client.post("/auth/login", json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"})
    assert login.status_code == 200, login.text

    goal = client.post(
        "/goals",
        json={"title": "Refactor the parser", "description": "d"},
    ).json()
    goal_id = goal["id"]

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    org_id = db.query(Organization.id).scalar()
    plan = Plan(organization_id=org_id, goal_id=_uuid.UUID(goal_id), title="Parser refactor", state="drafted")
    db.add(plan)
    db.flush()
    task = Task(
        organization_id=org_id, plan_id=plan.id, title="Refactor tokenizer",
        description="d", assigned_agent_key="backend_engineer", risk_level="R1",
        state="completed", idempotency_key=f"{goal_id}-wl", acceptance_criteria={},
    )
    db.add(task)
    db.flush()
    run = Run(organization_id=org_id, task_id=task.id, agent_key="backend_engineer", result_summary="Refactored.")
    db.add(run)
    db.flush()
    t0 = datetime.now(timezone.utc)
    db.add(ToolCall(
        run_id=run.id, tool_name="Read", input_summary={"file_path": "tokenizer.py"},
        output_summary={"preview": "class Tokenizer: ..."}, succeeded=True,
        started_at=t0, finished_at=t0 + timedelta(seconds=1),
    ))
    db.add(ToolCall(
        run_id=run.id, tool_name="Edit", input_summary={"file_path": "tokenizer.py"},
        output_summary={"preview": "applied"}, succeeded=True,
        started_at=t0 + timedelta(seconds=2), finished_at=t0 + timedelta(seconds=3),
    ))
    db.commit()
    db.close()

    resp = client.get(f"/goals/{goal_id}/plan").json()
    log = resp["tasks"][0]["work_log"]
    assert len(log) == 2
    assert log[0]["tool_name"] == "Read"  # chronological order preserved
    assert log[1]["tool_name"] == "Edit"
    assert log[0]["output_preview"] == "class Tokenizer: ..."
    assert log[0]["succeeded"] is True


def _make_task_for_cancel(db, org_id, state, goal_id=None, plan_id=None):
    import uuid as _uuid

    from app.models.work import Task

    task = Task(
        organization_id=org_id, plan_id=plan_id, title="Cancel-test task",
        description="d", assigned_agent_key="backend_engineer", risk_level="R1",
        state=state, idempotency_key=f"cancel-test-{_uuid.uuid4()}", acceptance_criteria={},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_cancel_endpoint_immediately_cancels_a_task_that_is_not_yet_running(client):
    """READY/ASSIGNED/etc tasks have no in-flight subprocess for a worker to
    interrupt, so cancel must take effect synchronously here rather than
    leaving a Cancellation row an idle task will never itself check."""
    from app.db import get_sessionmaker
    from app.models.identity import Organization
    from app.models.work import Cancellation

    login = client.post("/auth/login", json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"})
    assert login.status_code == 200, login.text

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    org_id = db.query(Organization.id).scalar()
    task = _make_task_for_cancel(db, org_id, state="ready")
    task_id = str(task.id)
    db.close()

    resp = client.post(f"/tasks/{task_id}/cancel", json={"reason": "no longer needed"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "cancelled"
    assert body["cancellation_requested"] is True

    db = SessionLocal()
    from app.models.work import Task as TaskModel
    refreshed = db.query(TaskModel).filter(TaskModel.id == task.id).one()
    assert refreshed.state == "cancelled"
    cancellation = db.query(Cancellation).filter(Cancellation.task_id == task.id).one()
    assert cancellation.reason == "no longer needed"
    db.close()


def test_cancel_endpoint_records_a_cancellation_without_flipping_a_running_tasks_state_early(client):
    """A RUNNING task has a real subprocess in flight - only the worker that
    owns it can safely stop it (see claude_worker.worker._make_is_cancelled).
    The API must record the request and leave task.state alone, never race
    the worker's own transition."""
    from app.db import get_sessionmaker
    from app.models.identity import Organization
    from app.models.work import Cancellation, Run

    login = client.post("/auth/login", json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"})
    assert login.status_code == 200, login.text

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    org_id = db.query(Organization.id).scalar()
    task = _make_task_for_cancel(db, org_id, state="running")
    run = Run(organization_id=org_id, task_id=task.id, agent_key="backend_engineer", state="running")
    db.add(run)
    db.commit()
    task_id, run_id = str(task.id), str(run.id)
    db.close()

    resp = client.post(f"/tasks/{task_id}/cancel", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "running"  # unchanged - the worker drives this, not the API
    assert body["cancellation_requested"] is True

    db = SessionLocal()
    cancellation = db.query(Cancellation).filter(Cancellation.task_id == task.id).one()
    assert str(cancellation.run_id) == run_id
    db.close()


def test_cancel_endpoint_rejects_an_already_terminal_task(client):
    from app.db import get_sessionmaker
    from app.models.identity import Organization

    login = client.post("/auth/login", json={"email": "smoke@rabit.sa", "password": "correct-horse-battery-staple"})
    assert login.status_code == 200, login.text

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    org_id = db.query(Organization.id).scalar()
    task = _make_task_for_cancel(db, org_id, state="completed")
    task_id = str(task.id)
    db.close()

    resp = client.post(f"/tasks/{task_id}/cancel", json={})
    assert resp.status_code == 409, resp.text
