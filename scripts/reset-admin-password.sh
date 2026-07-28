#!/usr/bin/env bash
#
# Reset a dashboard login password, safely, on the server.
#
# Use this when you cannot get into the dashboard (forgotten password, or the
# account is locked out after failed attempts). It:
#   1. lists the existing accounts so you can pick the right email
#   2. reads a NEW password from the terminal WITHOUT echoing it
#   3. hashes it with the application's own argon2 hasher
#   4. clears the failed-login counter and any lockout
#   5. revokes existing sessions, so an old stolen cookie cannot outlive the reset
#
# The password is never printed, never passed as a command-line argument (which
# would land in your shell history and in `ps` output), and never written to a
# file. It is read on stdin and handed straight to the hasher.
#
# NEVER send your password to anyone - including an AI assistant. Reset it here
# and log in yourself.
#
# Usage (as root on the server):
#   bash scripts/reset-admin-password.sh                # interactive picker
#   bash scripts/reset-admin-password.sh you@email.com  # target one account
set -uo pipefail

APP_ROOT="${APP_ROOT:-/opt/rabit-ai-company-os}"
TARGET_EMAIL="${1:-}"

ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { bad "Run as root."; exit 1; }
PY="$APP_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { bad "No venv at $PY — run scripts/install.sh first."; exit 1; }

export PYTHONPATH="$APP_ROOT/apps/api:$APP_ROOT/packages:$APP_ROOT/packages/permission-engine:$APP_ROOT/services:$APP_ROOT/services/claude-worker"
cd "$APP_ROOT"

say "Accounts on this server"
"$PY" - <<'PY'
from app.db import get_sessionmaker
from app.models.identity import User
db = get_sessionmaker()()
users = db.query(User).order_by(User.created_at.asc()).all()
if not users:
    print("  (none - finish onboarding in the dashboard first)")
for u in users:
    locked = " [LOCKED]" if u.locked_until else ""
    print(f"  - {u.email}   ({u.display_name}){locked}")
PY

if [[ -z "$TARGET_EMAIL" ]]; then
  printf '\nEmail to reset: '
  read -r TARGET_EMAIL
fi
[[ -n "$TARGET_EMAIL" ]] || { bad "No email given."; exit 1; }

# -s: do not echo. Read twice to catch typos before committing the change.
printf 'New password for %s (input hidden): ' "$TARGET_EMAIL"
read -rs NEW_PW; echo
printf 'Repeat it: '
read -rs NEW_PW2; echo
[[ "$NEW_PW" == "$NEW_PW2" ]] || { bad "Passwords do not match — nothing changed."; exit 1; }
[[ "${#NEW_PW}" -ge 12 ]] || { bad "Use at least 12 characters — nothing changed."; exit 1; }

say "Applying"
# The password travels on stdin only: never in argv, never in the environment,
# so it cannot leak via `ps`, shell history, or the process environment.
printf '%s' "$NEW_PW" | TARGET_EMAIL="$TARGET_EMAIL" "$PY" - <<'PY'
import os, sys

from app.auth.security import hash_password
from app.db import get_sessionmaker
from app.models.identity import Session as AuthSession, User

password = sys.stdin.read()
email = os.environ["TARGET_EMAIL"].strip()

db = get_sessionmaker()()
user = db.query(User).filter(User.email == email).first()
if not user:
    print(f"[FAIL] No account with email {email!r}.")
    sys.exit(1)

user.password_hash = hash_password(password)
user.failed_login_count = 0   # clear the lockout counter
user.locked_until = None      # and any active lockout
user.is_active = True
db.add(user)

# Any session issued before the reset must die with it - otherwise a cookie
# stolen earlier would keep working after you rotated the password.
revoked = db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
db.commit()
print(f"[ OK ] Password reset for {email}. Lockout cleared. {revoked} old session(s) revoked.")
PY
rc=$?
unset NEW_PW NEW_PW2

if [[ $rc -eq 0 ]]; then
  ok "Done. Log in at your server's public URL with the new password."
  echo "     Then send a command and watch the agents react."
else
  bad "Password was NOT changed (see above)."
fi
exit $rc
