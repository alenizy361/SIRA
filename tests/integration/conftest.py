"""Integration tests hit the REAL local Postgres/Redis started in this
sandbox (see docs/DECISIONS.md - no Docker daemon available here, so
Postgres 16 / Redis run as native processes: `pg_ctlcluster 16 main start`,
`redis-server --daemonize yes`). These are NOT mocks.
"""
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in [
    REPO_ROOT / "packages" / "contracts",
    REPO_ROOT / "packages" / "permission-engine",
    REPO_ROOT / "apps" / "api",
    REPO_ROOT / "services",
    REPO_ROOT / "services" / "claude-worker",
    REPO_ROOT / "packages",
]:
    sys.path.insert(0, str(p))

TEST_DATABASE_URL = "postgresql+psycopg://rabit:rabit_dev_local_only@localhost:5432/rabit_os"


@pytest.fixture(scope="session")
def engine():
    return create_engine(TEST_DATABASE_URL, future=True)


@pytest.fixture()
def db(engine):
    Session = sessionmaker(bind=engine, future=True)
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def org_id(db):
    from app.models.identity import Organization

    org = Organization(name=f"Test Org {uuid.uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)
    yield org.id
    # Delete in FK dependency order - this is sandbox test data cleanup,
    # not a production data-retention path.
    oid = str(org.id)
    db.execute(text("DELETE FROM run_leases WHERE task_id IN (SELECT id FROM tasks WHERE organization_id = :id)"), {"id": oid})
    db.execute(text("DELETE FROM runs WHERE organization_id = :id"), {"id": oid})
    db.execute(text("DELETE FROM task_dependencies WHERE task_id IN (SELECT id FROM tasks WHERE organization_id = :id)"), {"id": oid})
    db.execute(text("DELETE FROM tasks WHERE organization_id = :id"), {"id": oid})
    db.execute(text("DELETE FROM plans WHERE organization_id = :id"), {"id": oid})
    db.execute(text("DELETE FROM goals WHERE organization_id = :id"), {"id": oid})
    db.execute(text("DELETE FROM organizations WHERE id = :id"), {"id": oid})
    db.commit()
