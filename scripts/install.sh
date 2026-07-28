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
#     /health/dependencies, host-published at 127.0.0.1:${API_PORT:-18081};
#     the web app is host-published at 127.0.0.1:${WEB_PORT:-18080}. Neither
#     port is meant to be reached directly by a browser - only Nginx and
#     local health checks use them (see docker-compose.yml and
#     infra/nginx/rabit-os.conf.template for why 8000/3000 were dropped:
#     a VPS already running other apps is very likely already using those).
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

# Which commit is about to be deployed? This installer does NOT pull - it
# deploys whatever is in REPO_ROOT right now. Stating the version up front
# (and recording it in APP_ROOT/BUILD_INFO) is the difference between "the
# install succeeded" and "the install succeeded and shipped what I think it
# did" - a stale checkout otherwise reinstalls old code perfectly happily.
SOURCE_COMMIT="unknown"
SOURCE_BRANCH="unknown"
report_source_version() {
  if ! git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    log_warn "${REPO_ROOT} is NOT a git checkout - cannot verify which version you are deploying."
    log_warn "If this is ${APP_ROOT} itself, it never contains .git (the sync excludes it), so it"
    log_warn "can never be updated with 'git pull'. Re-run this installer from a real git clone."
    return
  fi
  SOURCE_COMMIT="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  SOURCE_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  local dirty=""
  [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]] && dirty=" (uncommitted changes present)"
  log_info "Deploying commit: ${SOURCE_COMMIT} on ${SOURCE_BRANCH}${dirty}"

  # Behind the remote? Then the operator almost certainly meant to pull first.
  local upstream behind
  upstream="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [[ -n "$upstream" ]]; then
    git -C "$REPO_ROOT" fetch --quiet origin "$SOURCE_BRANCH" 2>/dev/null || true
    behind="$(git -C "$REPO_ROOT" rev-list --count "HEAD..${upstream}" 2>/dev/null || echo 0)"
    if [[ "${behind:-0}" -gt 0 ]]; then
      log_warn "This checkout is ${behind} commit(s) BEHIND ${upstream}."
      log_warn "You are about to deploy OLD code. Run this first, then re-run the installer:"
      log_warn "    git -C ${REPO_ROOT} pull origin ${SOURCE_BRANCH}"
    fi
  fi
}
report_source_version

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
  local pkgs=(nginx postgresql-client redis-tools gettext-base curl ca-certificates gnupg openssl rsync)
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
    # This is a silent-staleness trap: APP_ROOT never contains .git (the
    # rsync below excludes it), so running the installer from here can only
    # ever redeploy the code already sitting in APP_ROOT. Every re-run then
    # "succeeds" while shipping the same old build forever.
    if ! git -C "$APP_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
      log_warn "${APP_ROOT} has no .git, so this run CANNOT bring in newer code."
      log_warn "To deploy an update: pull in a real clone and run that clone's installer, e.g."
      log_warn "    git -C /root/SIRA pull origin <branch> && sudo /root/SIRA/scripts/install.sh"
    fi
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

  # Stamp what was actually deployed. APP_ROOT has no .git, so without this
  # there is no way to tell on the server which commit is live.
  {
    echo "commit=${SOURCE_COMMIT}"
    echo "branch=${SOURCE_BRANCH}"
    echo "source=${REPO_ROOT}"
    echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$APP_ROOT/BUILD_INFO"
  log_info "Recorded deployed version in ${APP_ROOT}/BUILD_INFO (commit ${SOURCE_COMMIT})"
}

setup_directory_structure() {
  log_step "Setting up directory structure under ${APP_ROOT}"
  mkdir -p "$APP_ROOT" "$BACKUP_ROOT" "$APP_ROOT/workspace"

  # Application code is owned by root but group-owned by aicompany with group
  # read/traverse. This is the crucial bit: the claude-worker runs as the
  # non-root aicompany user and must be able to cd into APP_ROOT, execute
  # services/claude-worker/run.sh, and read the code + its .venv. With the
  # previous root:root 0750 the worker (an "other" relative to root) could
  # not even traverse APP_ROOT, so systemd failed every start with
  # "CHDIR ... Permission denied" and crash-looped thousands of times.
  # root keeps write; aicompany gets read+execute via the group; the world
  # still gets nothing.
  chown -R "root:${AICOMPANY_USER}" "$APP_ROOT"
  chmod -R g+rX "$APP_ROOT"
  chmod 2750 "$APP_ROOT"

  # The workspace is aicompany's to write in (Claude CLI session state, task
  # worktrees).
  chown -R "${AICOMPANY_USER}:${AICOMPANY_USER}" "$APP_ROOT/workspace"
  chmod 2770 "$APP_ROOT/workspace"

  # run.sh must be executable by the group (aicompany).
  chmod 0750 "$APP_ROOT/services/claude-worker/run.sh" 2>/dev/null || true

  # .env holds secrets - root-only. The `chmod -R g+rX` above would have
  # exposed it to the aicompany group, so lock it back down. The worker never
  # needs to read it directly (systemd loads it via EnvironmentFile= and
  # passes the values in as environment variables).
  chown root:root "$APP_ROOT/.env" 2>/dev/null || true
  chmod 0600 "$APP_ROOT/.env" 2>/dev/null || true

  # Backups are root-only (DB dumps + .env copies) - the recursive g+rX above
  # touched them too, so re-lock the whole subtree, not just its top dir.
  chown -R root:root "$BACKUP_ROOT"
  chmod -R go-rwx "$BACKUP_ROOT"
  chmod 700 "$BACKUP_ROOT"
  log_ok "Directory ownership set (root-owned code, ${AICOMPANY_USER} group read/exec, ${AICOMPANY_USER}-owned workspace)"
}

# ---------------------------------------------------------------------------
# 5b. Host Python venv for services/claude-worker
# ---------------------------------------------------------------------------
setup_worker_venv() {
  # api/web run in Docker, but the claude-worker deliberately does NOT: it
  # shells out to the `claude` CLI whose login state is per-OS-user and lives
  # in ~aicompany. So it needs its Python dependencies installed on the HOST.
  # This step was missing entirely at first, which made
  # rabit-claude-worker.service crash-loop on a real deployment - run.sh
  # sourced a $APP_ROOT/.venv that nothing ever created.
  local venv="$APP_ROOT/.venv"
  local py=""
  for candidate in python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then py="$candidate"; break; fi
  done
  if [[ -z "$py" ]]; then
    log_warn "No python3 on PATH — skipping worker venv. rabit-claude-worker.service will not start until Python 3.11+ is installed."
    return
  fi

  if [[ ! -x "$venv/bin/python" ]]; then
    log_step "Creating host Python venv for claude-worker at ${venv}"
    if ! "$py" -m venv "$venv" 2>/dev/null; then
      log_warn "Could not create venv with '${py} -m venv' (is python3-venv installed?) — skipping. rabit-claude-worker.service will not start."
      return
    fi
  else
    log_ok "Worker venv already exists at ${venv}"
  fi

  log_step "Installing worker dependencies into ${venv}"
  # The worker imports app.config/app.db/app.models and packages/*, so it
  # needs the same dependency set as the API itself.
  if "$venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 && \
     "$venv/bin/pip" install --quiet -r "$APP_ROOT/apps/api/requirements.txt"; then
    log_ok "Worker dependencies installed"
  else
    log_warn "pip install for the worker venv failed — rabit-claude-worker.service will not start until this is resolved."
    return
  fi

  chmod +x "$APP_ROOT/services/claude-worker/run.sh" 2>/dev/null || true
  # The worker runs as aicompany, so it must be able to read its own venv.
  chown -R "${AICOMPANY_USER}:${AICOMPANY_USER}" "$venv"
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
  # One shared DB password so the value embedded in DATABASE_URL matches
  # POSTGRES_PASSWORD (both carry CHANGE_ME in .env.example). Everything else
  # (e.g. SESSION_SECRET_KEY) gets its own per-key random secret.
  local line key value secret db_pw
  db_pw="$(openssl rand -hex 32)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "$line" =~ ^[[:space:]]*# || "$line" != *=* ]]; then
      echo "$line" >> "$target"
      continue
    fi
    key="${line%%=*}"
    value="${line#*=}"
    if [[ "$value" == *CHANGE_ME* ]]; then
      # Substitute the CHANGE_ME token IN PLACE so structured values like
      # DATABASE_URL=postgresql+psycopg://rabit:CHANGE_ME@localhost:5432/rabit_os
      # keep their scheme/host/db instead of being replaced wholesale by a
      # bare hex string (which SQLAlchemy cannot parse -> worker crash-loop).
      case "$key" in
        POSTGRES_PASSWORD|DATABASE_URL)
          value="${value//CHANGE_ME/$db_pw}"
          ;;
        *)
          secret="$(openssl rand -hex 32)"
          value="${value//CHANGE_ME/$secret}"
          ;;
      esac
      echo "${key}=${value}" >> "$target"
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
build_app_images() {
  [[ -f "$COMPOSE_FILE" ]] || die "docker-compose.yml not found at ${COMPOSE_FILE}."
  # `docker compose up` (used by rabit-api.service/rabit-web.service, and
  # by run_migrations below) does NOT rebuild an already-existing local
  # image just because its Dockerfile changed on disk - re-running this
  # script after a code update (e.g. `git pull` into the source checkout,
  # which sync_repo_to_app_root then rsyncs into APP_ROOT) would silently
  # keep serving the OLD image otherwise. Found for real: a fix to
  # api.Dockerfile had no effect until this explicit build step existed.
  log_step "Building api/web images (picks up any Dockerfile/source changes)"
  compose build api web
}

start_data_services() {
  [[ -f "$COMPOSE_FILE" ]] || die "docker-compose.yml not found at ${COMPOSE_FILE}."
  log_step "Pulling images and starting postgres + redis"
  compose pull postgres redis
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
  # `enable --now` STARTS a unit, but on a re-install the unit is already
  # running the OLD container - and `enable --now` is a no-op for an
  # already-active unit, so it would keep serving the stale image even
  # though build_app_images just built a new one. `enable` (arm at boot)
  # followed by `restart` (recreate the container from the freshly built
  # image) is what actually makes a re-install take effect. This was a real
  # bug: after a code update the dashboard kept showing the old build until
  # the container was manually recreated.
  systemctl enable rabit-api.service rabit-web.service >/dev/null 2>&1 || true
  log_step "Restarting app services to pick up the freshly built images"
  systemctl restart rabit-api.service
  systemctl restart rabit-web.service
  systemctl enable rabit-claude-worker.service >/dev/null 2>&1 || true
  if ! systemctl restart rabit-claude-worker.service; then
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
  local web_port="${WEB_PORT:-18080}"
  local api_port="${API_PORT:-18081}"
  local template="$APP_ROOT/infra/nginx/rabit-os.conf.template"
  [[ -f "$template" ]] || { log_warn "Template ${template} not found — skipping nginx configuration."; return; }

  log_step "Configuring Nginx (DOMAIN=${domain}, WEB_PORT=${web_port}, API_PORT=${api_port})"
  mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
  DOMAIN="$domain" WEB_PORT="$web_port" API_PORT="$api_port" \
    envsubst '${DOMAIN} ${WEB_PORT} ${API_PORT}' < "$template" > /etc/nginx/sites-available/rabit-os.conf

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

  # Ubuntu's stock nginx ships an enabled default site that also declares
  # `listen 80 default_server`. Leaving it enabled alongside our config (which
  # marks itself default_server on the no-DOMAIN path) makes `nginx -t` fail
  # with "a duplicate default server for 0.0.0.0:80" and aborts the install.
  # This app owns port 80 on its host, so remove the stock default site.
  rm -f /etc/nginx/sites-enabled/default

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
  setup_worker_venv
  verify_claude_cli
  generate_env
  # .env may define POSTGRES_*/DB_NAME overrides — load it so downstream
  # steps (compose, migrations) see the same values docker compose will.
  load_env_file "$APP_ROOT/.env"
  sync_env_defaults
  start_data_services
  build_app_images
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

  # Always port 80 via Nginx (default_server when DOMAIN is unset - see
  # configure_nginx) - never a container port directly; the browser is
  # never meant to reach 18080/18081 itself.
  local dashboard_url="http://${DOMAIN:-$(hostname -I 2>/dev/null | awk '{print $1}')}/"

  log_step "Install finished"
  echo ""
  echo "  Dashboard:      ${dashboard_url}"
  echo "  API:            ${API_URL}"
  echo "  App directory:  ${APP_ROOT}"
  echo "  Backups:        ${BACKUP_ROOT}"
  echo "  Deployed commit: ${SOURCE_COMMIT} (${SOURCE_BRANCH})"
  echo ""
  if [[ "$SOURCE_COMMIT" == "unknown" ]]; then
    echo "  NOTE: the deployed version could not be determined (source is not a git"
    echo "  checkout). If the dashboard still shows an older UI than you expect, you"
    echo "  are almost certainly re-deploying stale code - see the warnings above."
    echo ""
  fi
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
