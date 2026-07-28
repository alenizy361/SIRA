# API Reference (current, real endpoints only)

Base URL: `http://localhost:8000` (dev) - all endpoints except `/health/*`
require the `rabit_session` cookie (set by `/auth/login` or
`/auth/onboard`).

## Health

- `GET /health/live` - process is up, no dependency checks.
- `GET /health/ready` - `{status, checks: {postgres, redis}}`.
- `GET /health/dependencies` - same detail, for the System Doctor page.

## Auth

- `POST /auth/onboard` `{organization_name, admin_email, admin_password (>=12 chars), admin_display_name}` - one-time bootstrap, 403 if an org already exists.
- `POST /auth/login` `{email, password}` - 401 wrong credentials, 423 locked out.
- `POST /auth/logout`
- `GET /auth/me` - `{id, organization_id, email, display_name, totp_enabled}`.

## Agents

- `GET /agents` - all 23 agents: `{agent_key, display_name_en, display_name_ar, mission, risk_ceiling, enabled, disabled_reason, current_state}`.

## Goals / Plans / Tasks

- `POST /goals` `{title, description, source?}` - creates a goal (state `goal_captured`).
- `GET /goals`, `GET /goals/{id}`.
- `POST /goals/{id}/transition` `{target_state}` - validated against the state machine.
- `POST /goals/{id}/plan` - **makes a real Claude Code CLI call** (CEO agent role, `--json-schema`); synchronous, can take 20s-3min; creates a `Plan` + `Task` graph and advances the goal to `plan_drafted`. 502 on any planning failure (never falls back to fake data).
- `GET /plans`, `GET /tasks` - read-only listings.

## Approvals

- `GET /approvals` - pending only.
- `POST /approvals/{id}/resolve` `{decision: approve|approve_with_conditions|reject|request_revision, conditions?, comment?}`.

## Audit

- `GET /audit-logs?limit=100`

## Reliability / governance views

- `GET /incidents`, `GET /budgets`, `GET /memory`
- `GET /integrations` - always reflects real DB state; a provider with no
  configured `Integration` row is synthesized as `configured: false`
  rather than omitted, so the UI can never show a nonexistent connection
  as active.
- `POST /system/emergency-stop` - see `docs/OPERATIONS.md`.

## WebSocket

- `WS /ws?since_seq=N` - requires the session cookie. First frame:
  `{"type":"hello","organization_id":...}`, then replay of any events with
  `sequence > since_seq` (bounded to the last 2000 events per
  organization), then live events, plus a `{"type":"heartbeat"}` frame
  every 20 seconds. Event shape and the full `EventType`/`CoreState`
  vocabulary: `packages/contracts/events.py`.

## Not yet implemented (return 404 today)

`/runs`, `/analytics` - the corresponding dashboard pages show an honest
"not available yet" state rather than mock data.
