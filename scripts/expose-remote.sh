#!/usr/bin/env bash
#
# ONE COMMAND: make your locally-running company reachable from outside this
# machine, without touching your router.
#
#   curl -fsSL https://raw.githubusercontent.com/alenizy361/SIRA/claude/rabit-ai-company-os-build-77fj8p/scripts/expose-remote.sh | sudo bash
#
# Why a tunnel and not port-forwarding: most home connections sit behind
# CGNAT (no public IP to forward to at all) or a dynamic IP that changes
# without notice, and even where forwarding IS possible it means opening an
# unencrypted port straight into your home network. A Cloudflare Tunnel makes
# an OUTBOUND-only connection from this machine to Cloudflare's edge, so
# nothing needs to be opened on your router/firewall, and the public URL is
# HTTPS automatically.
#
# This uses a free "quick tunnel" - zero signup, zero login, one command.
# The tradeoff: the URL is random and changes every time the tunnel restarts
# (a crash, a reboot). If you want a STABLE URL that survives restarts, see
# the note this script prints at the end (it requires one interactive login
# to a free Cloudflare account - like `claude auth login`, that step cannot
# be scripted).
#
# Prerequisite: the company must already be running locally (scripts/run-local.sh).
# This script only adds the tunnel in front of it.
set -uo pipefail

LOCAL_URL="${LOCAL_URL:-http://localhost:80}"
UNIT="rabit-tunnel.service"

ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  bad "Run this with sudo:  curl -fsSL <url> | sudo bash"
  exit 1
fi

# ---------------------------------------------------------------------------
say "1/4 Checking the company is actually running locally first"
# ---------------------------------------------------------------------------
# Tunneling a dead local service just gives remote visitors a working HTTPS
# connection to nothing - check this BEFORE installing anything.
if ! curl -fsS -m 5 "http://127.0.0.1/api/health/live" >/dev/null 2>&1 \
   && ! curl -fsS -m 5 "http://127.0.0.1:18081/health/live" >/dev/null 2>&1; then
  bad "The company doesn't appear to be running on this machine yet."
  echo "     Run scripts/run-local.sh first, then run this script."
  exit 1
fi
ok "Local company is responding"

# ---------------------------------------------------------------------------
say "2/4 Installing cloudflared"
# ---------------------------------------------------------------------------
if command -v cloudflared >/dev/null 2>&1; then
  ok "cloudflared already installed ($(cloudflared --version 2>/dev/null | head -1))"
else
  ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$ARCH" in
    amd64|x86_64) PKG_ARCH="amd64" ;;
    arm64|aarch64) PKG_ARCH="arm64" ;;
    armhf|armv7l) PKG_ARCH="armhf" ;;
    *) bad "Unsupported architecture: ${ARCH}"; exit 1 ;;
  esac

  log_via_repo() {
    mkdir -p --mode=0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg || return 1
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs 2>/dev/null || echo bookworm) main" \
      > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -qq && apt-get install -y -qq cloudflared
  }
  log_via_direct_deb() {
    local url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${PKG_ARCH}.deb"
    curl -fsSL "$url" -o /tmp/cloudflared.deb || return 1
    apt-get install -y -qq /tmp/cloudflared.deb
    rm -f /tmp/cloudflared.deb
  }

  log_step_ok=0
  if log_via_repo >/dev/null 2>&1 && command -v cloudflared >/dev/null 2>&1; then
    log_step_ok=1
  elif log_via_direct_deb >/dev/null 2>&1 && command -v cloudflared >/dev/null 2>&1; then
    log_step_ok=1
  fi

  if [[ "$log_step_ok" -eq 1 ]]; then
    ok "Installed cloudflared ($(cloudflared --version 2>/dev/null | head -1))"
  else
    bad "Could not install cloudflared automatically. See: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
say "3/4 Starting the tunnel as a background service"
# ---------------------------------------------------------------------------
cat > "/etc/systemd/system/${UNIT}" <<EOF
[Unit]
Description=Rabit AI Company OS - Cloudflare quick tunnel (public access without port-forwarding)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --url ${LOCAL_URL}
Restart=always
RestartSec=5
StartLimitIntervalSec=0

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null 2>&1
systemctl restart "$UNIT"

# ---------------------------------------------------------------------------
say "4/4 Fetching your public URL"
# ---------------------------------------------------------------------------
echo "    (cloudflared needs a few seconds to register the tunnel)"
URL=""
for _ in $(seq 1 20); do
  URL="$(journalctl -u "$UNIT" -n 100 --no-pager 2>/dev/null | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | tail -1)"
  [[ -n "$URL" ]] && break
  sleep 1
done

echo ""
echo "======================================================================"
if [[ -n "$URL" ]]; then
  ok "Your company is reachable from anywhere at:"
  echo ""
  echo "    ${URL}"
  echo ""
  warn "This URL is TEMPORARY: it changes if the tunnel restarts or the"
  warn "machine reboots. Get it again anytime with:"
  echo "      journalctl -u ${UNIT} -n 50 --no-pager | grep trycloudflare.com"
else
  bad "Tunnel started but the URL hasn't appeared in the log yet."
  echo "     Check in a few seconds with:"
  echo "       journalctl -u ${UNIT} -n 50 --no-pager | grep trycloudflare.com"
fi
echo "======================================================================"
echo ""
echo "  Want a PERMANENT URL that never changes? That needs a free Cloudflare"
echo "  account (one interactive login, like claude auth login - cannot be"
echo "  scripted) and a domain. Ask for it and it'll be set up as a proper"
echo "  named tunnel instead of this quick one."
