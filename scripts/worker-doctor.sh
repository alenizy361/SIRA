#!/usr/bin/env bash
#
# Diagnose (and where safe, auto-fix) the host claude-worker pipeline:
# the #1 operator symptom is "the CEO has been planning for an hour" —
# a goal frozen because the worker never picked it up or died mid-claim.
#
# Checks, in causal order:
#   1. Is rabit-claude-worker.service running? (last log lines if not)
#   2. Is the aicompany user's Claude CLI actually authenticated?
#      (This is THE manual install step — it cannot be scripted.)
#   3. Can this host reach Postgres/Redis on loopback?
#   4. Are any goals stuck? plan_status='running' with no live worker
#      claim older than 10 minutes is a dead claim -> reset to
#      'requested' so the next poll tick retries it.
#   5. If everything is healthy, restart the worker for a fresh tick.
#
# Usage (as root):  bash scripts/worker-doctor.sh
#   or:             curl -fsSL <raw-url> | bash
set -uo pipefail

APP_ROOT="${APP_ROOT:-/opt/rabit-ai-company-os}"
AICOMPANY_USER="${AICOMPANY_USER:-aicompany}"
UNIT="rabit-claude-worker.service"

ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { bad "Run as root."; exit 1; }

PROBLEMS=0
NEED_LOGIN=0

# ---------------------------------------------------------------------------
# WHY did it stop? systemd records the exact outcome, which distinguishes the
# cases that look identical from outside: an OOM kill (memory ceiling too low
# for a real claude run), a non-zero exit (a crash - read the traceback), or a
# latched start limit. Print this whether or not it is currently up, because a
# unit that keeps dying and restarting looks "active" the moment you check.
say "0/5 last exit reason"
systemctl show "$UNIT" \
  -p Result,ExecMainStatus,ExecMainCode,NRestarts,MemoryMax,MemoryPeak,ActiveState,SubState \
  2>/dev/null | sed 's/^/  /'
RESULT="$(systemctl show "$UNIT" -p Result --value 2>/dev/null || true)"
case "$RESULT" in
  oom-kill)
    bad "The worker was OOM-KILLED: it hit its MemoryMax ceiling."
    echo "     Fix: re-run scripts/install.sh - it now sizes MemoryMax to this host's RAM."
    PROBLEMS=$((PROBLEMS+1)) ;;
  start-limit-hit)
    bad "systemd LATCHED this unit off after repeated failures (start-limit-hit)."
    echo "     This script clears it below; the underlying crash still needs the fix above."
    PROBLEMS=$((PROBLEMS+1)) ;;
  signal)
    warn "The worker was killed by a signal (often the kernel OOM killer)." ;;
esac
# Kernel-level OOM evidence, which systemd does not always attribute to the unit.
if journalctl -k -n 300 --no-pager 2>/dev/null | grep -aiE "out of memory|oom-kill" | grep -aiE "claude|python" | tail -3 | grep -q .; then
  bad "Kernel OOM killer has recently killed a claude/python process:"
  journalctl -k -n 300 --no-pager 2>/dev/null | grep -aiE "out of memory|oom-kill" | tail -3 | sed 's/^/     /'
  PROBLEMS=$((PROBLEMS+1))
fi

say "1/5 worker service"
if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
  ok "$UNIT is active"
else
  bad "$UNIT is NOT running"
  PROBLEMS=$((PROBLEMS+1))
  # The FIRST exception is what matters; the tail is usually just systemd's
  # restart chatter. Surface the actual Python error line explicitly so the
  # cause is obvious without reading a wall of traceback.
  echo "---- the actual error (most recent) ----"
  journalctl -u "$UNIT" -n 400 --no-pager 2>/dev/null \
    | grep -aiE "error|exception|traceback|refused|denied|no such|not found|could not|failed to" \
    | tail -8
  echo "---- last 25 log lines ----"
  journalctl -u "$UNIT" -n 25 --no-pager 2>/dev/null | tail -25
  echo "---------------------------"
  # A latched start-rate limit makes every later restart a silent no-op.
  if systemctl show "$UNIT" -p ExecMainStatus,NRestarts,Result 2>/dev/null | grep -q "Result=start-limit-hit"; then
    warn "systemd has LATCHED this unit off (start-limit-hit) - clearing it now."
  fi
fi

# ---------------------------------------------------------------------------
# Datastore reachability FROM THE HOST is the worker's most common hard
# dependency failure: the API can be perfectly healthy (it talks to Postgres
# over the compose network as 'postgres') while the host worker, which must
# reach 127.0.0.1:5432, cannot connect at all because the container is not
# publishing that port. Show the published ports so the difference is visible.
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  say "1b/5 datastore ports published to the host"
  docker ps --format '  {{.Names}}  {{.Ports}}' 2>/dev/null | grep -Ei 'postgres|redis' || \
    warn "No postgres/redis containers are running - start them: docker compose -f ${APP_ROOT}/docker-compose.yml up -d"
fi

# ---------------------------------------------------------------------------
say "2/5 Claude CLI authentication (user: ${AICOMPANY_USER})"
# ---------------------------------------------------------------------------
AUTH_JSON="$(sudo -u "$AICOMPANY_USER" -H bash -lc 'claude auth status 2>/dev/null' || true)"
if printf '%s' "$AUTH_JSON" | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
  ok "Claude CLI is authenticated for ${AICOMPANY_USER}"
else
  bad "Claude CLI is NOT authenticated for ${AICOMPANY_USER} (got: ${AUTH_JSON:-empty})"
  NEED_LOGIN=1
  PROBLEMS=$((PROBLEMS+1))
fi

# ---------------------------------------------------------------------------
say "3/5 datastores from the host"
# ---------------------------------------------------------------------------
DB_URL=""
if [[ -f "$APP_ROOT/.env" ]]; then
  DB_URL="$(sed -n 's/^DATABASE_URL=//p' "$APP_ROOT/.env" | head -1)"
fi
# psql does not understand SQLAlchemy's +psycopg dialect marker.
PSQL_URL="${DB_URL/postgresql+psycopg:\/\//postgresql:\/\/}"

# Probe Postgres AUTHORITATIVELY - connect exactly the way the worker does,
# via its own venv's psycopg. Using `psql` alone gives a false "NOT reachable"
# on a host that simply doesn't have the postgresql-client package installed
# (a very common case: redis-cli is present but psql is not), which sends
# operators chasing a database problem that does not exist.
VENV_PY="$APP_ROOT/.venv/bin/python"
if [[ -z "$DB_URL" ]]; then
  bad "Could not read DATABASE_URL from ${APP_ROOT}/.env"
  PROBLEMS=$((PROBLEMS+1))
elif [[ -x "$VENV_PY" ]] && "$VENV_PY" - "$DB_URL" <<'PY' >/dev/null 2>&1
import sys
import psycopg
url = sys.argv[1].replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(url, connect_timeout=5) as conn:
    conn.execute("SELECT 1")
PY
then
  ok "Postgres reachable from the host (via the worker's own psycopg)"
elif command -v psql >/dev/null 2>&1 && psql "$PSQL_URL" -tAc 'SELECT 1' >/dev/null 2>&1; then
  ok "Postgres reachable from the host (via psql)"
elif [[ ! -x "$VENV_PY" ]] && ! command -v psql >/dev/null 2>&1; then
  warn "Cannot verify Postgres: neither the worker venv nor psql is available here."
  warn "The worker itself will still connect - check its heartbeat after it restarts."
else
  bad "Postgres NOT reachable from the host (the worker cannot work without it)"
  echo "     URL host/port from .env: ${DB_URL#*@}"
  echo "     Is the compose stack up?  docker compose -f ${APP_ROOT}/docker-compose.yml ps"
  PROBLEMS=$((PROBLEMS+1))
fi
if redis-cli -h 127.0.0.1 ping 2>/dev/null | grep -q PONG; then
  ok "Redis reachable from the host"
else
  warn "Redis NOT reachable from the host (events will not publish)"
  PROBLEMS=$((PROBLEMS+1))
fi

# ---------------------------------------------------------------------------
say "4/5 goals waiting on the CEO"
# ---------------------------------------------------------------------------
if [[ -n "$PSQL_URL" ]]; then
  psql "$PSQL_URL" -c "
    SELECT left(id::text, 8) AS goal, left(title, 40) AS title,
           metadata_json->>'plan_status' AS plan_status,
           to_char(updated_at, 'HH24:MI') AS updated
    FROM goals
    WHERE metadata_json->>'plan_status' IS NOT NULL
    ORDER BY updated_at DESC LIMIT 8;" 2>/dev/null || true

  # A 'running' claim older than 10 minutes is dead (planning times out at
  # 3 minutes) — reset it so the worker retries on its next tick.
  # Count only returned row UUIDs — psql prints the "UPDATE n" command tag
  # even under -tA, which a bare line-count would misread as a reset row.
  RESET=$(psql "$PSQL_URL" -tAc "
    UPDATE goals
    SET metadata_json = jsonb_set(metadata_json, '{plan_status}', '\"requested\"')
    WHERE metadata_json->>'plan_status' = 'running'
      AND updated_at < now() - interval '10 minutes'
    RETURNING id;" 2>/dev/null | grep -cE '^[0-9a-f]{8}-' || true)
  if [[ "${RESET:-0}" -gt 0 ]]; then
    warn "Reset ${RESET} goal(s) stuck in a dead 'running' claim -> 'requested' (worker will retry)"
  else
    ok "No dead planning claims"
  fi

  PENDING=$(psql "$PSQL_URL" -tAc "
    SELECT count(*) FROM goals WHERE metadata_json->>'plan_status' = 'requested';" 2>/dev/null || echo "?")
  echo "     goals waiting for planning: ${PENDING}"
fi

# ---------------------------------------------------------------------------
say "5/5 verdict"
# ---------------------------------------------------------------------------
if [[ "$NEED_LOGIN" -eq 1 ]]; then
  echo ""
  bad "ROOT CAUSE (most likely): the worker has no Claude session, so the CEO"
  echo "       never actually runs — the dashboard shows 'planning' forever."
  echo ""
  echo "  Fix (interactive, cannot be scripted) — run this, follow the login link,"
  echo "  then the worker restarts and picks the goal up within ~5 seconds:"
  echo ""
  echo "      sudo -u ${AICOMPANY_USER} -H claude auth login && systemctl restart ${UNIT}"
  echo ""
  exit 1
fi

# Clear systemd's start-rate latch BEFORE restarting. The unit is
# Restart=on-failure with StartLimitBurst=10/StartLimitIntervalSec=600, so once
# it has crash-looped systemd refuses further starts ("start request repeated
# too quickly") until the failure state is reset. Without this, a restart here
# silently does nothing and the worker stays dead even after its bug is fixed.
systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
systemctl restart "$UNIT" 2>/dev/null || true

# Verify it is genuinely UP a few seconds later - a unit that dies immediately
# after start looks identical to a healthy one at restart time.
sleep 3
if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
  ok "Worker is running (restarted for a fresh poll tick)"
else
  bad "Worker is STILL not running after restart. Its own log says why:"
  echo "---- last 25 log lines ----"
  journalctl -u "$UNIT" -n 25 --no-pager 2>/dev/null | tail -25
  echo "---------------------------"
  PROBLEMS=$((PROBLEMS+1))
fi

if [[ "$PROBLEMS" -eq 0 ]]; then
  ok "All checks passed — pending goals should start planning within ~5s."
  echo "     Confirm from your browser:"
  echo "       curl -s http://<this-server-public-ip>/api/health/worker"
  echo "     Watch it live:  journalctl -u ${UNIT} -f"
else
  warn "${PROBLEMS} problem(s) found above — fix those, then re-run this script."
fi
