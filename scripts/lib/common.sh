#!/usr/bin/env bash
# shellcheck shell=bash
#
# scripts/lib/common.sh — shared helpers for Rabit AI Company OS ops scripts.
#
# Sourced (never executed directly) by install.sh, upgrade.sh, rollback.sh,
# backup.sh, restore.sh, doctor.sh, smoke-test.sh, uninstall.sh.
#
# NOTE: this file is intentionally *not* one of the numbered deliverables in
# the build task, but every script below needs the same primitives (logging,
# root checks, docker-compose detection, systemd detection, .env loading,
# confirmation prompts). Duplicating ~150 lines eight times would be a worse
# and more error-prone deliverable than one shared, carefully written file.
# If the orchestrating session would rather inline this, every function here
# is small and self-contained.

# Guard against double-sourcing.
if [[ -n "${__RABIT_COMMON_SH_LOADED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
__RABIT_COMMON_SH_LOADED=1

# --- Paths -------------------------------------------------------------
# APP_ROOT is the canonical install location on a real VPS. It is where the
# repo is expected to be checked out and where docker-compose.yml,
# .env, infra/, etc. live at runtime. Overridable for testing.
: "${APP_ROOT:=/opt/rabit-ai-company-os}"
: "${BACKUP_ROOT:=${APP_ROOT}/backups}"
: "${AICOMPANY_USER:=aicompany}"
: "${DB_NAME:=rabit_os}"
# Host-published container ports - deliberately not 8000/3000 (see
# docker-compose.yml and infra/nginx/rabit-os.conf.template): the browser
# never talks to these directly, only Nginx and these local health checks
# do, so they're free to avoid whatever a VPS's other apps already use.
: "${API_PORT:=18081}"
: "${WEB_PORT:=18080}"
: "${API_URL:=http://localhost:${API_PORT}}"
: "${WEB_URL:=http://localhost:${WEB_PORT}}"
: "${COMPOSE_FILE:=${APP_ROOT}/docker-compose.yml}"

# --- Logging -------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'; C_GRN=$'\033[0;32m'; C_YLW=$'\033[0;33m'
  C_BLU=$'\033[0;34m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_DIM=""; C_RST=""
fi

_ts() { date '+%Y-%m-%d %H:%M:%S'; }

# All logging goes to stderr, on purpose: several scripts (backup.sh,
# smoke-test.sh) print a single machine-readable value (a path, a PASS/FAIL
# summary) to stdout as their real "return value" so callers can do
# `result="$(scripts/backup.sh)"` safely. Log noise must never land on stdout.
log_info()  { printf '%s [%sINFO %s] %s\n'  "$(_ts)" "$C_BLU" "$C_RST" "$*" >&2; }
log_ok()    { printf '%s [%s OK  %s] %s\n'  "$(_ts)" "$C_GRN" "$C_RST" "$*" >&2; }
log_warn()  { printf '%s [%sWARN %s] %s\n'  "$(_ts)" "$C_YLW" "$C_RST" "$*" >&2; }
log_error() { printf '%s [%sERROR%s] %s\n'  "$(_ts)" "$C_RED" "$C_RST" "$*" >&2; }
log_step()  { printf '\n%s==> %s%s\n' "$C_BLU" "$*" "$C_RST" >&2; }

die() { log_error "$*"; exit 1; }

# --- Privilege checks ------------------------------------------------------
require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "This script must be run as root (try: sudo $0 $*)."
  fi
}

# --- systemd detection -------------------------------------------------
# Guard every systemctl call with this — CI containers, dev boxes, and some
# Docker-only hosts do not run systemd as PID 1.
is_systemd_pid1() {
  [[ -d /run/systemd/system ]] && [[ "$(ps -p 1 -o comm= 2>/dev/null || true)" == "systemd" ]]
}

require_systemd_or_skip() {
  local action="$1"
  if ! is_systemd_pid1; then
    log_warn "systemd is not PID 1 on this host — skipping: ${action}. (This is expected in containers/dev sandboxes; systemd units only apply on a real VPS.)"
    return 1
  fi
  return 0
}

# --- docker compose detection -----------------------------------------
# Sets the global array COMPOSE_CMD (e.g. (docker compose) or (docker-compose)).
detect_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
  else
    die "Neither 'docker compose' (plugin) nor 'docker-compose' (standalone) was found."
  fi
}

compose() {
  detect_compose_cmd
  ( cd "$APP_ROOT" && "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" "$@" )
}

# compose_soft — like compose(), but never calls die()/exit on a missing
# compose command; returns 1 instead. Use this in read-only/diagnostic
# contexts (doctor.sh) that must keep running every check regardless of
# what's installed.
compose_soft() {
  local cmd=()
  if docker compose version >/dev/null 2>&1; then
    cmd=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    cmd=(docker-compose)
  else
    return 1
  fi
  ( cd "$APP_ROOT" && "${cmd[@]}" -f "$COMPOSE_FILE" "$@" )
}

# --- .env handling -------------------------------------------------------
# Loads KEY=VALUE pairs from an env file into the current shell's environment
# without executing arbitrary shell (safer than `source`). Ignores blank
# lines and comments.
load_env_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="$(echo -n "$key" | xargs)"
    # strip surrounding quotes if present
    value="${value%\"}"; value="${value#\"}"
    value="${value%\'}"; value="${value#\'}"
    [[ -z "$key" ]] && continue
    # Do NOT clobber a value the caller already set in the environment. The
    # installer's usage is `sudo DOMAIN=example.com scripts/install.sh`, but
    # the generated .env has an empty `DOMAIN=`; without this guard load_env_file
    # would export DOMAIN="" over the operator's value and silently skip TLS.
    if [[ -n "${!key:-}" ]]; then
      continue
    fi
    export "${key}=${value}"
  done < "$file"
}

# sync_env_defaults — call this immediately after load_env_file so derived
# defaults (DB_NAME) track whatever the real .env actually configured
# (e.g. POSTGRES_DB=rabit) instead of silently falling back to this file's
# hardcoded default. Every script that touches Postgres/Redis should do:
#   load_env_file "$APP_ROOT/.env"; sync_env_defaults
# near the top, before relying on DB_NAME/POSTGRES_*/REDIS_* values.
sync_env_defaults() {
  DB_NAME="${POSTGRES_DB:-$DB_NAME}"
  # API_URL/WEB_URL were defaulted from API_PORT/WEB_PORT at the top of this
  # file, before load_env_file (called after this point in every script) had
  # a chance to apply a customized port from .env - recompute them now so a
  # customized API_PORT/WEB_PORT in .env is actually honored, not silently
  # ignored in favor of the pre-.env default port.
  API_URL="http://localhost:${API_PORT}"
  WEB_URL="http://localhost:${WEB_PORT}"
}

# --- confirmation prompt ---------------------------------------------------
# Usage: confirm "Do the thing?" "${ASSUME_YES:-false}"
confirm() {
  local prompt="$1"
  local assume_yes="${2:-false}"
  if [[ "$assume_yes" == "true" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    log_error "Refusing to proceed without confirmation (non-interactive shell and no --yes given)."
    return 1
  fi
  local reply
  read -r -p "${prompt} [y/N] " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# --- resource checks -------------------------------------------------------
cpu_count()   { nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1; }
mem_total_mb() { awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0; }
disk_free_gb() { local path="${1:-/}"; df -Pk "$path" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}'; }

# --- simple TCP/HTTP wait helper -----------------------------------------
# wait_for_http URL timeout_seconds
wait_for_http() {
  local url="$1" timeout="${2:-60}" waited=0
  until curl -fsS -o /dev/null -m 3 "$url" 2>/dev/null; do
    waited=$((waited + 2))
    if [[ "$waited" -ge "$timeout" ]]; then
      return 1
    fi
    sleep 2
  done
  return 0
}

# wait_for_container_healthy <service-name> <timeout-seconds>
wait_for_container_healthy() {
  local service="$1" timeout="${2:-90}" waited=0 cid status
  while true; do
    cid="$(compose ps -q "$service" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
      if [[ "$status" == "healthy" || "$status" == "running" ]]; then
        return 0
      fi
    fi
    waited=$((waited + 3))
    if [[ "$waited" -ge "$timeout" ]]; then
      log_error "Timed out waiting for '${service}' to become healthy (last status: ${status:-none})."
      return 1
    fi
    sleep 3
  done
}

# --- backup directory resolution -----------------------------------------
# resolve_backup_dir <arg-or-empty>
# Accepts: an absolute path, a bare timestamp (YYYYmmdd_HHMMSS[_label]) under
# BACKUP_ROOT, or empty (falls back to BACKUP_ROOT/latest). Echoes the
# resolved absolute path, or returns non-zero with nothing on stdout.
resolve_backup_dir() {
  local arg="${1:-}"
  local candidate=""
  if [[ -z "$arg" ]]; then
    candidate="${BACKUP_ROOT}/latest"
  elif [[ "$arg" == /* ]]; then
    candidate="$arg"
  else
    candidate="${BACKUP_ROOT}/${arg}"
  fi

  if [[ -d "$candidate" ]]; then
    (cd "$candidate" && pwd -P)
    return 0
  fi
  return 1
}

# validate_backup_dir <dir> — checks the directory looks like a real backup
# produced by backup.sh (manifest present; at least one of the restorable
# artifacts present). Prints nothing; returns 0/1.
validate_backup_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || { log_error "Backup directory does not exist: ${dir}"; return 1; }
  [[ -f "$dir/manifest.txt" ]] || { log_error "Not a valid backup directory (missing manifest.txt): ${dir}"; return 1; }
  if [[ ! -f "$dir/postgres_${DB_NAME}.dump" && ! -f "$dir/.env" ]]; then
    log_error "Backup directory has neither a Postgres dump nor a .env copy — refusing to treat it as restorable: ${dir}"
    return 1
  fi
  return 0
}

# --- git helpers -----------------------------------------------------------
current_git_ref() {
  local dir="${1:-$APP_ROOT}"
  if git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "$(git -C "$dir" rev-parse HEAD 2>/dev/null) $(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  else
    echo "unknown unknown"
  fi
}
