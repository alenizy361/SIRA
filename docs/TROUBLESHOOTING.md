# Troubleshooting

Run `scripts/doctor.sh` first - it checks OS/resources, Docker or native
Postgres/Redis reachability, the `aicompany` user, `claude auth status`,
Nginx config validity, and systemd unit status, printing a PASS/FAIL/WARN
and a one-line fix per check.

## "claude auth status" shows loggedIn: false

The `aicompany` user (or whichever OS user runs `services/claude-worker`)
has not logged in. Run, as that user:
```bash
sudo -u aicompany -H claude auth login
```
No API key is used anywhere in this system - if you're tempted to set
`ANTHROPIC_API_KEY` to work around this, don't; fix the login instead.

## `POST /goals/{id}/plan` returns 502

The underlying `claude` CLI call failed or returned output that didn't
parse as the required JSON schema. Check:
- `claude auth status` for the API's OS user (not necessarily
  `aicompany` if you're running the API directly rather than via systemd).
- Whether you've hit a Claude Max usage/rate limit for the current window
  (the CLI's `stream-json` output includes a `rate_limit_event` message
  when this is the cause - re-running after the window resets typically
  succeeds; this build hit exactly this transient failure once during
  development and it succeeded on retry).
- The API log (`journalctl -u rabit-api` or the container logs) for the
  `stderr_tail` captured on the `RunResult`.

## WebSocket keeps reconnecting / dashboard shows "connecting"

- Confirm you're logged in (the `/ws` endpoint requires the session
  cookie - an expired or missing session closes the connection with code
  1008).
- Confirm Redis is reachable (`GET /health/dependencies`) - the event bus
  is entirely Redis pub/sub-backed.

## Postgres/Redis connection refused

In this sandbox (no systemd, no Docker daemon), start them natively:
```bash
pg_ctlcluster 16 main start
redis-server --daemonize yes
```
In a real Docker Compose deployment: `docker compose up -d postgres redis`
and check `docker compose logs postgres`.

## A task is stuck in "running" after a crash/restart

It shouldn't stay stuck: `orchestrator.leasing.expire_stale_leases()`
reclaims any lease whose `expires_at` has passed, making the task eligible
for re-scheduling. If it's been stuck longer than the task's
`timeout_seconds` plus a grace period, check whether the claude-worker
process is actually running (`systemctl status rabit-claude-worker`) - a
stopped worker never expires or renews its own leases.

## Nginx won't reload / `nginx -t` fails

`scripts/install.sh` and `scripts/upgrade.sh` both run `nginx -t` before
reloading and abort on failure rather than leaving Nginx in a broken
state. Check `infra/nginx/rabit-os.conf.template` was rendered with a
real `$DOMAIN` value, and that ports 3000/8000 are actually listening
locally before Nginx tries to proxy to them.
