#!/usr/bin/env bash
#
# ONE COMMAND: install and run Rabit AI Company OS on your own Ubuntu/Debian
# machine, 24/7, starting again automatically after a reboot.
#
#   curl -fsSL https://raw.githubusercontent.com/alenizy361/SIRA/claude/rabit-ai-company-os-build-77fj8p/scripts/run-local.sh | sudo bash
#
# What it does:
#   1. installs prerequisites (git, docker, python venv) if missing
#   2. clones/updates the source into /opt/rabit-src
#   3. runs the real installer: Postgres + Redis + API + web in Docker, the
#      claude worker on the host as the non-root `aicompany` user
#   4. enables every service at boot so the company survives a restart
#   5. tells you the ONE manual step left (logging the CLI in) and the URL
#
# Local-first by design: everything binds to 127.0.0.1, so nothing is exposed
# to your network. Re-running this is safe - it updates in place and never
# overwrites your .env secrets or your database.
set -uo pipefail

BRANCH="${RABIT_BRANCH:-claude/rabit-ai-company-os-build-77fj8p}"
REPO_URL="${RABIT_REPO_URL:-https://github.com/alenizy361/SIRA.git}"
SRC="${RABIT_SRC:-/opt/rabit-src}"
APP_ROOT="${APP_ROOT:-/opt/rabit-ai-company-os}"
AICOMPANY_USER="${AICOMPANY_USER:-aicompany}"

ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  bad "Run this with sudo:  curl -fsSL <url> | sudo bash"
  exit 1
fi

# ---------------------------------------------------------------------------
say "1/5 Checking this machine"
# ---------------------------------------------------------------------------
if ! command -v apt-get >/dev/null 2>&1; then
  bad "This script targets Ubuntu/Debian (apt). For another distro, run scripts/install.sh manually."
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  bad "systemd is required to keep the company running 24/7 and to restart it after a reboot."
  exit 1
fi
ok "Ubuntu/Debian with systemd"

if ! command -v git >/dev/null 2>&1; then
  say "Installing git"
  apt-get update -qq && apt-get install -y -qq git || { bad "Could not install git."; exit 1; }
fi
ok "git present"

# ---------------------------------------------------------------------------
say "2/5 Getting the source into ${SRC}"
# ---------------------------------------------------------------------------
if [[ -d "$SRC/.git" ]]; then
  git -C "$SRC" remote set-url origin "$REPO_URL"
  git -C "$SRC" fetch --prune origin --quiet || { bad "Could not fetch. Check your internet connection."; exit 1; }
  git -C "$SRC" checkout -B "$BRANCH" "origin/${BRANCH}" --quiet
  git -C "$SRC" reset --hard "origin/${BRANCH}" --quiet
else
  rm -rf "$SRC"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$SRC" || { bad "Clone failed."; exit 1; }
fi
ok "Source at $(git -C "$SRC" rev-parse --short HEAD)"

# ---------------------------------------------------------------------------
say "3/5 Installing (Postgres, Redis, API, dashboard, worker)"
# ---------------------------------------------------------------------------
echo "    This can take several minutes the first time (Docker images build)."
# No DOMAIN: the installer binds everything to loopback, which is what a
# personal machine wants - nothing reachable from the local network.
if ! DOMAIN="" "$SRC/scripts/install.sh"; then
  bad "The installer did not finish cleanly. Scroll up for the first error."
  echo "     Then re-run this same command - it is safe to repeat."
  exit 1
fi

# ---------------------------------------------------------------------------
say "4/5 Making it survive reboots"
# ---------------------------------------------------------------------------
for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
  systemctl enable "$unit" >/dev/null 2>&1 && ok "$unit will start at boot" \
    || warn "Could not enable $unit at boot"
done
# Docker must come back on its own too, or nothing else can.
systemctl enable docker >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
say "5/5 Status"
# ---------------------------------------------------------------------------
AUTH_OK=0
if sudo -u "$AICOMPANY_USER" -H bash -lc 'claude auth status 2>/dev/null' | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
  AUTH_OK=1
fi

WORKER_UP=0
systemctl is-active --quiet rabit-claude-worker.service && WORKER_UP=1

echo ""
echo "======================================================================"
echo "  Dashboard:  http://localhost"
echo "              http://localhost:18080   (direct, if port 80 is taken)"
echo "  Source:     ${SRC}"
echo "  Installed:  ${APP_ROOT}"
echo "======================================================================"
echo ""

if [[ "$AUTH_OK" -eq 1 && "$WORKER_UP" -eq 1 ]]; then
  ok "The company is RUNNING. Open the dashboard and send a command."
  echo ""
  echo "  Prove it end to end:   sudo bash ${SRC}/scripts/prove-it-works.sh"
  echo "  If anything stalls:    sudo bash ${SRC}/scripts/worker-doctor.sh"
  exit 0
fi

if [[ "$AUTH_OK" -eq 0 ]]; then
  warn "ONE MANUAL STEP LEFT - the agents cannot run until the CLI is logged in."
  echo ""
  echo "  Run this, follow the link it prints, then you are done:"
  echo ""
  echo "      sudo -u ${AICOMPANY_USER} -H claude auth login"
  echo "      sudo systemctl restart rabit-claude-worker.service"
  echo ""
  echo "  (This cannot be scripted: it opens an interactive browser login, and"
  echo "   the session belongs to the ${AICOMPANY_USER} user the worker runs as.)"
fi

if [[ "$WORKER_UP" -eq 0 && "$AUTH_OK" -eq 1 ]]; then
  bad "The worker is not running. Diagnose it with:"
  echo "      sudo bash ${SRC}/scripts/worker-doctor.sh"
fi

echo ""
echo "  Then open:  http://localhost"
exit 0
