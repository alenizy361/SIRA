#!/usr/bin/env bash
#
# One-command redeploy for a live Rabit AI Company OS server.
#
# Why this exists: scripts/install.sh deliberately never runs `git pull` - it
# deploys whatever checkout it was launched from. On a server whose only copy
# of the code is APP_ROOT (which has no .git, because the install rsync
# excludes it), there is no way to get newer code without first obtaining a
# real clone. Operators hit this as "the install succeeded but the dashboard
# still shows the old UI". This script does the whole update path in one go:
#
#   fresh clone/pull  ->  install from THAT clone  ->  force a real web
#   image rebuild + container recreate  ->  verify the new UI is actually live
#
# Usage (as root on the server):
#   curl -fsSL <raw-url-of-this-file> | bash
# or:
#   bash scripts/redeploy.sh
#
# Override any of these via the environment if your layout differs:
#   RABIT_BRANCH  RABIT_CLONE  RABIT_REPO_URL  APP_ROOT  WEB_URL
set -euo pipefail

BRANCH="${RABIT_BRANCH:-claude/rabit-ai-company-os-build-77fj8p}"
CLONE="${RABIT_CLONE:-/root/SIRA}"
REPO_URL="${RABIT_REPO_URL:-https://github.com/alenizy361/SIRA.git}"
APP_ROOT="${APP_ROOT:-/opt/rabit-ai-company-os}"
WEB_URL="${WEB_URL:-http://localhost:18080}"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
fail() { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { fail "Run this as root."; exit 1; }
command -v git >/dev/null || { fail "git is not installed."; exit 1; }
command -v docker >/dev/null || { fail "docker is not installed."; exit 1; }

# ---------------------------------------------------------------------------
# 0. Preflight: a foreign listener on the datastore ports would make the
#    compose bring-up fail halfway through and leave the stack down. Catch it
#    before touching a working install rather than after.
# ---------------------------------------------------------------------------
# Deliberately does NOT use `ss`/`netstat`: both are absent or non-functional
# in some minimal images, and a silent false "port is free" here is exactly
# the case this guard exists to catch. A bash /dev/tcp connect plus `docker ps`
# needs nothing that is not already required to run this stack.
port_conflict() {
  local port="$1"
  # Nothing accepting connections -> definitely free.
  timeout 2 bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/${port}" 2>/dev/null || return 1
  # Occupied, but by one of our own published compose containers -> fine.
  docker ps --format '{{.Ports}}' 2>/dev/null | grep -q "127\.0\.0\.1:${port}->" && return 1
  return 0
}
for p in 5432 6379; do
  if port_conflict "$p"; then
    warn "Something that is not Docker is already listening on port ${p}."
    warn "docker compose publishes Postgres/Redis on 127.0.0.1:${p} so the host"
    warn "worker can reach them, so this bring-up would fail with"
    warn "'port is already allocated'. Stop that service, or set POSTGRES_PORT/"
    warn "REDIS_PORT in ${APP_ROOT}/.env (and match DATABASE_URL/REDIS_URL)."
    fail "Aborting before changing anything."
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# 1. Get the newest source into a REAL git clone
# ---------------------------------------------------------------------------
say "Fetching latest source (${BRANCH})"
if [[ -d "$CLONE/.git" ]]; then
  git -C "$CLONE" remote set-url origin "$REPO_URL"
  git -C "$CLONE" fetch --prune origin
  git -C "$CLONE" checkout -B "$BRANCH" "origin/${BRANCH}"
  git -C "$CLONE" reset --hard "origin/${BRANCH}"
else
  rm -rf "$CLONE"
  git clone --branch "$BRANCH" "$REPO_URL" "$CLONE"
fi
NEW_SHA="$(git -C "$CLONE" rev-parse --short HEAD)"
ok "Source checkout ${CLONE} is at ${NEW_SHA} (${BRANCH})"

PREV_SHA="$(sed -n 's/^commit=//p' "${APP_ROOT}/BUILD_INFO" 2>/dev/null || true)"
[[ -n "$PREV_SHA" ]] && echo "     previously deployed: ${PREV_SHA}"

# ---------------------------------------------------------------------------
# 2. Install FROM THAT CLONE (never from APP_ROOT, which cannot self-update)
# ---------------------------------------------------------------------------
say "Running installer from ${CLONE}"
"$CLONE/scripts/install.sh"

# ---------------------------------------------------------------------------
# 3. Guarantee the running web container came from a freshly built image.
#    --no-cache defeats any stale layer; --force-recreate defeats "the old
#    container was merely restarted".
# ---------------------------------------------------------------------------
say "Rebuilding and recreating the web container"
cd "$APP_ROOT"
docker compose build --no-cache web
docker compose up -d --force-recreate web

# ---------------------------------------------------------------------------
# 4. Verify the NEW UI is genuinely being served, not just that a container is
#    up. 'space-backdrop' exists only in the redesigned frontend and is the
#    single reliable discriminator, so it alone gates success. The "/" status
#    is reported for information only: it is 307 once the config-level
#    redirect (apps/web/next.config.ts) is deployed, but was 200 on earlier
#    builds where the hop happened client-side - so it must NOT gate.
# ---------------------------------------------------------------------------
say "Verifying the served frontend"
marker=0
root_code=000
for _ in $(seq 1 20); do
  marker="$(curl -fsS -m 10 "${WEB_URL}/command-center" 2>/dev/null | grep -c 'space-backdrop' || true)"
  root_code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "${WEB_URL}/" 2>/dev/null || echo 000)"
  [[ "${marker:-0}" -gt 0 ]] && break
  sleep 3
done

echo ""
echo "  deployed commit : ${NEW_SHA}"
echo "  space-backdrop  : ${marker}   (expected: > 0 - this is the check that matters)"
echo "  GET /           : ${root_code} (307 = server-side redirect; 200 = older client-side hop, also OK)"
echo ""

if [[ "${marker:-0}" -gt 0 ]]; then
  ok "The redesigned command center is live."
  echo ""
  echo "  Open the dashboard and hard-refresh once (Ctrl+Shift+R / long-press reload)."
  exit 0
fi

fail "The server is still serving the OLD frontend."
echo ""
echo "  Collect this and send it back:"
echo "    cat ${APP_ROOT}/BUILD_INFO"
echo "    grep -c space-backdrop ${APP_ROOT}/apps/web/src/app/globals.css"
echo "    docker compose -f ${APP_ROOT}/docker-compose.yml ps"
echo "    docker image inspect --format '{{.Created}}' \\"
echo "      \"\$(docker compose -f ${APP_ROOT}/docker-compose.yml images -q web)\""
exit 1
