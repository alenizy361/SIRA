# Build Status — Rabit AI Company OS

Last updated: 2026-07-28 (this build session).

## Environment reality (read this first)

This build was executed inside an ephemeral cloud sandbox, **not** a
persistent VPS. There is no systemd PID 1, no Docker daemon running, no
`/opt/autonomous-company-os` to migrate from (the repo was a blank
single-commit README), and no independently-authenticated `aicompany` OS
user. See `docs/DECISIONS.md` ("Environment reality check before build")
for the full rationale and what the user explicitly approved: build the
complete codebase + real local tests in this sandbox; defer the actual
`scripts/install.sh` run to a session with real VPS/SSH access.

Everything described below as "real" or "tested" was genuinely executed in
this sandbox: Postgres 16 and Redis run as native processes (not Docker,
since no daemon is reachable here — `pg_ctlcluster 16 main start` /
`redis-server --daemonize yes`), and the FastAPI app was actually started
with `uvicorn` and exercised over real HTTP with `curl`.

## What is DONE and verified

- **Monorepo scaffold** matching constitution section 4 exactly.
- **`packages/contracts`**: risk levels (R0-R5, hard-prohibited action list)
  and the realtime event contract (`EventType`, `CoreState`) - single source
  of truth other packages import.
- **`packages/agent-specs`**: 23 YAML files (one per constitution section 9
  agent) validated against `packages/agent-specs/schema.json` via
  `apps/api/app/agents/loader.py`. Loaded and seeded into the
  `agent_definitions` table at API startup - verified via `GET /agents`
  returning all 23 with correct risk ceilings and 8 correctly marked
  `enabled: false` with a `disabled_reason` (seo_geo, marketing_growth,
  analytics, customer_success, customer_support, finance_procurement,
  legal_compliance - no integration configured for any of them).
- **`constitution/`**: 10 governing docs + 23 per-agent contract docs.
- **`packages/permission-engine`**: R0-R5 evaluation, hard-prohibited
  action rejection, budget ceilings, incident-active gating, risk-ceiling
  enforcement. **14/14 unit tests pass** (`tests/unit/test_permission_engine.py`).
- **`services/orchestrator`**: state machine (goal + task, all documented
  transitions including RETRY_WAIT/BLOCKED/FAILED/INCIDENT_OPENED paths),
  exponential-backoff-with-jitter retry, DB-backed leasing via
  `SELECT...FOR UPDATE SKIP LOCKED` (prevents double-assignment, survives
  simulated worker crash/restart), dependency+concurrency-aware scheduler.
  **26 unit tests + 7 integration tests pass against a real local Postgres.**
- **`services/claude-worker`**: real subprocess adapter around the
  locally-authenticated `claude` CLI (v2.1.220, `authMethod: oauth_token` -
  confirmed NOT an API key). Detects CLI capabilities dynamically from
  `--help`/`--version` rather than hardcoding flags. Builds an isolated git
  worktree + branch per task, enforces workspace-root containment, strips
  thinking/redacted_thinking blocks from stream-json before they ever reach
  storage or events, redacts secret-shaped keys from tool-call payloads,
  auto-commits the resulting diff onto the task branch (so a later
  worktree removal never silently discards agent work - this was caught
  and fixed after the first real run exposed it), supports cancellation via
  process-group kill. **9 unit tests pass.**
  **A REAL end-to-end task was executed**: the sample bug in
  `workspace/sample-cv-app` (a funnel CTA label test failing on purpose)
  was diagnosed and fixed by an actual `claude -p` invocation, respecting
  the "only touch `src/funnel/`" constraint, committed to
  `agent/task-e2e-<id>` (verify with
  `git -C workspace/sample-cv-app log --all --oneline`), and independently
  re-verified by re-running pytest against the resulting commit. This is
  the constitution's required "prove one real end-to-end agent invocation"
  evidence (section 26 / Definition of Done).
- **`apps/api`**: FastAPI control plane. 68 tables across identity/company/
  agents/work/governance/memory/integrations/reliability (constitution
  section 5), 2 Alembic migrations applied for real. Argon2 password
  hashing, server-side revocable sessions (not JWT), account lockout after
  5 failed attempts (verified: 6th attempt returns 423, correct password
  still rejected during lockout window), CORS, structured JSON logs with
  secret redaction. Endpoints implemented and verified over real HTTP:
  `/health/{live,ready,dependencies}`, `/auth/{onboard,login,logout,me}`,
  `/agents`, `/goals` (+transition), `/approvals` (+resolve), `/audit-logs`,
  `/plans`, `/tasks`, `/budgets`, `/incidents`, `/integrations` (always
  shows `configured: false` for every provider - no live credentials
  exist), `/memory`, `/system/emergency-stop`, `/ws` (WebSocket with
  sequence numbers, replay-since-last-seq, 20s heartbeat, Redis pub/sub
  fan-out). **50 automated tests pass** (`pytest tests/unit tests/integration`,
  excluding the one costly real-CLI test above), plus manual curl
  verification of the full onboard→login→goal→approval→audit→emergency-stop
  flow and of duplicate-onboarding rejection.
- **`infra/`**: `docker-compose.yml` (validates with `docker compose config`),
  Dockerfiles for api/web, Nginx reverse-proxy template with security
  headers, systemd units for api/web/claude-worker (the latter correctly
  scoped to a non-root `aicompany` user).
- **`scripts/`**: install/upgrade/rollback/backup/restore/doctor/smoke-test/
  uninstall - all pass `bash -n` syntax validation. **Not yet run against a
  real VPS** (no such target exists in this sandbox) - this is the one
  category of "done" that means "written and syntax-valid," not
  "executed end-to-end," and is flagged as such deliberately.

## In progress

- **`apps/web`**: Next.js command center - delegated to a background agent
  with the real, already-running API contract. Check this file's next
  revision (or `git log`) for what landed: which of the 16 required pages
  are fully wired to real endpoints vs. gracefully degraded pending a
  backend endpoint that doesn't exist yet (plans/tasks have list endpoints
  but no create/assign UI wiring yet).

## Known gaps / honest limitations (not yet done)

1. **No live VPS deployment.** `scripts/install.sh` has not been run for
   real anywhere - see "Environment reality" above. This requires a human
   to hand over SSH access to an actual server.
2. **`services/claude-worker`'s poll loop is not wired to publish
   `core.state.changed` / `run.output.delta` WebSocket events yet** - the
   adapter's `on_event` callback exists and strips unsafe content
   correctly, but nothing yet forwards those events into
   `apps/api/app/realtime/bus.py`'s Redis channel. The dashboard's 3D core
   will not yet animate from real backend activity until this wiring is
   added - it is real infrastructure on both ends, just not connected.
3. **No real third-party integrations** (GitHub, Vercel, Sentry, PostHog,
   Google Analytics/Search Console/Ads, email, support platform, payment
   processor) - all correctly show `configured: false`. Wiring any of
   these requires the user to supply real credentials.
4. **Voice experience, budgets/spend enforcement wiring into the
   permission engine at the API-route level, and the CEO/PM auto-planning
   step (goal → plan → tasks) are not yet wired into `apps/api` routes** -
   the underlying pieces (permission-engine, orchestrator scheduler,
   claude-worker) are real and tested in isolation, but a route that
   chains "goal created → CEO agent plans it → tasks created" does not
   exist yet. Today, `POST /goals` only creates the goal row.
5. **`tests/security` and `tests/load` directories are still empty** -
   the permission-engine tests cover policy-level security logic, and the
   auth flow was manually verified (lockout, session revocation, duplicate
   onboarding rejection), but a dedicated prompt-injection test suite and
   load tests have not been written yet.
6. Everything in this sandbox runs as **root** (the container's only user)
   rather than the non-root `aicompany` service account the constitution
   requires. The code (systemd units, claude-worker adapter) is written to
   run correctly as a non-root user in a real deployment; this sandbox
   simply doesn't have that user to demonstrate it live.

## Exact next actions (in priority order)

1. Read the frontend agent's report once it lands, verify `npm run build`
   actually passed, spot-check 2-3 pages manually.
2. Wire `POST /goals` → CEO-agent planning: on goal creation, synchronously
   or via the orchestrator, create a `Plan` + `Task` row so the dashboard
   has something real to show beyond an empty list.
3. Wire `run.output.delta` / `core.state.changed` events from
   `services/claude-worker` into `apps/api/app/realtime/bus.py` so the 3D
   core has real data to react to.
4. Write `tests/security/test_prompt_injection.py` exercising the
   claude-worker's untrusted-context handling against adversarial input.
5. When a real VPS becomes available: run `scripts/doctor.sh` first, then
   `scripts/install.sh`, then re-run `scripts/smoke-test.sh`.
