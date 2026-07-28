# Architecture Decisions — Rabit AI Company OS

Format: date, decision, rationale. Newest first.

## 2026-07-28 — Test the deployment artifacts, not just the source

**Decision:** Added `tests/unit/test_deployment_contract.py`, which asserts
the contract between the source tree and what actually ships: every source
tree `apps/api` imports is COPYed into `api.Dockerfile` and present on its
`PYTHONPATH`; `next.config.ts` enables the `standalone` output that
`web.Dockerfile`'s runner stage copies; `install.sh` creates the host venv
`run.sh` depends on; and every `Path(__file__).parents[N]` sys.path
bootstrap resolves to a directory that exists.

**Rationale — the actual root cause of a painful deploy.** Everything in
this project was built and verified by running processes *directly on a dev
machine*. The thing that ships is different in two ways that were never
exercised: a **containerized** api/web stack, and a **host-level** worker
process. Every deployment failure came from that gap, one at a time:
`rsync` missing from the installer's package list; a duplicate top-level
`gzip` directive nginx rejects; a smoke test that raced container startup;
`services/claude-worker` never COPYed into the api image
(`ModuleNotFoundError`, crash-loop); `install.sh` never rebuilding images
after a source change; `next.config.ts` missing `output: "standalone"`; and
`run.sh` sourcing a venv nothing created. Each individual fix was correct
but the *pattern* was the problem — a fix-one-symptom-per-round loop that
cost the user many attempts.

This session broke that loop by starting a real Docker daemon in the dev
sandbox and actually building and running the images: the api image now
verifiably imports `app.main`/`claude_worker`/`contracts`/`permission_engine`/
`orchestrator` inside the container, the full compose stack (postgres +
redis + api + web) comes up healthy, containerized Alembic migrations run,
and `scripts/smoke-test.sh` passes against it end to end. The worker was
verified by launching `run.sh` in a simulated `APP_ROOT` and watching it
authenticate the CLI and enter its poll loop. Where the sandbox genuinely
could not run something (npm install fails against the sandbox's TLS
proxy), that step was validated another way (building the runner stage from
a real local `.next/standalone` and confirming `node server.js` serves HTTP
200) rather than assumed.

The regression tests were themselves verified by reintroducing each bug and
confirming the corresponding test fails — a guard that cannot fail is not a
guard.

**Also fixed here:** `publisher.py` and `bus.py` computed
`parents[3] / "packages"`, which resolves to a nonexistent `apps/packages`.
It never broke anything because `PYTHONPATH` already covered it, so the
broken `sys.path` entry sat inert — exactly the kind of latent landmine the
new test now catches.

## 2026-07-28 — One shared event publisher, not three ad-hoc ones

**Decision:** Added `apps/api/app/realtime/publisher.py` as the single
place that constructs and publishes a realtime `Event` (sequence number,
Redis publish, replay-log append). Both `services/claude-worker` (task
execution) and `apps/api/app/routers/goals.py` /
`app/services/planning.py` (goal/plan/task creation) call into it rather
than each hand-rolling `EventBus` calls.

**Rationale:** `services/claude-worker` already imports `app.*` modules
directly throughout this monorepo (see `worker.py`'s existing imports of
`app.config`/`app.db`/`app.models`), so there was no reason to duplicate
sequence-numbering/Redis-publish logic in a second module - one publisher
function keeps the wire format, forbidden-payload-key check, and replay
log behavior identical regardless of which side of the system triggered
the event.

**Rationale for what publishes what:** `core.state.changed` transitions
follow the actual unit of work rather than a fixed timer -
`planning`/`coding` while a Claude CLI call is in flight, `reviewing`
immediately after, then `idle` (or `paused` on an authentication
failure) - so the state always reflects genuine backend activity, never
an animation loop decoupled from what's actually happening. Verified with
a live browser check (not just an automated test): publishing a
`CoreState.WARNING` event against the running API changed the actual
rendered sphere color and Arabic state label in an already-open browser
tab with no reload - see `docs/BUILD_STATUS.md`.

## 2026-07-28 — Dependency versions bumped after a real vulnerability scan

**Decision:** `apps/api/requirements.txt`: removed `python-jose` (and its
transitive `ecdsa`/`pyasn1`/`rsa`) entirely - it was included speculatively
for JWT support but never actually used once server-side revocable
sessions were chosen instead; bumped `fastapi` 0.115.6 → 0.140.7 (pulls a
patched `starlette` 1.3.1 automatically) and `python-multipart` 0.0.20 →
0.0.32.

**Rationale:** `pip-audit` found 21 known CVEs across exactly those three
packages. Removing an unused dependency is strictly better than pinning a
"fixed" version of something that shouldn't be there at all. Re-ran
`pip-audit` (0 findings) and the full test suite (73/73 passing) after the
upgrade before committing it.

## 2026-07-28 — CEO-agent planning is a real synchronous Claude CLI call, not a template

**Decision:** `POST /goals/{id}/plan` (`apps/api/app/services/planning.py`)
shells out to the real `claude` CLI with `--json-schema` and a CEO-agent
prompt, parses the structured JSON result, and persists a real Plan/Task
graph - rather than a canned/templated plan generator.

**Rationale:** Constitution rule #1 ("no placeholder business logic") and
the product vision explicitly describe the CEO agent converting goals
into plans via genuine reasoning. A template would violate both. Kept
synchronous (not queued) because a single planning call is short (no code
tools involved, small timeout) - background execution is reserved for
actual code-writing tasks via `services/claude-worker`'s poll loop.

## 2026-07-28 — Environment reality check before build

**Decision:** Build the full monorepo, migrations, and real (non-Docker) local
integration tests inside this sandbox; do not fabricate a "live VPS install."

**Rationale:** Repository inspection showed `alenizy361/SIRA` was a blank
single-commit repo (no existing `autonomous-company-os` install to migrate),
and the execution environment is an ephemeral container with no systemd PID 1,
no running Docker daemon, and no independently-authenticated `aicompany` OS
user for a separate Claude Max CLI session. Per the constitution's own rule
("every completion claim must include evidence" / "do not claim a feature is
finished if simulated or untested"), we do not simulate a production install.
Confirmed with the user (2026-07-28): build code in-repo, defer live
deployment to a session with real VPS/SSH access. See `docs/BUILD_STATUS.md`
for exactly what has been executed/tested in this sandbox vs. what still
requires a real server.

## 2026-07-28 — No Kubernetes/Temporal; DB-backed state machine + Redis queue

**Decision:** Use PostgreSQL as the durable orchestration state store and
Redis for queues/leases/pub-sub, with a hand-rolled state machine in
`services/orchestrator`. No Temporal, no Kubernetes.

**Rationale:** Constitution section 4 explicitly asks for this on a
single-VPS first release, with clean interfaces for a future swap.

## 2026-07-28 — Claude worker talks to the real `claude` CLI via subprocess

**Decision:** `services/claude-worker` shells out to the locally authenticated
`claude` CLI (found at `/opt/node22/bin/claude`, v2.1.220) rather than the
Anthropic API, per the no-API-key constraint. Streaming is attempted via
`--output-format stream-json`; a line-buffered text fallback is implemented
for older/incompatible CLI builds. Verified CLI flags empirically with
`claude --help` before wiring the adapter (see BUILD_STATUS for the exact
flag set discovered on this box).

## 2026-07-28 — Currency/timezone/locale defaults

**Decision:** SAR is the operating currency, Asia/Riyadh the default
timezone, Arabic is the first-class default UI locale with instant EN
switching, per constitution sections 2/3.

## 2026-07-28 — Postgres/Redis run natively in this sandbox, not via Docker

**Decision:** For local dev/test in this container we start
`postgres` (already apt-installed, v16) and `redis-server` directly as
foreground/background processes rather than through `docker-compose`,
since no Docker daemon is reachable here. `docker-compose.yml` is still the
authoritative production deployment definition for a real VPS with Docker.

## 2026-07-28 — Monorepo layout matches constitution section 4 verbatim

**Decision:** Directory layout under `/home/user/SIRA` mirrors the spec's
tree exactly (apps/, services/, packages/, constitution/, infra/, scripts/,
tests/, workspace/, docs/) so future contributors can navigate by the same
mental model as the constitution documents.
