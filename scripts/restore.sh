#!/usr/bin/env bash
#
# scripts/restore.sh — manually restore a specific backup created by
# backup.sh (Postgres dump + .env/config). This is the general-purpose
# "put this backup back" tool; for "the last upgrade broke prod, revert
# code+data+services and verify" use scripts/rollback.sh instead.
#
# Usage:
#   scripts/restore.sh <backup-path-or-timestamp> [--yes]
#
# Requires either --yes or an interactive y/N confirmation before touching
# anything. Validates the backup directory before proceeding.
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

ASSUME_YES=false
BACKUP_ARG=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=true; shift ;;
    -h|--help) echo "Usage: $0 <backup-path-or-timestamp> [--yes]"; exit 0 ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done
BACKUP_ARG="${POSITIONAL[0]:-}"

[[ -n "$BACKUP_ARG" ]] || die "Usage: $0 <backup-path-or-timestamp> [--yes]  (list backups with: ls ${BACKUP_ROOT})"

BACKUP_DIR="$(resolve_backup_dir "$BACKUP_ARG")" || die "No backup found for '${BACKUP_ARG}' (looked at that literal path and under ${BACKUP_ROOT})."
validate_backup_dir "$BACKUP_DIR" || die "Refusing to restore from an invalid backup directory: ${BACKUP_DIR}"

log_step "Restore plan"
log_info "Source backup: ${BACKUP_DIR}"
[[ -f "$BACKUP_DIR/manifest.txt" ]] && sed 's/^/  manifest: /' "$BACKUP_DIR/manifest.txt" >&2
HAS_DB=false;  [[ -f "$BACKUP_DIR/postgres_${DB_NAME}.dump" ]] && HAS_DB=true
HAS_ENV=false; [[ -f "$BACKUP_DIR/.env" ]] && HAS_ENV=true
HAS_NGINX=false; [[ -d "$BACKUP_DIR/nginx" ]] && [[ -n "$(ls -A "$BACKUP_DIR/nginx" 2>/dev/null)" ]] && HAS_NGINX=true
HAS_SYSTEMD=false; [[ -d "$BACKUP_DIR/systemd" ]] && [[ -n "$(ls -A "$BACKUP_DIR/systemd" 2>/dev/null)" ]] && HAS_SYSTEMD=true

log_info "Will restore: db=${HAS_DB} env=${HAS_ENV} nginx_config=${HAS_NGINX} systemd_units=${HAS_SYSTEMD}"

if ! confirm "Restore the above into the running system at ${APP_ROOT}? This overwrites current .env/config and the '${DB_NAME}' database." "$ASSUME_YES"; then
  log_info "Restore cancelled by operator."
  exit 1
fi

CHANGES=()

if [[ "$HAS_DB" == "true" ]]; then
  log_step "Restoring Postgres database '${DB_NAME}'"
  # Stop the app + worker FIRST so nothing reads/writes the target DB while
  # pg_restore --clean drops and recreates tables (otherwise DROPs interleave
  # with live queries and leave a half-restored, inconsistent database).
  if is_systemd_pid1; then
    for unit in rabit-claude-worker.service rabit-web.service rabit-api.service; do
      systemctl stop "$unit" 2>/dev/null || true
    done
  else
    compose stop api web 2>/dev/null || true
  fi

  compose up -d postgres
  wait_for_container_healthy postgres 90 || die "postgres did not become healthy — aborting before touching data."
  # Terminate any lingering connections to the target DB so DROPs don't block.
  compose exec -T postgres psql -U "${POSTGRES_USER:-postgres}" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true
  compose cp "$BACKUP_DIR/postgres_${DB_NAME}.dump" "postgres:/tmp/manual_restore.dump"
  if compose exec -T postgres pg_restore -U "${POSTGRES_USER:-postgres}" -d "$DB_NAME" --clean --if-exists /tmp/manual_restore.dump; then
    log_ok "Database restored"
    CHANGES+=("Postgres database '${DB_NAME}' restored from ${BACKUP_DIR}/postgres_${DB_NAME}.dump")
    compose exec -T postgres rm -f /tmp/manual_restore.dump || true
  else
    # --if-exists already suppresses the benign 'does not exist' notices, so a
    # non-zero exit here is a REAL failure - do not report success on a
    # possibly half-restored database with services about to restart onto it.
    compose exec -T postgres rm -f /tmp/manual_restore.dump || true
    die "pg_restore FAILED for '${DB_NAME}'. The database may be partially restored. Do NOT restart services against it - investigate the dump, then re-run restore."
  fi
  # Bring the app back up now that the DB is consistent.
  if is_systemd_pid1; then
    for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
      [[ -f "/etc/systemd/system/${unit}" ]] && systemctl start "$unit" 2>/dev/null || true
    done
  else
    compose up -d api web 2>/dev/null || true
  fi
else
  log_warn "No Postgres dump in this backup — DB left untouched."
fi

if [[ "$HAS_ENV" == "true" ]]; then
  if [[ -f "$APP_ROOT/.env" ]]; then
    cp -p "$APP_ROOT/.env" "$APP_ROOT/.env.pre-restore.bak"
    CHANGES+=("Previous .env saved as ${APP_ROOT}/.env.pre-restore.bak")
  fi
  cp -p "$BACKUP_DIR/.env" "$APP_ROOT/.env"
  chmod 600 "$APP_ROOT/.env"
  CHANGES+=(".env restored from backup (mode 600)")
  log_ok ".env restored"
else
  log_warn "No .env in this backup — current .env left untouched."
fi

if [[ "$HAS_NGINX" == "true" ]] && command -v nginx >/dev/null 2>&1; then
  if [[ -f "$BACKUP_DIR/nginx/rabit-os.conf" ]]; then
    cp -p /etc/nginx/sites-available/rabit-os.conf /etc/nginx/sites-available/rabit-os.conf.pre-restore.bak 2>/dev/null || true
    cp -p "$BACKUP_DIR/nginx/rabit-os.conf" /etc/nginx/sites-available/rabit-os.conf
    if nginx -t; then
      systemctl reload nginx 2>/dev/null || service nginx reload 2>/dev/null || true
      CHANGES+=("Nginx config restored to /etc/nginx/sites-available/rabit-os.conf and reloaded")
      log_ok "Nginx config restored and reloaded"
    else
      log_error "Restored nginx config failed 'nginx -t' — reverting to the pre-restore copy."
      cp -p /etc/nginx/sites-available/rabit-os.conf.pre-restore.bak /etc/nginx/sites-available/rabit-os.conf 2>/dev/null || true
    fi
  fi
fi

if [[ "$HAS_SYSTEMD" == "true" ]] && is_systemd_pid1; then
  for f in "$BACKUP_DIR"/systemd/*; do
    [[ -f "$f" ]] || continue
    cp -p "$f" "/etc/systemd/system/$(basename "$f")"
    CHANGES+=("systemd unit $(basename "$f") restored")
  done
  systemctl daemon-reload
  log_ok "systemd unit files restored and daemon reloaded (services not automatically restarted — do that explicitly if desired)"
fi

log_step "Restore complete — summary of changes"
if [[ "${#CHANGES[@]}" -eq 0 ]]; then
  echo "No changes were made (backup contained nothing restorable)."
else
  for c in "${CHANGES[@]}"; do
    echo "  - ${c}"
  done
fi
echo ""
echo "Recommended next step: scripts/doctor.sh (and scripts/smoke-test.sh if services are running)."
