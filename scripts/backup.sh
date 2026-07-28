#!/usr/bin/env bash
#
# scripts/backup.sh — timestamped backup of the Rabit AI Company OS install.
#
# Captures: Postgres dump, Redis RDB snapshot (if present), .env (mode 600,
# never staged into git), the active Nginx config, the active systemd unit
# files, and the current git commit SHA + branch.
#
# Usage:
#   scripts/backup.sh [--label some-note]
#
# Env overrides: APP_ROOT, BACKUP_ROOT, DB_NAME, COMPOSE_FILE
#
# Prints the backup directory path as the last line of stdout on success so
# other scripts (upgrade.sh, install.sh) can capture it with:
#   backup_dir="$(scripts/backup.sh)"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# Load real config from .env (if this install has one yet) so DB/Redis
# credentials and derived defaults match the actual deployment, not just
# this library's hardcoded fallbacks.
load_env_file "$APP_ROOT/.env"
sync_env_defaults

LABEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --label) LABEL="${2:-}"; shift 2 ;;
    --label=*) LABEL="${1#*=}"; shift ;;
    -h|--help) echo "Usage: $0 [--label NOTE]"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

TIMESTAMP="$(date -u '+%Y%m%d_%H%M%S')"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}${LABEL:+_${LABEL}}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log_step "Creating backup at ${BACKUP_DIR}"

# --- metadata -------------------------------------------------------------
{
  echo "backup_timestamp_utc=${TIMESTAMP}"
  echo "app_root=${APP_ROOT}"
  read -r sha branch < <(current_git_ref "$APP_ROOT")
  echo "git_commit_sha=${sha}"
  echo "git_branch=${branch}"
  echo "hostname=$(hostname 2>/dev/null || echo unknown)"
} > "$BACKUP_DIR/manifest.txt"
log_ok "Wrote manifest.txt (commit ${sha:-unknown} on ${branch:-unknown})"

# --- Postgres dump ----------------------------------------------------------
DB_DUMPED=false
if [[ -f "$COMPOSE_FILE" ]] && compose ps -q postgres >/dev/null 2>&1 && [[ -n "$(compose ps -q postgres 2>/dev/null)" ]]; then
  log_info "Dumping Postgres database '${DB_NAME}' via docker compose service 'postgres'..."
  if compose exec -T postgres pg_dump -U "${POSTGRES_USER:-postgres}" -Fc "$DB_NAME" > "$BACKUP_DIR/postgres_${DB_NAME}.dump" 2>"$BACKUP_DIR/.pg_dump.stderr"; then
    DB_DUMPED=true
    log_ok "Postgres dump written to postgres_${DB_NAME}.dump ($(du -h "$BACKUP_DIR/postgres_${DB_NAME}.dump" | cut -f1))"
  else
    log_error "pg_dump via docker compose failed. stderr:"
    cat "$BACKUP_DIR/.pg_dump.stderr" >&2 || true
    die "Aborting backup — refusing to report success with a missing DB dump."
  fi
elif command -v pg_dump >/dev/null 2>&1 && PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_isready -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" >/dev/null 2>&1; then
  log_info "Dumping Postgres database '${DB_NAME}' via local pg_dump..."
  PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" -U "${POSTGRES_USER:-postgres}" -Fc "$DB_NAME" > "$BACKUP_DIR/postgres_${DB_NAME}.dump"
  DB_DUMPED=true
  log_ok "Postgres dump written to postgres_${DB_NAME}.dump"
else
  log_warn "Postgres is not reachable (neither via docker compose nor locally) — skipping DB dump. This backup will NOT be restorable for data; re-run once Postgres is up."
fi
rm -f "$BACKUP_DIR/.pg_dump.stderr"

# --- Redis snapshot ---------------------------------------------------------
if [[ -f "$COMPOSE_FILE" ]] && [[ -n "$(compose ps -q redis 2>/dev/null)" ]]; then
  log_info "Triggering Redis BGSAVE and copying dump.rdb..."
  if compose exec -T redis redis-cli SAVE >/dev/null 2>&1; then
    if compose cp redis:/data/dump.rdb "$BACKUP_DIR/redis_dump.rdb" 2>/dev/null; then
      log_ok "Redis snapshot copied to redis_dump.rdb"
    else
      log_warn "Could not copy dump.rdb out of the redis container — skipping Redis snapshot."
    fi
  else
    log_warn "Redis SAVE failed or redis-cli unavailable — skipping Redis snapshot."
  fi
elif [[ -f /var/lib/redis/dump.rdb ]]; then
  cp -p /var/lib/redis/dump.rdb "$BACKUP_DIR/redis_dump.rdb"
  log_ok "Redis snapshot copied from /var/lib/redis/dump.rdb"
else
  log_warn "No reachable Redis instance found — skipping Redis snapshot (queues/pub-sub state is not durable by design; this is non-fatal)."
fi

# --- .env (permission-restricted, never in git) -----------------------------
if [[ -f "$APP_ROOT/.env" ]]; then
  cp -p "$APP_ROOT/.env" "$BACKUP_DIR/.env"
  chmod 600 "$BACKUP_DIR/.env"
  log_ok ".env copied with mode 600"
else
  log_warn "No .env found at ${APP_ROOT}/.env — skipping."
fi

# --- Active nginx config -----------------------------------------------------
if [[ -d /etc/nginx ]]; then
  mkdir -p "$BACKUP_DIR/nginx"
  if [[ -f /etc/nginx/sites-available/rabit-os.conf ]]; then
    cp -p /etc/nginx/sites-available/rabit-os.conf "$BACKUP_DIR/nginx/" 2>/dev/null || true
  fi
  if [[ -L /etc/nginx/sites-enabled/rabit-os.conf ]]; then
    { echo "symlink -> $(readlink -f /etc/nginx/sites-enabled/rabit-os.conf)"; } > "$BACKUP_DIR/nginx/sites-enabled.txt"
  fi
  log_ok "Captured active Nginx config for rabit-os (if present)"
else
  log_warn "Nginx not installed on this host — skipping Nginx config backup."
fi

# --- Active systemd unit files ----------------------------------------------
mkdir -p "$BACKUP_DIR/systemd"
found_units=false
for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
  if [[ -f "/etc/systemd/system/${unit}" ]]; then
    cp -p "/etc/systemd/system/${unit}" "$BACKUP_DIR/systemd/" 2>/dev/null || true
    found_units=true
  fi
done
if [[ "$found_units" == "true" ]]; then
  log_ok "Captured active systemd unit files for rabit-* services"
else
  log_warn "No rabit-* systemd unit files found under /etc/systemd/system — skipping."
fi

# --- finalize ----------------------------------------------------------------
chmod -R go-rwx "$BACKUP_DIR"
BACKUP_SIZE="$(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)"
log_step "Backup complete (${BACKUP_SIZE:-unknown size}), db_dumped=${DB_DUMPED}"

# Machine-readable summary for callers, human-readable path on the last line.
ln -sfn "$BACKUP_DIR" "${BACKUP_ROOT}/latest"

echo "$BACKUP_DIR"
