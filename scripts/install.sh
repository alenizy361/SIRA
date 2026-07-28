#!/usr/bin/env bash
#
# scripts/install.sh — idempotent installer for Rabit AI Company OS on
# Ubuntu 22.04/24.04.
#
# ASSUMPTIONS (double-check against the real apps/api, apps/web,
# docker-compose.yml, and .env.example once those are built):
#   - Target install directory is /opt/rabit-ai-company-os (APP_ROOT).
#   - docker-compose.yml lives at $APP_ROOT/docker-compose.yml with services
#     named exactly: postgres, redis, api, web.
#   - .env.example lives at $APP_ROOT/.env.example; any value literally
#     containing CHANGE_ME is replaced with an `openssl rand -hex 32` secret.
#   - Alembic migrations live under apps/api/alembic and are runnable as
#     `alembic upgrade head` from inside the `api` container's working dir.
#   - The API serves health endpoints at /health/live, /health/ready,
#     /health/dependencies on port 8000; the web app serves on port 3000.
#
# Safe to re-run: every step below checks current state before acting.
#
# Usage:
#   sudo scripts/install.sh
#   sudo DOMAIN=rabit.example.com scripts/install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$SCRIPT_DIR/lib/common.sh"

REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------------------
require_root "$@"

log_step "Rabit AI Company OS installer starting"
log_info "Repo checkout: ${REPO_ROOT}"
log_info "Install target (APP_ROOT): ${APP_ROOT}"

# ---------------------------------------------------------------------------
# 1. OS check (warn-only — we don't hard-fail on derivatives/newer point
#    releases, but we do want the operator to know what was assumed)
# ---------------------------------------------------------------------------
check_os() {
  if [[ ! -f /etc/os-release ]]; then
    log_warn "Cannot read /etc/os-release — not a recognizable Linux distro. Continuing anyway."
    return
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    log_warn "This installer targets Ubuntu 22.04/24.04; detected ID='${ID:-unknown}'. Proceeding, but things may not work."
  elif [[ "${VERSION_ID:-}" != "22.04" && "${VERSION_ID:-}" != "24.04" ]]; then
    log_warn "Detected Ubuntu ${VERSION_ID:-unknown}, not the tested 22.04/24.04. Proceeding anyway."
  else
    log_ok "Detected Ubuntu ${VERSION_ID}"
  fi
}

# ---------------------------------------------------------------------------
# 2. Resource checks (warn, never hard-fail per spec)
# ---------------------------------------------------------------------------
check_resources() {
  local cpus mem disk
  cpus="$(cpu_count)"
  mem="$(mem_total_mb)"
  disk="$(disk_free_gb /)"

  log_info "Detected resources: ${cpus} CPU(s), ${mem} MB RAM, ${disk} GB free on /"

  [[ "$cpus" -lt 2 ]] && log_warn "Fewer than 2 CPUs detected (${cpus}). The stack will run but may be slow under load."
  [[ "$mem" -lt 2048 ]] && log_warn "Less than 2GB RAM detected (${mem}MB). Consider adding swap or upgrading the VPS."
  [[ "$disk" -lt 10 ]] && log_warn "Less than 10GB free disk on / (${disk}GB). Backups and Docker images will fill this fast."
}

# ---------------------------------------------------------------------------
# 3. apt packages
# ---------------------------------------------------------------------------
APT_UPDATED=false
apt_update_once() {
  if [[ "$APT_UPDATED" == "false" ]]; then
    apt-get update -y
    APT_UPDATED=true
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log_ok "Docker already installed ($(docker --version))"
  else
    log_step "Installing Docker"
    apt_update_once
    if apt-get install -y docker.io; then
      log_ok "Installed docker.io from Ubuntu archive"
    else
      log_warn "docker.io install failed — falling back to Docker's official apt repository"
      install_docker_official_repo
    fi
  fi
  systemctl_or_service_enable docker

  if docker compose version >/dev/null 2>&1; then
    log_ok "docker compose plugin already present"
  else
    log_step "Installing docker-compose-plugin"
    apt_update_once
    if ! apt-get install -y docker-compose-plugin; then
      log_warn "docker-compose-plugin not available from current apt sources — falling back to Docker's official apt repository"
      install_docker_official_repo
      apt-get install -y docker-compose-plugin || die "Could not install a working 'docker compose' plugin from any source."
    fi
  fi
}

install_docker_official_repo() {
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /tmp/docker.gpg.asc
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg /tmp/docker.gpg.asc
    rm -f /tmp/docker.gpg.asc
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
}

systemctl_or_service_enable() {
  local svc="$1"
  if is_systemd_pid1; then
    systemctl enable --now "$svc" 2>/dev/null || true
  else
    log_warn "systemd not PID 1 — not enabling ${svc} as a system service. Ensure it is started some other way (e.g. the host's container runtime)."
  fi
}

install_nodejs() {
  if command -v node >/dev/null 2>&1; then
    local ver major
    ver="$(node --version)"
    major="$(echo "$ver" | sed -E 's/^v([0-9]+).*/\1/')"
    if [[ "$major" -ge 22 ]]; then
      log_ok "Node.js already installed (${ver})"
      return
    fi
    log_warn "Node.js ${ver} found but 22.x is required — installing 22.x via NodeSource alongside."
  fi
  log_step "Installing Node.js 22.x via NodeSource"
  curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh
  bash /tmp/nodesource_setup.sh
  rm -f /tmp/nodesource_setup.sh
  apt-get install -y nodejs
  log_ok "Installed $(node --version)"
}

install_python311() {
  if command -v python3.11 >/dev/null 2>&1; then
    log_ok "python3.11 already installed"
    return
  fi
  log_step "Installing python3.11"
  apt_update_once
  if apt-get install -y python3.11 python3.11-venv; then
    log_ok "Installed python3.11 from current apt sources"
  else
    log_warn "python3.11 not available from current apt sources on this release — add the deadsnakes PPA manually if host-level Python 3.11 tooling is required. Non-fatal: apps/api runs inside its Docker container regardless."
  fi
}

install_apt_packages() {
  log_step "Checking base apt packages"
  apt_update_once
  local pkgs=(nginx postgresql-client redis-tools gettext-base curl ca-certificates gnupg openssl)
  local missing=()
  for p in "${pkgs[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    log_info "Installing missing packages: ${missing[*]}"
    apt-get install -y "${missing[@]}"
  else
    log_ok "All base packages already present"
  fi

  install_docker
  install_nodejs
  install_python311
}

# ---------------------------------------------------------------------------
# 4. aicompany system user
# ---------------------------------------------------------------------------
create_aicompany_user() {
  if id "$AICOMPANY_USER" >/dev/null 2>&1; then
    log_ok "User '${AICOMPANY_USER}' already exists"
  else
    log_step "Creating non-root service user '${AICOMPANY_USER}'"
    useradd -r -m -s /bin/bash "$AICOMPANY_USER"
    log_ok "Created system user '${AICOMPANY_USER}' with home $(getent passwd "$AICOMPANY_USER" | cut -d: -f6)"
  fi
  # claude-worker needs to run `docker` in some deployments' health checks and
  # reach the API; it does NOT need to be in the docker group by default
  # (that would be root-equivalent). Leave it a plain unprivileged user.
}

# ---------------------------------------------------------------------------
# 5. App directory + repo sync
# ---------------------------------------------------------------------------
EXISTING_INSTALL_DETECTED=false

sync_repo_to_app_root() {
  mkdir -p "$(dirname "$APP_ROOT")"
  if [[ -d "$APP_ROOT" && -n "$(ls -A "$APP_ROOT" 2>/dev/null)" ]]; then
    EXISTING_INSTALL_DETECTED=true
  fi

  if [[ "$EXISTING_INSTALL_DETECTED" == "true" ]]; then
    log_warn "Existing install detected at ${APP_ROOT} — running backup.sh before touching anything"
    "$SCRIPT_DIR/backup.sh" --label pre-install >/dev/null
    log_ok "Pre-install backup complete"
  fi

  if [[ "$REPO_ROOT" == "$APP_ROOT" ]]; then
    log_ok "Already running from ${APP_ROOT} — nothing to sync"
    return
  fi

  log_step "Syncing repo checkout (${REPO_ROOT}) into ${APP_ROOT}"
  mkdir -p "$APP_ROOT"
  rsync -a --delete \
    --exclude '.git' \
    --exclude '.env' \
    --exclude 'backups' \
    --exclude 'node_modules' \
    --exclude '__pycache__' \
    --exclude 'workspace' \
    "$REPO_ROOT"/ "$APP_ROOT"/
  log_ok "Synced application code into ${APP_ROOT}"
}

setup_directory_structure() {
  log_step "Setting up directory structure under ${APP_ROOT}"
  mkdir -p "$APP_ROOT" "$BACKUP_ROOT" "$APP_ROOT/workspace"
  # Root-owned: application code, compose files, infra config, .env.
  chown -R root:root "$APP_ROOT"
  chmod 750 "$APP_ROOT"
  # aicompany-owned: its workspace (where the Claude CLI session state and
  # working files for services/claude-worker live).
  chown -R "${AICOMPANY_USER}:${AICOMPANY_USER}" "$APP_ROOT/workspace"
  chmod 750 "$APP_ROOT/workspace"
  # Backups are root-owned (contain DB dumps and .env copies).
  chown -R root:root "$BACKUP_ROOT"
  chmod 700 "$BACKUP_ROOT"
  log_ok "Directory ownership set (root-owned app/config, ${AICOMPANY_USER}-owned workspace)"
}

# ---------------------------------------------------------------------------
# 6. Node.js / claude CLI verification (never inject credentials)
# ---------------------------------------------------------------------------
CLAUDE_LOGIN_PENDING=true

verify_claude_cli() {
  log_step "Verifying Claude Code CLI"
  local claude_bin=""
  if sudo -u "$AICOMPANY_USER" -H bash -lc 'command -v claude' >/tmp/.claude_bin_path 2>/dev/null; then
    claude_bin="$(cat /tmp/.claude_bin_path)"
  fi
  rm -f /tmp/.claude_bin_path

  if [[ -z "$claude_bin" ]]; then
    log_warn "'claude' CLI not found on ${AICOMPANY_USER}'s PATH. Install it for that user (npm install -g @anthropic-ai/claude-code, or per Anthropic's docs) before starting rabit-claude-worker.service."
    return
  fi
  local version
  version="$(sudo -u "$AICOMPANY_USER" -H bash -lc 'claude --version' 2>/dev/null || echo unknown)"
  log_ok "Found claude CLI for ${AICOMPANY_USER}: ${claude_bin} (${version})"

  if sudo -u "$AICOMPANY_USER" -H bash -lc 'claude auth status' >/tmp/.claude_auth_status 2>&1; then
    if grep -qi 'logged in\|authenticated' /tmp/.claude_auth_status 2>/dev/null; then
      CLAUDE_LOGIN_PENDING=false
      log_ok "claude CLI reports an authenticated session for ${AICOMPANY_USER}"
    else
      log_warn "claude CLI is installed but auth status is unclear — see manual step below."
    fi
  else
    log_warn "claude CLI is not authenticated for ${AICOMPANY_USER} (or 'auth status' is not supported by this CLI version) — see manual step below."
  fi
  rm -f /tmp/.claude_auth_status
}

# ---------------------------------------------------------------------------
# 7. .env generation
# ---------------------------------------------------------------------------
generate_env() {
  local example="$APP_ROOT/.env.example"
  local target="$APP_ROOT/.env"

  if [[ ! -f "$example" ]]; then
    die ".env.example not found at ${example}. It must be committed alongside docker-compose.yml before install.sh can generate a working .env."
  fi

  if [[ -f "$target" ]]; then
    log_ok ".env already exists at ${target} — leaving it untouched (never overwriting secrets)."
    return
  fi

  log_step "Generating .env from .env.example (CHANGE_ME values replaced with generated secrets)"
  umask 077
  : > "$target"
  local line key value secret
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *=* ]]; then
      echo "$line" >> "$target"
      continue
    fi
    key="${line%%=*}"
    value="${line#*=}"
    if [[ "$value" == *CHANGE_ME* ]]; then
      secret="$(openssl rand -hex 32)"
      echo "${key}=${secret}" >> "$target"
    else
      echo "$line" >> "$target"
    fi
  done < "$example"
  chmod 600 "$target"
  chown "root:${AICOMPANY_USER}" "$target" 2>/dev/null || true
  log_ok "Generated ${target} (mode 600). This file must never be committed to git."
}

# ---------------------------------------------------------------------------
# 8. docker compose services + migrations
# ---------------------------------------------------------------------------
start_data_services() {
  [[ -f "$COMPOSE_FILE" ]] || die "docker-compose.yml not found at ${COMPOSE_FILE}."
  log_step "Pulling images and starting postgres + redis"
  compose pull
  compose up -d postgres redis
  log_info "Waiting for postgres to become healthy..."
  wait_for_container_healthy postgres 120 || die "postgres did not become healthy in time. Check: (cd ${APP_ROOT} && ${COMPOSE_CMD[*]:-docker compose} logs postgres)"
  log_info "Waiting for redis to become healthy..."
  wait_for_container_healthy redis 60 || die "redis did not become healthy in time."
  log_ok "postgres and redis are healthy"
}

run_migrations() {
  log_step "Running Alembic migrations inside the api container"
  compose run --rm api alembic upgrade head
  log_ok "Migrations applied"
}

start_app_services() {
  if is_systemd_pid1; then
    log_info "systemd is PID 1 — app services will be started by systemd units, not directly here."
    return
  fi
  log_warn "systemd not PID 1 — starting api/web directly via docker compose instead of systemd units."
  compose up -d api web
}

# ---------------------------------------------------------------------------
# 9. systemd units
# ---------------------------------------------------------------------------
install_systemd_units() {
  if ! require_systemd_or_skip "installing systemd units (rabit-api, rabit-web, rabit-claude-worker)"; then
    return
  fi
  log_step "Installing systemd units"
  for unit in rabit-api.service rabit-web.service rabit-claude-worker.service; do
    local src="$APP_ROOT/infra/systemd/${unit}"
    if [[ ! -f "$src" ]]; then
      log_warn "Missing unit file ${src} — skipping ${unit}."
      continue
    fi
    cp "$src" "/etc/systemd/system/${unit}"
  done
  systemctl daemon-reload
  systemctl enable --now rabit-api.service rabit-web.service
  if ! systemctl enable --now rabit-claude-worker.service; then
    log_warn "rabit-claude-worker.service did not start cleanly. This is expected if 'claude auth login' has not been run yet as ${AICOMPANY_USER} — see the manual step printed at the end of this script."
  fi
}

# ---------------------------------------------------------------------------
# 10. nginx
# ---------------------------------------------------------------------------
configure_nginx() {
  if ! command -v nginx >/dev/null 2>&1; then
    log_warn "nginx not installed — skipping reverse proxy configuration."
    return
  fi
  local domain="${DOMAIN:-_}"
  local template="$APP_ROOT/infra/nginx/rabit-os.conf.template"
  [[ -f "$template" ]] || { log_warn "Template ${template} not found — skipping nginx configuration."; return; }

  log_step "Configuring Nginx (DOMAIN=${domain})"
  mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
  DOMAIN="$domain" envsubst '${DOMAIN}' < "$template" > /etc/nginx/sites-available/rabit-os.conf

  if [[ "$domain" == "_" ]]; then
    # No real domain configured: this server block is meant to catch bare-IP
    # requests. Without an explicit `default_server`, nginx silently falls
    # back to whichever OTHER site on this box happens to sort first when
    # more than one vhost is present - on a VPS that already hosts other
    # sites (a real, common case, not hypothetical), that means visiting
    # the server's IP could route to a pre-existing site instead of this
    # dashboard. Mark it explicitly rather than relying on nginx's implicit
    # "first one wins" behavior.
    sed -i \
      -e 's/^\( *listen 80\);/\1 default_server;/' \
      -e 's/^\( *listen \[::\]:80\);/\1 default_server;/' \
      /etc/nginx/sites-available/rabit-os.conf
    log_info "No DOMAIN set - marked rabit-os.conf as the default_server for port 80/[::]:80"
  fi

  ln -sf /etc/nginx/sites-available/rabit-os.conf /etc/nginx/sites-enabled/rabit-os.conf

  if nginx -t; then
    if is_systemd_pid1; then
      systemctl enable --now nginx 2>/dev/null || true
      systemctl reload nginx
    else
      service nginx reload 2>/dev/null || nginx -s reload 2>/dev/null || log_warn "Could not reload nginx automatically — reload it manually."
    fi
    log_ok "Nginx configured and reloaded for DOMAIN=${domain}"
  else
    die "nginx -t failed against the generated config at /etc/nginx/sites-available/rabit-os.conf — not reloading. Fix the config and re-run."
  fi
}

maybe_run_certbot() {
  local domain="${DOMAIN:-}"
  if [[ -z "$domain" || "$domain" == "_" ]]; then
    log_info "DOMAIN not set — skipping TLS/certbot. Set DOMAIN=your.domain and re-run to enable HTTPS."
    return
  fi
  if ! command -v certbot >/dev/null 2>&1; then
    log_warn "certbot not installed — skipping TLS. Install with: apt-get install -y certbot python3-certbot-nginx"
    return
  fi
  if curl -fsS -o /dev/null -m 5 "http://${domain}/" 2>/dev/null; then
    log_step "Requesting a TLS certificate for ${domain} via certbot"
    certbot --nginx -d "$domain" --non-interactive --agree-tos -m "${CERTBOT_EMAIL:-admin@${domain}}" \
      || log_warn "certbot failed — continuing without TLS. The dashboard remains reachable over HTTP."
  else
    log_warn "${domain} is not reachable over HTTP yet (DNS not pointed here, or firewall blocking port 80) — skipping certbot for now. Re-run install.sh once DNS resolves."
  fi
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
main() {
  check_os
  check_resources
  install_apt_packages
  create_aicompany_user
  sync_repo_to_app_root
  setup_directory_structure
  verify_claude_cli
  generate_env
  # .env may define POSTGRES_*/DB_NAME overrides — load it so downstream
  # steps (compose, migrations) see the same values docker compose will.
  load_env_file "$APP_ROOT/.env"
  sync_env_defaults
  start_data_services
  run_migrations
  install_systemd_units
  start_app_services
  configure_nginx
  maybe_run_certbot

  log_step "Running smoke tests"
  if API_URL="${API_URL}" WEB_URL="${WEB_URL}" "$SCRIPT_DIR/smoke-test.sh"; then
    log_ok "Smoke tests passed"
  else
    log_error "Smoke tests failed — the stack is installed but not fully healthy yet. Run 'scripts/doctor.sh' for a diagnosis."
  fi

  local dashboard_url="http://${DOMAIN:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
  [[ -z "${DOMAIN:-}" ]] && dashboard_url="http://localhost:3000 (or http://<server-ip>:3000 if accessed remotely without a domain/Nginx)"

  log_step "Install finished"
  echo ""
  echo "  Dashboard:      ${dashboard_url}"
  echo "  API:            ${API_URL}"
  echo "  App directory:  ${APP_ROOT}"
  echo "  Backups:        ${BACKUP_ROOT}"
  echo ""
  if [[ "$CLAUDE_LOGIN_PENDING" == "true" ]]; then
    echo "  MANUAL STEP REQUIRED:"
    echo "    The Claude worker cannot run yet — authenticate the aicompany OS"
    echo "    user's Claude Code CLI session (this cannot and must not be"
    echo "    scripted/injected):"
    echo ""
    echo "      sudo -u ${AICOMPANY_USER} -H claude auth login"
    echo ""
    echo "    Then (re)start the worker:"
    echo "      sudo systemctl restart rabit-claude-worker.service   # if systemd is available"
    echo "      # otherwise run services/claude-worker directly as the ${AICOMPANY_USER} user"
    echo ""
  fi
}

main "$@"
