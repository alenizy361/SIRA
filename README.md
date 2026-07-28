# Rabit AI Company OS

An autonomous-company operating system: a command center where a human
sets goals, a CEO agent turns them into plans, specialized agents execute
tasks under a risk-tiered permission model, and every consequential action
is logged, reviewable, and reversible.

See `constitution/` for the governing rules this system enforces on
itself, and `docs/BUILD_STATUS.md` for an honest account of what is real
and tested today versus still pending.

## Repository layout

```
apps/web/                Next.js command center (dashboard, Arabic-first RTL)
apps/api/                FastAPI control plane (auth, goals, approvals, audit, WebSocket)
services/orchestrator/   State machine, DB-backed leasing, retry/backoff, scheduler
services/claude-worker/  Subprocess adapter around the locally-authenticated Claude Code CLI
packages/contracts/      Shared risk-level and realtime-event definitions
packages/agent-specs/    Machine-readable YAML contract for each of the 23 agents
packages/permission-engine/  R0-R5 risk evaluation
constitution/            Governing documents (mission, permissions, security, agents...)
infra/                    docker-compose, Dockerfiles, Nginx template, systemd units
scripts/                  install/upgrade/rollback/backup/restore/doctor/smoke-test
tests/                    unit, integration, security, load, e2e
workspace/                sample/managed repositories agents operate on - never the OS repo itself
docs/                    BUILD_STATUS, DECISIONS, and (fill in as written) OPERATIONS/API/SECURITY
```

## Local development (no Docker required - what this sandbox actually used)

Prerequisites: Python 3.11+, Node 22+, PostgreSQL 16, Redis, and the
`claude` CLI logged in (`claude auth login` - a Claude Max/Pro
subscription, **no Anthropic API key**).

```bash
# 1. Start Postgres + Redis (adjust to your OS's service manager)
pg_ctlcluster 16 main start   # or: systemctl start postgresql
redis-server --daemonize yes

# 2. Create the database
sudo -u postgres createdb -O <youruser> rabit_os

# 3. Python environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r apps/api/requirements.txt

# 4. Configure environment
cp .env.example .env   # then edit DATABASE_URL/SESSION_SECRET_KEY for real use

# 5. Run migrations
cd apps/api && alembic upgrade head && cd ../..

# 6. Start the API
PYTHONPATH=apps/api:packages:packages/permission-engine:services:services/claude-worker \
  uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000

# 7. Start the web app (separate terminal)
cd apps/web && npm install && npm run dev
```

Then open `http://localhost:3000` - it redirects to `/onboarding` on a
fresh database (no organization exists yet), or `/login` afterward.

## Running tests

```bash
source .venv/bin/activate
pytest tests/unit tests/integration tests/security \
  --deselect tests/integration/test_claude_worker_e2e.py::test_real_task_fixes_failing_test_in_isolated_worktree \
  --deselect tests/integration/test_planning_e2e.py::test_ceo_agent_plans_a_real_goal
```

The two deselected tests make real `claude` CLI calls (genuine Claude Max
usage) to prove the worker and CEO-planning flows end-to-end - they're
excluded from routine runs to avoid spending usage on every test run, but
both pass; run them individually when you want to re-verify that proof.

## Production deployment

`scripts/install.sh` targets a real Ubuntu 22.04/24.04 VPS with Docker: it
provisions a non-root `aicompany` service user (required because the
Claude Code CLI's login/session state is per-OS-user), configures
`docker-compose.yml` (Postgres, Redis, API, web), Nginx, and systemd units,
then runs `scripts/smoke-test.sh`. See `docs/BUILD_STATUS.md` for exactly
what has and hasn't been executed against a real server yet.

## Currency and timezone defaults

SAR (Saudi Riyal) and `Asia/Riyadh`, configurable per organization at
onboarding.
