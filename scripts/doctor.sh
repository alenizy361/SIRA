#!/usr/bin/env bash
#
# scripts/doctor.sh — read-only diagnostics for Rabit AI Company OS.
#
# Never mutates system state. Prints one PASS/FAIL/WARN line per check with
# a one-line actionable fix suggestion on FAIL. Exit code reflects overall
# health: 0 if every check is PASS or WARN, 1 if any check is FAIL.
#
# Usage: scripts/doctor.sh
set -uo pipefail
# NOTE: deliberately not `set -e` — a diagnostic script must keep running
# every check even after one fails, so it can report the full picture.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# Load real config from .env (if this install has one yet) so DB/Redis
# credentials and derived defaults match the actual deployment, not just
# this library's hardcoded fallbacks.
load_env_file "$APP_ROOT/.env"
sync_env_defaults

FAIL_COUNT=0
WARN_COUNT=0
PASS_COUNT=0

# result <PASS|FAIL|WARN> <check-name> <detail> [fix-suggestion]
result() {
  local status="$1" name="$2" detail="$3" fix="${4:-}"
  local color
  case "$status" in
    PASS) color="$C_GRN"; PASS_COUNT=$((PASS_COUNT + 1)) ;;
    WARN) color="$C_YLW"; WARN_COUNT=$((WARN_COUNT + 1)) ;;
    FAIL) color="$C_RED"; FAIL_COUNT=$((FAIL_COUNT + 1)) ;;
    *) color="$C_RST" ;;
  esac
  printf '%s[%-4s]%s %-38s %s\n' "$color" "$status" "$C_RST" "$name" "$detail"
  if [[ "$status" == "FAIL" && -n "$fix" ]]; then
    printf '        %sfix:%s %s\n' "$C_DIM" "$C_RST" "$fix"
  fi
}

echo "Rabit AI Company OS — doctor"
echo "============================"
echo ""

# --- OS version --------------------------------------------------------
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" == "ubuntu" && ( "${VERSION_ID:-}" == "22.04" || "${VERSION_ID:-}" == "24.04" ) ]]; then
    result PASS "OS version" "Ubuntu ${VERSION_ID}"
  else
    result WARN "OS version" "${PRETTY_NAME:-unknown} (installer targets Ubuntu 22.04/24.04)"
  fi
else
  result WARN "OS version" "/etc/os-release not found"
fi

# --- CPU / RAM / disk ----------------------------------------------------
cpus="$(cpu_count)"; mem="$(mem_total_mb)"; disk="$(disk_free_gb /)"
if [[ "$cpus" -ge 2 ]]; then result PASS "CPU count" "${cpus} vCPU(s)"; else result WARN "CPU count" "${cpus} vCPU (recommended >= 2)"; fi
if [[ "$mem" -ge 2048 ]]; then result PASS "Memory" "${mem} MB"; else result WARN "Memory" "${mem} MB (recommended >= 2048MB)"; fi
if [[ "$disk" -ge 10 ]]; then result PASS "Disk free (/)" "${disk} GB"; else result WARN "Disk free (/)" "${disk} GB (recommended >= 10GB)" ; fi

for mount in "$APP_ROOT" "$BACKUP_ROOT"; do
  if [[ -d "$mount" ]]; then
    free_gb="$(disk_free_gb "$mount")"
    if [[ "$free_gb" -ge 5 ]]; then
      result PASS "Disk free (${mount})" "${free_gb} GB"
    else
      result WARN "Disk free (${mount})" "${free_gb} GB — getting low" "Free up space or grow the volume backing ${mount}"
    fi
  fi
done

# --- Docker ----------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    result PASS "Docker daemon" "reachable ($(docker --version))"
  else
    result FAIL "Docker daemon" "installed but not reachable" "Start it: sudo systemctl start docker (or: sudo service docker start)"
  fi
else
  result FAIL "Docker" "not installed" "Run scripts/install.sh, or: sudo apt-get install -y docker.io docker-compose-plugin"
fi

# --- docker compose services ------------------------------------------------
if [[ -f "$COMPOSE_FILE" ]] && command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  for svc in postgres redis; do
    cid="$(compose_soft ps -q "$svc" 2>/dev/null || true)"
    if [[ -n "$cid" ]]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo unknown)"
      if [[ "$status" == "healthy" || "$status" == "running" ]]; then
        result PASS "compose service: ${svc}" "${status}"
      else
        result FAIL "compose service: ${svc}" "${status}" "cd ${APP_ROOT} && docker compose logs ${svc}"
      fi
    else
      result WARN "compose service: ${svc}" "not running" "cd ${APP_ROOT} && docker compose up -d ${svc}"
    fi
  done
else
  result WARN "docker compose services" "skipped (compose file or docker daemon unavailable)"
fi

# --- Postgres / Redis reachability -----------------------------------------
if command -v pg_isready >/dev/null 2>&1 && pg_isready -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" -t 5 >/dev/null 2>&1; then
  result PASS "Postgres reachability" "${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432}"
elif [[ -f "$COMPOSE_FILE" ]] && [[ -n "$(compose_soft ps -q postgres 2>/dev/null)" ]] && compose_soft exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" >/dev/null 2>&1; then
  result PASS "Postgres reachability" "reachable inside docker compose"
else
  result FAIL "Postgres reachability" "not reachable" "Check 'docker compose ps postgres' and 'docker compose logs postgres'"
fi

if command -v redis-cli >/dev/null 2>&1 && [[ "$(redis-cli -h "${REDIS_HOST:-localhost}" -p "${REDIS_PORT:-6379}" --no-auth-warning -a "${REDIS_PASSWORD:-}" PING 2>/dev/null)" == "PONG" ]]; then
  result PASS "Redis reachability" "${REDIS_HOST:-localhost}:${REDIS_PORT:-6379}"
elif [[ -f "$COMPOSE_FILE" ]] && [[ "$(compose_soft exec -T redis redis-cli PING 2>/dev/null)" == "PONG" ]]; then
  result PASS "Redis reachability" "reachable inside docker compose"
else
  result FAIL "Redis reachability" "not reachable" "Check 'docker compose ps redis' and 'docker compose logs redis'"
fi

# --- aicompany user ----------------------------------------------------
if id "$AICOMPANY_USER" >/dev/null 2>&1; then
  result PASS "'${AICOMPANY_USER}' OS user" "exists (uid $(id -u "$AICOMPANY_USER"))"
else
  result FAIL "'${AICOMPANY_USER}' OS user" "does not exist" "Run scripts/install.sh to create it, or: sudo useradd -r -m -s /bin/bash ${AICOMPANY_USER}"
fi

# --- claude CLI + auth status ------------------------------------------
check_claude_as() {
  local run_as="$1"
  local claude_bin claude_ver
  if [[ "$run_as" == "__current__" ]]; then
    claude_bin="$(command -v claude 2>/dev/null || true)"
  else
    claude_bin="$(sudo -u "$run_as" -H bash -lc 'command -v claude' 2>/dev/null || true)"
  fi

  if [[ -z "$claude_bin" ]]; then
    result FAIL "claude CLI (as ${run_as})" "not found on PATH" "Install the Claude Code CLI for this user (see Anthropic docs), then: sudo -u ${run_as} claude auth login"
    return
  fi

  if [[ "$run_as" == "__current__" ]]; then
    claude_ver="$(claude --version 2>/dev/null || echo unknown)"
  else
    claude_ver="$(sudo -u "$run_as" -H bash -lc 'claude --version' 2>/dev/null || echo unknown)"
  fi
  result PASS "claude CLI (as ${run_as})" "${claude_bin} (${claude_ver})"

  local auth_output rc
  if [[ "$run_as" == "__current__" ]]; then
    auth_output="$(claude auth status 2>&1)"; rc=$?
  else
    auth_output="$(sudo -u "$run_as" -H bash -lc 'claude auth status' 2>&1)"; rc=$?
  fi

  # `claude auth status` emits JSON ({"loggedIn": true, ...}); match that first
  # so an authenticated host is not perpetually reported UNHEALTHY.
  if [[ $rc -eq 0 ]] && { echo "$auth_output" | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true' \
       || echo "$auth_output" | grep -qi 'logged in\|authenticated'; }; then
    result PASS "claude auth status (as ${run_as})" "authenticated"
  else
    result FAIL "claude auth status (as ${run_as})" "not authenticated (or unsupported CLI subcommand)" "sudo -u ${run_as} -H claude auth login"
  fi
}

if id "$AICOMPANY_USER" >/dev/null 2>&1; then
  check_claude_as "$AICOMPANY_USER"
else
  check_claude_as "__current__"
fi

# --- nginx -------------------------------------------------------------
if command -v nginx >/dev/null 2>&1; then
  if nginx -t >/tmp/.doctor_nginx_t 2>&1; then
    result PASS "nginx config" "valid ('nginx -t' passed)"
  else
    result FAIL "nginx config" "invalid" "Review: $(tail -n1 /tmp/.doctor_nginx_t)"
  fi
  rm -f /tmp/.doctor_nginx_t
else
  result WARN "nginx" "not installed"
fi

# --- systemd units -------------------------------------------------------
if is_systemd_pid1; then
  for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
    if [[ ! -f "/etc/systemd/system/${unit}" ]]; then
      result WARN "systemd: ${unit}" "unit file not installed" "Run scripts/install.sh to install it"
      continue
    fi
    if systemctl is-active --quiet "$unit"; then
      result PASS "systemd: ${unit}" "active"
    else
      state="$(systemctl is-active "$unit" 2>/dev/null || echo unknown)"
      result FAIL "systemd: ${unit}" "${state}" "sudo systemctl status ${unit} --no-pager; sudo journalctl -u ${unit} -n 50"
    fi
  done
else
  result WARN "systemd units" "skipped — systemd is not PID 1 on this host"
fi

# --- summary -------------------------------------------------------------
echo ""
echo "----------------------------------------"
printf 'PASS=%d  WARN=%d  FAIL=%d\n' "$PASS_COUNT" "$WARN_COUNT" "$FAIL_COUNT"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  echo "Overall: UNHEALTHY"
  exit 1
else
  echo "Overall: HEALTHY"
  exit 0
fi
