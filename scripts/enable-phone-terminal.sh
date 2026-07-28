#!/usr/bin/env bash
#
# ONE COMMAND: get a real terminal (SSH) into this machine from your PHONE,
# from anywhere - not just on the same WiFi.
#
#   curl -fsSL https://raw.githubusercontent.com/alenizy361/SIRA/claude/rabit-ai-company-os-build-77fj8p/scripts/enable-phone-terminal.sh | sudo bash
#
# How: Tailscale - a private WireGuard mesh network. Your PC and your phone
# both join the same private network (free account), and then your phone can
# reach the PC by a private IP/hostname from ANYWHERE with internet, exactly
# as if they were on the same LAN. No router config, no port-forwarding, no
# exposing SSH to the public internet at all (unlike opening port 22 on a
# router, which is a magnet for scanners/brute force - Tailscale's tunnel is
# only reachable by devices YOU added to your account).
#
# What this script does on THIS machine:
#   1. installs and enables openssh-server (the actual terminal server)
#   2. installs Tailscale
#   3. starts `tailscale up`, which prints a one-time login link (interactive
#      - like `claude auth login`, this cannot be scripted: it's a browser
#      device-approval flow tied to your account)
#
# What YOU still do on your phone (can't be scripted from here):
#   1. Install the "Tailscale" app (App Store / Google Play) and log into the
#      SAME account you used for the login link above.
#   2. Install any SSH client - Termius (iOS/Android) or Blink Shell (iOS) or
#      Termux (Android) all work fine.
#   3. SSH to the address this script prints at the end.
set -uo pipefail

TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"

ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  bad "Run this with sudo:  curl -fsSL <url> | sudo bash"
  exit 1
fi

# ---------------------------------------------------------------------------
say "1/3 Installing the SSH server"
# ---------------------------------------------------------------------------
if command -v sshd >/dev/null 2>&1 || dpkg -s openssh-server >/dev/null 2>&1; then
  ok "openssh-server already installed"
else
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq openssh-server \
    || { bad "Could not install openssh-server."; exit 1; }
  ok "Installed openssh-server"
fi
systemctl enable --now ssh >/dev/null 2>&1 || systemctl enable --now sshd >/dev/null 2>&1 || true
if systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet sshd 2>/dev/null; then
  ok "SSH server is running"
else
  bad "SSH server did not start. Check: systemctl status ssh"
  exit 1
fi

# ---------------------------------------------------------------------------
say "2/3 Installing Tailscale"
# ---------------------------------------------------------------------------
if command -v tailscale >/dev/null 2>&1; then
  ok "Tailscale already installed ($(tailscale version 2>/dev/null | head -1))"
else
  curl -fsSL https://tailscale.com/install.sh | sh || { bad "Tailscale install failed."; exit 1; }
  ok "Installed Tailscale"
fi

# ---------------------------------------------------------------------------
say "3/3 Connecting this machine to your private network"
# ---------------------------------------------------------------------------
if tailscale status >/dev/null 2>&1; then
  ok "Already connected to Tailscale"
else
  echo "    A one-time login link will appear below. Open it (on this machine"
  echo "    or by copying the link to any browser, even on your phone) and"
  echo "    approve it - like logging into any app for the first time."
  echo ""
  tailscale up --ssh
fi

# Give the phone SSH access to the LINUX account too (not just the Tailscale
# network layer) via Tailscale's own built-in SSH server, which needs no
# separate SSH key setup - it authenticates using your Tailscale login itself.
tailscale set --ssh 2>/dev/null || true

TS_IP="$(tailscale ip -4 2>/dev/null | head -1)"
TS_NAME="$(tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 | sed 's/\.$//')"

echo ""
echo "======================================================================"
if [[ -n "$TS_IP" ]]; then
  ok "This machine is on your private Tailscale network."
  echo ""
  echo "  On your phone:"
  echo "    1. Install the Tailscale app and log into the SAME account."
  echo "    2. Install an SSH app (Termius, Blink Shell, or Termux)."
  echo "    3. SSH to:"
  echo ""
  echo "         ssh ${TARGET_USER}@${TS_IP}"
  [[ -n "$TS_NAME" ]] && echo "       or: ssh ${TARGET_USER}@${TS_NAME}"
  echo ""
  echo "  This works from ANYWHERE with internet - not just your home WiFi -"
  echo "  and nothing about SSH is exposed to the public internet at all."
else
  warn "Tailscale is installed but not connected yet. Finish the login link"
  warn "printed above, then run:  tailscale ip -4"
fi
echo "======================================================================"
