#!/usr/bin/env bash
#
# scripts/rollback.sh — restore Rabit AI Company OS to a previous backup.
#
# Restores the most recent backup (BACKUP_ROOT/latest) unless --backup is
# given. Stops services, restores the Postgres dump, checks out the git
# commit recorded in the backup's manifest.txt, restarts services, runs
# smoke-test.sh, and reports PASS/FAIL.
#
# Usage:
#   sudo scripts/rollback.sh [--backup PATH_OR_TIMESTAMP] [--yes]
#
# Called automatically by upgrade.sh on smoke-test failure.
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

BACKUP_ARG=""
ASSUME_YES=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) BACKUP_ARG="${2:-}"; shift 2 ;;
    --backup=*) BACKUP_ARG="${1#*=}"; shift ;;
    --yes) ASSUME_YES=true; shift ;;
    -h|--help) echo "Usage: $0 [--backup PATH_OR_TIMESTAMP] [--yes]"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

BACKUP_DIR="$(resolve_backup_dir "$BACKUP_ARG")" || die "Could not resolve a backup directory for '${BACKUP_ARG:-<latest>}' under ${BACKUP_ROOT}."
validate_backup_dir "$BACKUP_DIR" || die "Refusing to roll back to an invalid backup: ${BACKUP_DIR}"

# shellcheck disable=SC1090
TARGET_SHA="$(grep -E '^git_commit_sha=' "$BACKUP_DIR/manifest.txt" | cut -d= -f2-)"
TARGET_BRANCH="$(grep -E '^git_branch=' "$BACKUP_DIR/manifest.txt" | cut -d= -f2-)"

log_step "Rollback plan"
log_info "Backup:        ${BACKUP_DIR}"
log_info "Target commit: ${TARGET_SHA:-unknown} (was on branch ${TARGET_BRANCH:-unknown})"
log_info "DB dump:       $( [[ -f "$BACKUP_DIR/postgres_${DB_NAME}.dump" ]] && echo present || echo 'NOT PRESENT — DB will not be restored' )"

if ! confirm "This will stop services, restore the database from this backup, and check out ${TARGET_SHA:-an older commit}. Continue?" "$ASSUME_YES"; then
  log_info "Rollback cancelled by operator."
  exit 1
fi

log_step "1/5 — Stopping services"
if is_systemd_pid1; then
  for unit in rabit-claude-worker.service rabit-web.service rabit-api.service; do
    systemctl stop "$unit" 2>/dev/null || true
  done
else
  compose stop api web 2>/dev/null || true
fi

log_step "2/5 — Restoring database"
if [[ -f "$BACKUP_DIR/postgres_${DB_NAME}.dump" ]]; then
  compose up -d postgres
  wait_for_container_healthy postgres 90 || die "postgres did not become healthy — cannot restore DB."
  compose cp "$BACKUP_DIR/postgres_${DB_NAME}.dump" "postgres:/tmp/rollback_restore.dump"
  if compose exec -T postgres pg_restore -U "${POSTGRES_USER:-postgres}" -d "$DB_NAME" --clean --if-exists /tmp/rollback_restore.dump; then
    log_ok "Database restored from ${BACKUP_DIR}/postgres_${DB_NAME}.dump"
  else
    log_warn "pg_restore reported errors (often harmless 'does not exist' notices on --clean). Verify with scripts/doctor.sh."
  fi
  compose exec -T postgres rm -f /tmp/rollback_restore.dump || true
else
  log_warn "No Postgres dump in this backup — skipping DB restore (code/config-only rollback)."
fi

log_step "3/5 — Restoring code to ${TARGET_SHA:-<unknown>}"
# APP_ROOT is produced by install.sh's rsync (no .git), so revert via the real
# source clone: RABIT_CLONE or BUILD_INFO's source= path. Check out the target
# commit there, then rsync into APP_ROOT so the rebuilt images run the OLD code
# to match the restored OLD database.
_CLONE=""
if git -C "$APP_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  _CLONE="$APP_ROOT"
else
  _CLONE="${RABIT_CLONE:-}"
  [[ -z "$_CLONE" && -f "$APP_ROOT/BUILD_INFO" ]] && _CLONE="$(sed -n 's/^source=//p' "$APP_ROOT/BUILD_INFO" | head -1)"
fi
CODE_REVERTED=false
if [[ -n "${TARGET_SHA:-}" && "$TARGET_SHA" != "unknown" && -n "$_CLONE" && -d "$_CLONE/.git" ]]; then
  if git -C "$_CLONE" checkout "$TARGET_SHA" 2>/dev/null; then
    if [[ "$_CLONE" != "$APP_ROOT" ]]; then
      rsync -a --delete \
        --exclude '.git' --exclude '.env' --exclude 'backups' \
        --exclude 'node_modules' --exclude '.venv' --exclude '__pycache__' --exclude 'workspace' \
        "$_CLONE"/ "$APP_ROOT"/
    fi
    CODE_REVERTED=true
    log_ok "Code reverted to ${TARGET_SHA} (source clone: ${_CLONE})"
  else
    log_warn "Could not checkout ${TARGET_SHA} in ${_CLONE}."
  fi
fi
if [[ "$CODE_REVERTED" != "true" ]]; then
  log_warn "CODE WAS NOT REVERTED (no usable git clone / commit). The database was rolled back but the app code is unchanged - they may mismatch. To revert the code, run scripts/redeploy.sh with the target commit checked out in your clone."
fi

log_step "3b/5 — Rebuilding images from the restored code"
# Without this the containers keep the previously-built (new-code) image even
# after the code and DB were reverted.
compose build api web || log_warn "image rebuild failed — containers may still run the previous image."

if [[ -f "$BACKUP_DIR/.env" ]]; then
  cp -p "$APP_ROOT/.env" "$APP_ROOT/.env.pre-rollback.bak" 2>/dev/null || true
  cp -p "$BACKUP_DIR/.env" "$APP_ROOT/.env"
  chmod 600 "$APP_ROOT/.env"
  log_ok "Restored .env from backup (previous .env saved as .env.pre-rollback.bak)"
fi

log_step "4/5 — Restarting services"
# Force-recreate so the freshly (re)built image actually replaces the running
# container rather than the old one being restarted in place.
compose up -d --force-recreate postgres redis api web || log_warn "compose recreate reported an error."
if is_systemd_pid1; then
  systemctl daemon-reload
  for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
    [[ -f "/etc/systemd/system/${unit}" ]] && systemctl restart "$unit" 2>/dev/null || true
  done
fi

log_step "5/5 — Smoke testing rolled-back stack"
if "$SCRIPT_DIR/smoke-test.sh"; then
  log_ok "Rollback to ${TARGET_SHA:-previous state} PASSED smoke tests."
  echo "ROLLBACK: PASS (restored ${BACKUP_DIR})"
  exit 0
else
  log_error "Rollback to ${TARGET_SHA:-previous state} completed but smoke tests still FAIL. Manual investigation required — run scripts/doctor.sh."
  echo "ROLLBACK: FAIL (restored ${BACKUP_DIR}, but smoke-test.sh failed)"
  exit 1
fi
