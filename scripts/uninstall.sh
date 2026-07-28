#!/usr/bin/env bash
#
# scripts/uninstall.sh — remove Rabit AI Company OS from this host.
#
# Stops and disables systemd units (if present), stops docker compose
# services, and removes the application directory. Backups are PRESERVED
# by default; pass --purge-backups to delete them too. Requires --yes.
#
# Usage:
#   sudo scripts/uninstall.sh --yes
#   sudo scripts/uninstall.sh --yes --purge-backups
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
PURGE_BACKUPS=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) ASSUME_YES=true; shift ;;
    --purge-backups) PURGE_BACKUPS=true; shift ;;
    -h|--help) echo "Usage: $0 --yes [--purge-backups]"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

if [[ "$ASSUME_YES" != "true" ]]; then
  if ! confirm "This will stop all Rabit AI Company OS services and remove ${APP_ROOT} (backups $( [[ "$PURGE_BACKUPS" == "true" ]] && echo WILL || echo will NOT ) be deleted). Continue?" "false"; then
    log_info "Uninstall cancelled by operator."
    exit 1
  fi
fi

log_step "1/4 — Stopping and disabling systemd units"
if is_systemd_pid1; then
  for unit in rabit-claude-worker.service rabit-web.service rabit-api.service; do
    if systemctl list-unit-files "$unit" >/dev/null 2>&1; then
      systemctl stop "$unit" 2>/dev/null || true
      systemctl disable "$unit" 2>/dev/null || true
      rm -f "/etc/systemd/system/${unit}"
      log_ok "Stopped, disabled, and removed unit: ${unit}"
    fi
  done
  systemctl daemon-reload
else
  log_warn "systemd not PID 1 — skipping systemd unit teardown (nothing to disable on this host)."
fi

log_step "2/4 — Stopping docker compose services"
if [[ -f "$COMPOSE_FILE" ]] && command -v docker >/dev/null 2>&1; then
  compose down || log_warn "docker compose down reported errors — continuing with directory removal."
  log_ok "docker compose services stopped"
else
  log_warn "No docker-compose.yml found at ${COMPOSE_FILE} (or docker unavailable) — skipping compose teardown."
fi

log_step "3/4 — Removing Nginx site config"
if [[ -f /etc/nginx/sites-enabled/rabit-os.conf || -L /etc/nginx/sites-enabled/rabit-os.conf ]]; then
  rm -f /etc/nginx/sites-enabled/rabit-os.conf
  rm -f /etc/nginx/sites-available/rabit-os.conf
  if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then
    systemctl reload nginx 2>/dev/null || service nginx reload 2>/dev/null || true
  fi
  log_ok "Removed Nginx site config for rabit-os"
else
  log_info "No Nginx site config found for rabit-os — nothing to remove."
fi

log_step "4/4 — Removing application directory"
BACKUPS_UNDER_APP_ROOT=false
case "$BACKUP_ROOT" in
  "$APP_ROOT"/*) BACKUPS_UNDER_APP_ROOT=true ;;
esac

if [[ ! -d "$APP_ROOT" ]]; then
  log_info "${APP_ROOT} does not exist — nothing to remove."
elif [[ "$PURGE_BACKUPS" == "true" ]]; then
  rm -rf "$APP_ROOT"
  [[ "$BACKUPS_UNDER_APP_ROOT" == "false" && -d "$BACKUP_ROOT" ]] && rm -rf "$BACKUP_ROOT"
  log_ok "Removed ${APP_ROOT} AND ${BACKUP_ROOT} (--purge-backups was given)"
else
  if [[ "$BACKUPS_UNDER_APP_ROOT" == "true" ]]; then
    log_info "Preserving backups at ${BACKUP_ROOT} — removing everything else under ${APP_ROOT}"
    find "$APP_ROOT" -mindepth 1 -maxdepth 1 ! -name "$(basename "$BACKUP_ROOT")" -exec rm -rf {} +
  else
    rm -rf "$APP_ROOT"
    log_info "Backups live outside ${APP_ROOT} at ${BACKUP_ROOT} — left untouched"
  fi
  log_ok "Removed application directory (backups preserved at ${BACKUP_ROOT})"
fi

echo ""
echo "Uninstall complete."
echo "  Backups preserved at: ${BACKUP_ROOT}$( [[ "$PURGE_BACKUPS" == "true" ]] && echo ' (DELETED — --purge-backups was given)')"
echo "  Note: the '${AICOMPANY_USER}' OS user, Docker, and installed apt packages (nginx, docker, node, etc.) are left in place — remove them manually if you're decommissioning the whole host, not just this app."
