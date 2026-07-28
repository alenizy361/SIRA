#!/usr/bin/env bash
#
# scripts/smoke-test.sh — post-deploy health verification.
#
# Checks (in order, exits non-zero with a clear message on the FIRST failure):
#   1. API   GET /health/live
#   2. API   GET /health/ready
#   3. API   GET /health/dependencies
#   4. Web   GET /  (root)
#   5. Postgres reachability (via pg_isready if available, else docker compose)
#   6. Redis reachability (via redis-cli PING if available, else docker compose)
#
# ASSUMPTION: health endpoints are served at API root (not under /api/) even
# though Nginx exposes the API under /api/ publicly — this script talks to
# the API directly on its host-published container port, bypassing Nginx,
# which is the normal pattern for local health checks.
#
# Env overrides: API_PORT / WEB_PORT (read from .env, default 18081/18080 -
# see docker-compose.yml and infra/nginx/rabit-os.conf.template for why
# these aren't 8000/3000). API_URL/WEB_URL are always derived from these by
# common.sh's sync_env_defaults - set API_PORT/WEB_PORT, not API_URL/WEB_URL
# directly, or your override will be silently recomputed away.
#
# Usage: scripts/smoke-test.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

# Load real config from .env (if this install has one yet) so DB/Redis
# credentials and derived defaults match the actual deployment, not just
# this library's hardcoded fallbacks.
load_env_file "$APP_ROOT/.env"
sync_env_defaults

FAILURES=0

check_http() {
  local name="$1" url="$2" accept="${3:-^200$}"
  local code attempt
  # A freshly (re)started container/systemd unit is not instantly ready -
  # Type=simple systemd units are marked "active" the moment the process is
  # forked, well before Uvicorn/Next.js has actually bound its port. A
  # single one-shot check right after install.sh starts these units races
  # that startup and fails even on a perfectly healthy deploy (seen on a
  # real VPS: nginx finished configuring the same second the smoke test
  # started). Retry for up to ~30s before giving up for real.
  for attempt in $(seq 1 15); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$url" || echo 000)"
    [[ "$code" =~ $accept ]] && break
    sleep 2
  done
  if [[ "$code" =~ $accept ]]; then
    log_ok "${name}: ${url} -> HTTP ${code}"
  else
    log_error "${name}: ${url} -> HTTP ${code} (expected ${accept}, gave up after ~30s of retries)"
    FAILURES=$((FAILURES + 1))
    return 1
  fi
}

check_postgres() {
  local host="${POSTGRES_HOST:-localhost}" port="${POSTGRES_PORT:-5432}"
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -h "$host" -p "$port" -t 5 >/dev/null 2>&1; then
      log_ok "Postgres: pg_isready succeeded (${host}:${port})"
      return 0
    fi
  fi
  if [[ -f "$COMPOSE_FILE" ]] && compose exec -T postgres pg_isready -U "${POSTGRES_USER:-postgres}" >/dev/null 2>&1; then
    log_ok "Postgres: healthy inside docker compose service 'postgres'"
    return 0
  fi
  log_error "Postgres: not reachable on ${host}:${port} (host) nor via docker compose"
  FAILURES=$((FAILURES + 1))
  return 1
}

check_redis() {
  local host="${REDIS_HOST:-localhost}" port="${REDIS_PORT:-6379}"
  if command -v redis-cli >/dev/null 2>&1; then
    if [[ "$(redis-cli -h "$host" -p "$port" -a "${REDIS_PASSWORD:-}" --no-auth-warning PING 2>/dev/null)" == "PONG" ]]; then
      log_ok "Redis: PING -> PONG (${host}:${port})"
      return 0
    fi
  fi
  if [[ -f "$COMPOSE_FILE" ]] && [[ "$(compose exec -T redis redis-cli PING 2>/dev/null)" == "PONG" ]]; then
    log_ok "Redis: healthy inside docker compose service 'redis'"
    return 0
  fi
  log_error "Redis: not reachable on ${host}:${port} (host) nor via docker compose"
  FAILURES=$((FAILURES + 1))
  return 1
}

log_step "Smoke test starting (API_URL=${API_URL}, WEB_URL=${WEB_URL})"

# Fail fast on the first failing check, mirroring how a deploy pipeline
# should behave — but still runs each *category* (api/web/db) so the
# operator gets one coherent error rather than a cascade.
check_http "API live"          "${API_URL}/health/live"          || { echo "SMOKE TEST FAILED: API liveness check failed."; exit 1; }
check_http "API ready"         "${API_URL}/health/ready"         || { echo "SMOKE TEST FAILED: API readiness check failed."; exit 1; }
check_http "API dependencies"  "${API_URL}/health/dependencies"  || { echo "SMOKE TEST FAILED: API dependency check failed."; exit 1; }
# The web root force-redirects ("/" -> 307 -> /command-center), so a healthy
# stack returns 3xx here, not 200. Accept 2xx and 3xx for the root check.
check_http "Web root"          "${WEB_URL}/"           '^(200|30[0-9])$' || { echo "SMOKE TEST FAILED: web app root did not respond (expected 2xx/3xx)."; exit 1; }
check_postgres                                                    || { echo "SMOKE TEST FAILED: Postgres is not reachable."; exit 1; }
check_redis                                                        || { echo "SMOKE TEST FAILED: Redis is not reachable."; exit 1; }

log_ok "All smoke tests passed"
echo "PASS"
exit 0
