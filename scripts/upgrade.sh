#!/usr/bin/env bash
#
# scripts/upgrade.sh — upgrade Rabit AI Company OS to a new git ref.
#
# Flow: backup -> git checkout target ref -> rebuild containers -> run new
# Alembic migrations -> restart services -> smoke test. On smoke-test
# failure, automatically invokes rollback.sh against the pre-upgrade backup
# and exits non-zero.
#
# Usage:
#   sudo scripts/upgrade.sh [target-ref]
#   sudo TARGET_REF=v1.4.0 scripts/upgrade.sh
#
# target-ref defaults to TARGET_REF env var, then to "origin/main".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# Load real config from .env (if this install has one yet) so DB/Redis
# credentials and derived defaults match the actual deployment, not just
# this library's hardcoded fallbacks.
load_env_file "$APP_ROOT/.env"
sync_env_defaults

require_root "$@"

TARGET_REF="${1:-${TARGET_REF:-}}"

# APP_ROOT is produced by install.sh's rsync, which excludes .git - so it is
# almost never a git checkout. Resolve the REAL source clone: RABIT_CLONE, or
# the `source=` path recorded in APP_ROOT/BUILD_INFO. Upgrade fetches/checks
# out there, then rsyncs into APP_ROOT (like install). Only the pre-git legacy
# layout (APP_ROOT itself is a clone) upgrades in place.
if [[ -d "$APP_ROOT/.git" ]]; then
  CLONE="$APP_ROOT"
else
  CLONE="${RABIT_CLONE:-}"
  if [[ -z "$CLONE" && -f "$APP_ROOT/BUILD_INFO" ]]; then
    CLONE="$(sed -n 's/^source=//p' "$APP_ROOT/BUILD_INFO" | head -1)"
  fi
  [[ -n "$CLONE" && -d "$CLONE/.git" ]] || die \
    "No source git clone found (checked \$RABIT_CLONE and ${APP_ROOT}/BUILD_INFO 'source='). ${APP_ROOT} has no .git of its own. Re-clone the repo and run scripts/redeploy.sh, or set RABIT_CLONE=/path/to/clone."
fi

# Default the target to the clone's current upstream branch, not origin/main
# (which on this repo is only the initial README commit).
if [[ -z "$TARGET_REF" ]]; then
  _cur_branch="$(git -C "$CLONE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  TARGET_REF="origin/${_cur_branch}"
fi

log_step "Upgrading Rabit AI Company OS to '${TARGET_REF}' (source clone: ${CLONE})"
read -r OLD_SHA OLD_BRANCH < <(current_git_ref "$CLONE")
log_info "Current state: ${OLD_SHA} on ${OLD_BRANCH}"

log_step "1/6 — Pre-upgrade backup"
BACKUP_DIR="$("$SCRIPT_DIR/backup.sh" --label "pre-upgrade-${OLD_SHA:0:12}")"
log_ok "Backup created at ${BACKUP_DIR}"

fail_and_rollback() {
  local reason="$1"
  log_error "Upgrade failed: ${reason}"
  log_step "Auto-rolling back to pre-upgrade state (${OLD_SHA})"
  if "$SCRIPT_DIR/rollback.sh" --backup "$BACKUP_DIR" --yes; then
    log_error "Upgrade to '${TARGET_REF}' FAILED and was automatically rolled back to ${OLD_SHA}. Reason: ${reason}"
  else
    log_error "Upgrade FAILED and automatic rollback ALSO FAILED. Manual intervention required. Pre-upgrade backup: ${BACKUP_DIR}"
  fi
  exit 1
}

log_step "2/6 — Fetching and checking out ${TARGET_REF}"
git -C "$CLONE" fetch --all --tags --prune
if ! git -C "$CLONE" checkout "$TARGET_REF" 2>/tmp/.upgrade_checkout_err; then
  cat /tmp/.upgrade_checkout_err >&2
  rm -f /tmp/.upgrade_checkout_err
  die "git checkout '${TARGET_REF}' failed. Repository left at ${OLD_SHA} on ${OLD_BRANCH}."
fi
rm -f /tmp/.upgrade_checkout_err
git -C "$CLONE" pull --ff-only origin "$(git -C "$CLONE" rev-parse --abbrev-ref HEAD)" 2>/dev/null || true
read -r NEW_SHA NEW_BRANCH < <(current_git_ref "$CLONE")
log_ok "Checked out ${NEW_SHA} (${NEW_BRANCH:-detached})"

# If we upgraded a separate clone, sync it into APP_ROOT (same excludes as
# install.sh) so the containers build from the new code.
if [[ "$CLONE" != "$APP_ROOT" ]]; then
  log_step "2b/6 — Syncing ${CLONE} -> ${APP_ROOT}"
  rsync -a --delete \
    --exclude '.git' --exclude '.env' --exclude 'backups' \
    --exclude 'node_modules' --exclude '.venv' --exclude '__pycache__' --exclude 'workspace' \
    "$CLONE"/ "$APP_ROOT"/ || fail_and_rollback "rsync of new code into ${APP_ROOT} failed"
fi

log_step "3/6 — Rebuilding and pulling containers"
compose pull || fail_and_rollback "docker compose pull failed"
compose build --pull api web || fail_and_rollback "docker compose build failed"
compose up -d postgres redis || fail_and_rollback "failed to (re)start postgres/redis"
wait_for_container_healthy postgres 120 || fail_and_rollback "postgres did not become healthy after upgrade"
wait_for_container_healthy redis 60 || fail_and_rollback "redis did not become healthy after upgrade"

log_step "4/6 — Running new Alembic migrations"
compose run --rm api alembic upgrade head || fail_and_rollback "alembic migration failed"

log_step "5/6 — Restarting application services"
if is_systemd_pid1; then
  systemctl daemon-reload
  for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
    if [[ -f "/etc/systemd/system/${unit}" ]]; then
      systemctl restart "$unit" || fail_and_rollback "failed to restart ${unit}"
    fi
  done
else
  compose up -d api web || fail_and_rollback "failed to (re)start api/web via docker compose"
fi

log_step "6/6 — Smoke testing"
if ! "$SCRIPT_DIR/smoke-test.sh"; then
  fail_and_rollback "smoke-test.sh reported failures against the upgraded stack"
fi

log_ok "Upgrade to '${TARGET_REF}' (${NEW_SHA}) succeeded and passed smoke tests."
echo "Upgraded ${OLD_SHA} -> ${NEW_SHA} (${TARGET_REF}). Pre-upgrade backup kept at: ${BACKUP_DIR}"
