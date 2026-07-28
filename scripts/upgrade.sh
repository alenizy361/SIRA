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

TARGET_REF="${1:-${TARGET_REF:-origin/main}}"

[[ -d "$APP_ROOT/.git" ]] || die "${APP_ROOT} is not a git checkout — cannot upgrade. Run scripts/install.sh first."

log_step "Upgrading Rabit AI Company OS to '${TARGET_REF}'"
read -r OLD_SHA OLD_BRANCH < <(current_git_ref "$APP_ROOT")
log_info "Current state: ${OLD_SHA} on ${OLD_BRANCH}"

log_step "1/6 — Pre-upgrade backup"
BACKUP_DIR="$("$SCRIPT_DIR/backup.sh" --label "pre-upgrade-${OLD_SHA:0:12}")"
log_ok "Backup created at ${BACKUP_DIR}"

log_step "2/6 — Fetching and checking out ${TARGET_REF}"
git -C "$APP_ROOT" fetch --all --tags --prune
if ! git -C "$APP_ROOT" checkout "$TARGET_REF" 2>/tmp/.upgrade_checkout_err; then
  cat /tmp/.upgrade_checkout_err >&2
  rm -f /tmp/.upgrade_checkout_err
  die "git checkout '${TARGET_REF}' failed. Repository left at ${OLD_SHA} on ${OLD_BRANCH}."
fi
rm -f /tmp/.upgrade_checkout_err
read -r NEW_SHA NEW_BRANCH < <(current_git_ref "$APP_ROOT")
log_ok "Checked out ${NEW_SHA} (${NEW_BRANCH:-detached})"

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
