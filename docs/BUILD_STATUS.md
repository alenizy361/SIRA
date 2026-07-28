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

- **CEO-agent auto-planning is now real and wired in**: `POST
  /goals/{id}/plan` (`apps/api/app/services/planning.py`) makes an actual
  `claude` CLI call with `--json-schema` (CEO-agent role prompt), and the
  resulting plan is persisted as a real `Plan` + `PlanStep` + `Task` rows
  and the goal's state machine advances to `plan_drafted`. Verified twice
  over real HTTP against the live goal created earlier - produced a
  genuinely coherent 4-5 task plan correctly assigned across
  ux_research/analytics/product_design/frontend_engineer/qa/ceo with
  R0-R2 risk levels, and again as an automated test
  (`tests/integration/test_planning_e2e.py`, passing). Building this
  surfaced and fixed a real prompt-construction bug: `prompt_builder.py`
  used to unconditionally append prose "end with a decision summary"
  instructions that conflicted with strict `--json-schema` mode; it now
  only appends that instruction when no `output_schema` is set.
- Integration tests now run against a dedicated `rabit_os_test` database
  (not the interactive `rabit_os` dev DB used for manual demos) after a
  test-isolation bug surfaced: `claude_worker.worker.run_once` scans ALL
  organizations, so leftover demo data (the CEO-planning demo's 5 real
  `ready` tasks) made a "no ready tasks" test flaky. **72 automated tests
  now pass** (`pytest tests/unit tests/integration tests/security`,
  excluding the two real-CLI-invoking tests kept manual-run-once to avoid
  spending Claude usage on every CI run).

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
4. **Voice experience and budgets/spend enforcement wiring into the
   permission engine at the API-route level are not yet connected** - the
   underlying pieces (permission-engine, budget model) are real and tested
   in isolation, but no route currently calls `PermissionEngine.evaluate()`
   before executing a mutating action, and no route reserves/commits
   against a `Budget` row. (Goal → plan → tasks IS wired now - see
   `POST /goals/{id}/plan` above - this gap is narrower than it was.)
   `services/claude-worker`'s poll loop (`worker.py`) also does not yet
   call the permission engine before executing a leased task - it trusts
   the task's `risk_level` column as already-classified. Both are real,
   scoped follow-ups, not aspirational.
5. **`tests/security` now has 22 passing tests** (prompt-injection
   structural guarantees, command-injection/shell=True AST check,
   cross-organization data isolation with a second org inserted directly
   via SQL). Writing them **found and fixed a real path-traversal bug**:
   `services/claude-worker/claude_worker/cli_adapter.py`'s
   `_create_worktree` built `f"{repo.name}-{task_id}"` and joined it under
   `.worktrees` - pathlib splits embedded `/`/`..` in that string into real
   path components on join, so a task_id like `"../../../../tmp/pwned"`
   resolved clean outside the workspace root entirely (verified with a
   throwaway `Path` test before fixing it). Fixed by rejecting any
   `task_id`/would-be path component that isn't a plain
   `[A-Za-z0-9._-]+` slug, plus a post-resolve containment check, in
   `_safe_path_component()`. See `tests/security/test_command_injection.py`
   for the regression test. **`tests/load` is still empty** - no load
   tests written yet.
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
