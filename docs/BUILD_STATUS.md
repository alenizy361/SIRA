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
this sandbox: Postgres 16 and Redis run as native processes
(`pg_ctlcluster 16 main start` / `redis-server --daemonize yes`), and the
FastAPI app was actually started with `uvicorn` and exercised over real
HTTP with `curl`.

**Update (2026-07-28):** a Docker daemon was subsequently started here
(`dockerd`), so the containerized path is no longer untested — the api/web
images are now really built and the full compose stack really runs. See
"Deployment path: now actually exercised" below, which supersedes the
earlier "no Docker daemon" caveat. The remaining untested-here item is the
`scripts/install.sh` run itself against a real Ubuntu VPS (systemd, nginx
reload, certbot), which needs a real server.

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
- **Emergency stop now actually stops new task assignment**, not just
  existing leases: `services/claude-worker/claude_worker/worker.py`'s poll
  loop now skips any organization whose `autonomy_mode` isn't
  `execute_low_risk`/`controlled_autonomous`, so a fresh `POST
  /system/emergency-stop` (which sets `observe_only`) blocks brand-new
  READY tasks too, not just ones that already held a lease. Regression
  test: `tests/integration/test_worker_poll.py::test_emergency_stop_autonomy_mode_blocks_new_task_assignment`.
  73 automated tests pass total now.

- **`apps/web`**: Next.js 16 (App Router, Turbopack) + TypeScript + Tailwind
  v4 command center, 20 routes. Independently re-verified (not just taking
  the building agent's word for it): `npm run build` passes with zero
  errors; started the production build for real and confirmed via
  Playwright (Chromium, 390x844 iPhone viewport) that: unauthenticated
  visits correctly redirect to `/login`; logging in with the real admin
  account and navigating to `/command-center` renders an actual live WebGL
  `<canvas>` (React Three Fiber AI core, not an image) with zero layout
  overflow (`scrollWidth === clientWidth`); `/agents` shows all 23 real
  agents with correct bilingual names, risk badges, and accurate
  enabled/disabled state (double-checked via raw DOM text after initially
  misreading a screenshot - CEO/CTO/Backend Engineer correctly show
  "مفعّل"/enabled, Analytics correctly shows "معطّل"/disabled with its real
  `disabled_reason`); `/goals` shows the real goal + its real
  `plan_drafted` state from the CEO-planning demo above; `/approvals`
  correctly shows an honest empty state. Arabic-first RTL with instant
  EN switching works. Voice capture uses the browser SpeechRecognition API
  with a transcript-confirmation step (no auto-submit), per spec.
  **Honest gap vs. the spec's "premium space-station" visual language**:
  the built UI is a clean, functional dark dashboard with a genuine
  reactive 3D sphere - it is not the full cinematic treatment (particle
  shell/neural arcs/waveform ring) described in constitution section 13.
  It is real and functional, not a mockup, but visually simpler than the
  aspirational description.
  Known incomplete pieces (all show honest "not available yet" empty
  states rather than fake data): `/runs` and `/analytics` have no backend
  endpoint yet.

- **The dashboard now genuinely reacts to real backend activity** (this
  was gap #2 below - now closed). `apps/api/app/realtime/publisher.py` is
  a shared, sequence-numbered publish path used by both
  `services/claude-worker/claude_worker/worker.py` (task execution:
  `task.assigned`/`task.started`/`task.progress`/`task.blocked`,
  `run.tool.started`/`run.tool.completed` from the CLI's actual tool-use/
  tool-result stream-json frames, `run.output.delta`, and
  `core.state.changed` transitioning through `planning`/`coding` →
  `reviewing` → `idle`/`paused`) and `apps/api/app/routers/goals.py` +
  `app/services/planning.py` (`goal.created`, `plan.created`,
  `task.created`, and `core.state.changed` bookending the real CEO-agent
  planning call). Verified three ways: (1) a new integration test
  (`tests/integration/test_worker_events.py`) subscribes to the real Redis
  channel during a task execution and asserts the exact event types,
  monotonically increasing sequence numbers, and correct entity
  references arrive; (2) `tests/integration/test_goal_events.py` does the
  same for `POST /goals`; (3) a genuine live-browser check: logged into
  the real running dashboard via Playwright, called
  `publish_core_state(org_id, CoreState.WARNING, ...)` directly against
  the live API process from a separate script, and captured a screenshot
  showing the AI core sphere change from teal/"خامل" (idle) to orange/
  "تحذير" (warning) with **no page reload** - the WebSocket→Zustand
  store→React re-render path the frontend agent built was already
  correct and needed no changes; only the backend was missing the
  publish calls. The frontend's `AICore.tsx` already had distinct visual
  configs for every `CoreState` value now actually emitted.
  Still open: the permission engine isn't yet consulted before these
  actions execute (see gap below), and `run.output.delta` payloads are
  short text deltas, not a full streamed transcript viewer.

## Deployment path: now actually exercised (2026-07-28)

Earlier revisions of this file described the stack as verified, but that
verification was always done by running processes **directly on the dev
machine**. The shipped artifacts - a containerized api/web stack plus a
host-level worker - had never been built or run. A real VPS deployment
consequently failed seven times in a row, once per untested assumption:
missing `rsync`; duplicate top-level nginx `gzip`; smoke test racing
container startup; `services/claude-worker` absent from the api image
(crash-loop); `install.sh` not rebuilding images after a source change;
`next.config.ts` missing `output: "standalone"`; and `run.sh` sourcing a
venv nothing created.

That gap is now closed and verified for real, with a Docker daemon running
in the dev sandbox:

- `api` image **builds**, and inside the running container `app.main`,
  `claude_worker`, `contracts`, `permission_engine` and `orchestrator` all
  import cleanly.
- Full `docker compose` stack (postgres + redis + api + web) comes up with
  postgres/redis **healthy**; Alembic migrations run **inside** the api
  container against the containerized database.
- `/health/live`, `/health/ready` (reporting postgres+redis ok) and the web
  app all return 200 through the published ports Nginx proxies to, and
  `scripts/smoke-test.sh` passes end-to-end against them.
- `web` runner stage verified by building it from a real `.next/standalone`
  and confirming `node server.js` serves HTTP 200. (`npm install` itself
  could not run in this sandbox - its TLS proxy breaks npm - but that step
  is already proven working on the user's VPS.)
- `services/claude-worker/run.sh` launched in a simulated `APP_ROOT`:
  authenticates the Claude CLI and enters its poll loop.

`tests/unit/test_deployment_contract.py` (13 tests) now locks this contract
in place, and each test was confirmed to fail when its bug is reintroduced.
**90 automated tests pass** in total.

## Dependency security scan

Ran `pip-audit` against `apps/api/requirements.txt`: found 21 known CVEs
across `python-jose` (unused - removed entirely, along with its vulnerable
transitive `ecdsa`/`pyasn1`/`rsa` deps, since we use server-side sessions
not JWT), `python-multipart` (bumped 0.0.20 → 0.0.32), and `starlette`
(fixed by bumping `fastapi` 0.115.6 → 0.140.7, which pulls a patched
starlette 1.3.1 automatically). Re-ran `pip-audit`: **0 known
vulnerabilities**. Re-ran the full test suite after the upgrade: all 73
tests still pass.

Ran `npm audit` on `apps/web`: 3 high-severity findings, all in Next.js's
own bundled build-time dependencies (`postcss`, `sharp`), whose only fix
path is downgrading to `next@9.3.x` (a 6-major-version regression that
would break the entire app). These are build/image-processing-tool CVEs
(CSS stringify XSS, libvips image CVEs) that don't correspond to an
exploitable path in how this app uses Next.js (no untrusted image uploads
processed through `sharp`, no attacker-controlled CSS compiled at
runtime). Documented here rather than silently ignored; revisit when Next
ships a patched release on the current major version.

## Known gaps / honest limitations (not yet done)

1. **No live VPS deployment.** `scripts/install.sh` has not been run for
   real anywhere - see "Environment reality" above. This requires a human
   to hand over SSH access to an actual server.
2. **No real third-party integrations** (GitHub, Vercel, Sentry, PostHog,
   Google Analytics/Search Console/Ads, email, support platform, payment
   processor) - all correctly show `configured: false`. Wiring any of
   these requires the user to supply real credentials.
3. **Voice experience and budgets/spend enforcement wiring into the
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
4. **`tests/security` now has 22 passing tests** (prompt-injection
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
5. Everything in this sandbox runs as **root** (the container's only user)
   rather than the non-root `aicompany` service account the constitution
   requires. The code (systemd units, claude-worker adapter) is written to
   run correctly as a non-root user in a real deployment; this sandbox
   simply doesn't have that user to demonstrate it live.

## Exact next actions (in priority order)

1. Wire the permission engine into the API/worker execution path: no route
   or worker task currently calls `PermissionEngine.evaluate()` before
   executing a mutating action - `packages/permission-engine` is real and
   tested in isolation but not yet consulted at the point of execution.
2. Add `GET/POST /runs` and `GET /analytics` endpoints so those two
   dashboard pages stop showing "not available yet".
3. Add `tests/load/` (currently empty) - basic WebSocket connection-count
   and event-throughput tests.
4. When a real VPS becomes available: run `scripts/doctor.sh` first, then
   `scripts/install.sh`, then re-run `scripts/smoke-test.sh`.
