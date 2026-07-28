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

## The dashboard still shows an OLD design after a successful install

Symptom: `scripts/install.sh` finishes, smoke tests pass, but the browser
still renders a previous version of the UI.

This is almost always a **stale source checkout**, not a cache. The
installer does **not** run `git pull`. It deploys whatever is in the
checkout you launched it from (`REPO_ROOT`, i.e. the parent of the
`scripts/` directory you invoked) and rsyncs it into `APP_ROOT`
(`/opt/rabit-ai-company-os`). Two traps follow from that:

1. **The clone is behind.** Re-running the installer from a checkout that
   was never pulled redeploys the old commit perfectly successfully.
2. **You ran the installer from `APP_ROOT` itself.** The rsync excludes
   `.git`, so `/opt/rabit-ai-company-os` is *not* a git repo and can never
   be updated with `git pull`. In that case `sync_repo_to_app_root` logs
   "Already running from … — nothing to sync" and the same old code ships
   forever.

The installer now reports `Deploying commit: <sha> on <branch>` at the
start, warns when the checkout is behind its upstream, writes
`APP_ROOT/BUILD_INFO`, and prints `Deployed commit:` in the final summary.

Diagnose which version is actually live:

```bash
cat /opt/rabit-ai-company-os/BUILD_INFO      # written by the installer
docker image inspect --format '{{.Created}}' "$(docker compose -f /opt/rabit-ai-company-os/docker-compose.yml images -q web)"
```

Fix — pull in the real clone, then re-run **that clone's** installer:

```bash
find /root /home /opt /srv -maxdepth 4 -type d -name .git 2>/dev/null   # locate the clone
CLONE=/root/SIRA                       # <- whatever you found
git -C "$CLONE" fetch origin
git -C "$CLONE" checkout <branch>
git -C "$CLONE" pull origin <branch>
git -C "$CLONE" log --oneline -1       # confirm the commit you expect
sudo "$CLONE/scripts/install.sh"
```

If the UI still looks old, force a rebuild that cannot reuse a cached
image layer, then recreate the container:

```bash
cd /opt/rabit-ai-company-os
docker compose build --no-cache web
docker compose up -d --force-recreate web
```

Verify from outside the box (substitute your host). The current UI ships a
`space-backdrop` element — that marker is the reliable check:

```bash
curl -s http://<host>/command-center | grep -c space-backdrop   # expect > 0
```

`/` redirects to `/command-center`. It answers `307` once the config-level
redirect in `apps/web/next.config.ts` is deployed; older builds answered
`200` and performed the hop client-side, so the status code alone does not
tell you whether the deploy is current — use the marker above.

## The installer prints a dashboard URL I cannot open

`install.sh` derives that URL from `hostname -I` when `DOMAIN` is unset, so
on a NAT'd host it prints the machine's **private** address (e.g.
`172.16.1.224`), which is only reachable from inside the server's own
network. The site is served on port 80 by Nginx regardless — open the
server's **public** IP or domain instead. The installer now detects a
private address and says so explicitly; set `DOMAIN=<your-domain>` before
running it to get the right URL printed (and to enable TLS via certbot).
