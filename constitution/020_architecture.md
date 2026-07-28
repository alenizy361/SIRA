# 020 — Architecture

## Monorepo layout

```
apps/
  api/            FastAPI backend: REST + websocket, permission engine
                  entrypoint, agent-spec loader (apps/api/app/agents/loader.py)
  web/            Next.js command center: mobile-first, Arabic-first/RTL UI
services/
  orchestrator/   The state machine (this document, below)
  claude-worker/  Executes agent work by shelling out to the `claude` CLI
  browser-worker/ Sandboxed Playwright-driven browser automation
  reviewer-worker/Runs independent-review evaluations
  scheduler/      Fires time-based and recurring triggers as events, not polling
packages/
  contracts/      Shared schemas: risk_levels.py(.ts), events.py — single
                  source of truth, imported everywhere else
  agent-specs/    schema.json + one YAML/JSON file per agent (constitution/agents/
                  is the human-readable twin of these machine contracts)
  permission-engine/ R0-R5 evaluation (constitution/040_permissions.md)
  tool-registry/  Catalog of tools with per-agent allow/deny lists
  observability/  Event bus, structured logging, audit trail
  ui/             Shared design-system components (RTL-aware, per
                  constitution/agents/product_design.md)
infra/
  docker/, nginx/, systemd/, migrations/, monitoring/, backup/
scripts/          install.sh, upgrade.sh, rollback.sh, backup.sh, restore.sh,
                  doctor.sh, uninstall.sh (docs/SPEC_SOURCE.md section 23)
tests/            unit/, integration/, e2e/, security/, load/
constitution/     this document set + constitution/agents/
docs/             SPEC_SOURCE.md, DECISIONS.md, BUILD_STATUS.md
workspace/        isolated git worktrees agents build in (rule 7,
                  constitution/010_operating_principles.md)
```

## Stack

- **PostgreSQL** is the durable state store: goals, plans, tasks, reviews,
  incidents, decisions, agent-spec versions, and the event log itself. If it
  matters after a restart, it is a row in Postgres.
- **Redis** backs queues, worker leases, budget/rate counters, and pub/sub
  fan-out from the orchestrator to the dashboard's websocket connections.
  Nothing durable lives only in Redis.
- **FastAPI** (`apps/api`) is the API surface: REST for CRUD-shaped
  operations, websocket for the live event stream, and the process that
  loads and validates agent specs at startup
  (`constitution/030_agent_protocol.md`).
- **Next.js** (`apps/web`) is the command center itself — the mobile-first,
  Arabic-first/RTL dashboard described in `constitution/000_mission.md`.

## Why no Kubernetes or Temporal yet

This is a single-VPS first release (`docs/DECISIONS.md`,
"No Kubernetes/Temporal; DB-backed state machine + Redis queue"). Standing up
a workflow engine or a cluster before there is a second server to justify one
adds operational surface area — more things that can silently fail, more
infrastructure `constitution/010_operating_principles.md` rule 5 has to cover
— without buying reliability a well-tested DB-backed state machine and a
Redis queue don't already provide at this scale. The state machine's
transitions are written as pure, testable functions and the queue is used
through a narrow interface in `services/orchestrator`; swapping in Temporal
or a Kubernetes-native queue later is a backend swap behind that interface,
not a rewrite of agent logic or the permission model.

## The state machine

Every goal and every task within it moves through a defined set of phases.
The orchestrator (`services/orchestrator`) is the only thing allowed to
transition a phase, and every transition emits an `Event`
(`packages/contracts/events.py`) that the dashboard renders live, mapped to a
`CoreState` for the human-facing summary.

### Main path

1. **GOAL_CAPTURED** — a goal is recorded (voice, text, or dashboard input),
   a `correlation_id` is assigned, and it enters the graph.
   (`CoreState.LISTENING` / `EventType.GOAL_CREATED`)
2. **UNDERSTANDING** — the owning agent (typically CEO or Product Manager)
   gathers `required_context` and clarifies intent.
3. **PLANNING** — the goal is decomposed into a task graph.
   (`CoreState.PLANNING` / `EventType.PLAN_CREATED`)
4. **PLAN_REVIEW** — Independent Reviewer (and CTO for technical plans)
   validate feasibility, risk classification, and policy compliance before
   any task starts.
5. **DELEGATING** — tasks are assigned to owning agents.
   (`CoreState.DELEGATING` / `EventType.TASK_ASSIGNED`)
6. **TASK_RUNNING** — the assigned agent executes, sub-phases rendered as
   `CoreState.CODING` / `TESTING` / etc. depending on agent type.
   (`EventType.TASK_STARTED`, `TASK_PROGRESS`)
7. **REVIEWING** — QA, Independent Reviewer, and/or Security evaluate the
   required evidence. (`CoreState.REVIEWING` / `EventType.REVIEW_REQUESTED`,
   `REVIEW_COMPLETED`)
8. **WAITING_FOR_APPROVAL** — entered only if the action is R3 or above
   (`constitution/040_permissions.md`); parked until a human resolves it.
   (`CoreState.WAITING_FOR_APPROVAL` / `EventType.APPROVAL_REQUESTED`,
   `APPROVAL_RESOLVED`)
9. **DEPLOYING** — the approved change is rolled out (preview, or production
   once R3 approval is on record). (`CoreState.DEPLOYING`)
10. **MONITORING** — post-deploy health and metrics are watched before the
    task is allowed to close. (`CoreState.MONITORING`)
11. **COMPLETED** — evidence is attached, KPIs are recorded, the goal/task
    is terminal. (`EventType.TASK_COMPLETED`)

### Failure and side paths

- **RETRY_WAIT** — a transient tool/infra failure triggers bounded
  exponential backoff and a re-attempt, up to the task's configured
  `max_retries` (`constitution/010_operating_principles.md` rule 8); beyond
  that, the task moves to `FAILED`.
- **BLOCKED** — required context or an input is missing, a dependency isn't
  satisfied, or the task needs a non-approval human input (e.g. a missing
  credential). (`EventType.TASK_BLOCKED`)
- **FAILED** — retries are exhausted, quality checks failed without a viable
  remediation task, or the task was explicitly rejected. A human or
  Independent Reviewer must dispose of it: retry, re-plan, or cancel.
- **INCIDENT_OPENED** — a safety condition trips (security block, budget
  breach, repeated production failure). The orchestrator freezes every task
  in the affected subtree in this phase and hands control to the Incident
  Commander Agent. (`EventType.INCIDENT_OPENED`,
  `constitution/090_incident_response.md`). Tasks only leave this phase via
  `EventType.INCIDENT_RESOLVED`, resuming their prior phase, moving to
  `RETRY_WAIT`, or being marked `FAILED`/`CANCELLED` per the Incident
  Commander's documented disposition.
- **CANCELLED** — explicit human or CEO-level cancellation, reachable from
  any non-terminal phase.

Every transition above is enforced by the orchestrator, not by an agent's own
judgment call — an agent requests a transition, the state machine and the
permission engine (`constitution/040_permissions.md`) decide whether it is
allowed to happen.
