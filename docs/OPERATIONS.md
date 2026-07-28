# Operations

## Services

| Service | How it runs | Port | Notes |
|---|---|---|---|
| `postgres` | Docker (prod) / native `pg_ctlcluster` (this sandbox) | 5432, internal only | Never expose publicly - `docker-compose.yml` uses `expose`, not `ports`. |
| `redis` | Docker (prod) / native `redis-server` (this sandbox) | 6379, internal only | Same - internal only. |
| `api` | Docker (`infra/docker/api.Dockerfile`) or `uvicorn` directly | 127.0.0.1:8000 | FastAPI control plane. |
| `web` | Docker (`infra/docker/web.Dockerfile`) or `next start` | 127.0.0.1:3000 | Next.js dashboard. |
| `claude-worker` | Host systemd unit as user `aicompany` (never containerized) | none (outbound only) | Needs the `claude` CLI's per-user login state. |

Nginx (`infra/nginx/rabit-os.conf.template`) reverse-proxies `/` → web,
`/api/` and `/ws` → api, and terminates TLS when a domain is configured.

## Day-to-day commands

```bash
scripts/doctor.sh          # read-only health check with actionable fixes
scripts/smoke-test.sh      # hits /health/live, /health/ready, /health/dependencies, web root
scripts/backup.sh          # timestamped DB + Redis + config backup
scripts/restore.sh <path>  # restore a specific backup (requires --yes)
scripts/upgrade.sh         # backup -> pull -> migrate -> smoke-test -> auto-rollback on failure
scripts/rollback.sh        # restore the most recent verified backup
```

## Restarting a service

Docker Compose deployment:
```bash
docker compose restart api      # or: web, postgres, redis
```

Systemd (claude-worker, or api/web when not using Docker Compose):
```bash
sudo systemctl restart rabit-claude-worker
sudo journalctl -u rabit-claude-worker -f
```

## Emergency stop

`POST /system/emergency-stop` (authenticated) immediately: sets the
organization's `autonomy_mode` to `observe_only` (the claude-worker poll
loop checks this before picking up any READY task - see
`services/claude-worker/claude_worker/worker.py`), and revokes all
outstanding `run_leases` rows. It does not yet forcibly kill an in-flight
`claude` subprocess mid-run (see `docs/BUILD_STATUS.md` known gaps) -
today's in-flight runs finish naturally; no *new* work starts.

To resume: an administrator must explicitly set the organization back to
`execute_low_risk` or `controlled_autonomous` (currently a direct DB
update - a `/settings` UI control for this is a follow-up).

## Backups

`scripts/backup.sh` writes to `$BACKUP_ROOT` (default
`/opt/rabit-ai-company-os/backups/<timestamp>/`): a `pg_dump`, a Redis RDB
copy, `.env` (permissions 600, never committed to git), the active Nginx
config, and the active systemd unit files, plus the current git commit SHA
and branch. Verify a backup is restorable periodically with
`scripts/restore.sh <path> --yes` against a disposable environment before
you need it for real.

## Claude Code CLI authentication

The `claude-worker` service runs as the `aicompany` OS user and needs its
own login:

```bash
sudo -u aicompany -H claude auth login
sudo -u aicompany -H claude auth status   # should show {"loggedIn": true, "authMethod": "oauth_token"}
```

No `ANTHROPIC_API_KEY` is used or accepted anywhere in this system - if
`claude auth status` shows `authMethod: api_key`, that is a
misconfiguration relative to this build's design (Claude Max subscription
only).

## Reboot recovery

On restart, `run_leases` rows whose `expires_at` has passed are reclaimed
by `orchestrator.leasing.expire_stale_leases()` - a crashed worker never
leaves a task permanently stuck. The claude-worker's poll loop
(`worker.py`) resumes scanning for READY tasks automatically once the
systemd unit restarts (`Restart=on-failure` in
`infra/systemd/rabit-claude-worker.service`).
