#!/usr/bin/env bash
#
# ONE COMMAND: find out EXACTLY where a redeploy silently failed to pick up
# new code, instead of guessing. Checks every layer independently: the
# remote's latest commit, the local clone's commit, what was installed into
# APP_ROOT, whether the SOURCE on disk actually has the new code, whether
# the RUNNING api container actually has it (these can differ - a stale
# Docker layer cache is a classic silent-failure point), when each image was
# actually built, and when the host worker last restarted.
#
#   curl -fsSL https://raw.githubusercontent.com/alenizy361/SIRA/claude/rabit-ai-company-os-build-77fj8p/scripts/deploy-diagnostic.sh | bash
set -uo pipefail

CLONE="${RABIT_SRC:-/root/SIRA}"
[ -d "$CLONE/.git" ] || CLONE="/opt/rabit-src"
APP_ROOT="${APP_ROOT:-/opt/rabit-ai-company-os}"
BRANCH="${RABIT_BRANCH:-claude/rabit-ai-company-os-build-77fj8p}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

say "1/7 Latest commit on GitHub (what SHOULD be deployed)"
git ls-remote "https://github.com/alenizy361/SIRA.git" "refs/heads/${BRANCH}" 2>&1

say "2/7 Commit the local source clone is actually on"
if [ -d "$CLONE/.git" ]; then
  git -C "$CLONE" log -1 --format="%H %s (%cr)"
else
  echo "NO CLONE found at $CLONE or /opt/rabit-src - the update step never ran"
fi

say "3/7 Commit last installed into APP_ROOT"
cat "${APP_ROOT}/BUILD_INFO" 2>/dev/null || echo "BUILD_INFO missing at ${APP_ROOT}/BUILD_INFO"

say "4/7 Does the SOURCE ON DISK have the newest fixes?"
grep -q "work_log" "${APP_ROOT}/apps/api/app/routers/goals.py" 2>/dev/null \
  && echo "[ OK ] source has work_log (agent transparency fix)" \
  || echo "[MISS] source does NOT have work_log yet"
grep -q "_advance_completed_goals" "${APP_ROOT}/services/claude-worker/claude_worker/worker.py" 2>/dev/null \
  && echo "[ OK ] source has _advance_completed_goals (stuck-goal fix)" \
  || echo "[MISS] source does NOT have _advance_completed_goals yet"

say "5/7 Does the RUNNING api CONTAINER have the newest fixes? (can differ from #4 - stale image cache)"
if command -v docker >/dev/null 2>&1; then
  (cd "$APP_ROOT" && docker compose exec -T api grep -q "work_log" /app/apps/api/app/routers/goals.py 2>/dev/null) \
    && echo "[ OK ] running api container has work_log" \
    || echo "[MISS] running api container does NOT have work_log - image is stale"
else
  echo "docker not found here"
fi

say "6/7 When was each Docker image actually built?"
if command -v docker >/dev/null 2>&1; then
  api_img="$(docker compose -f "${APP_ROOT}/docker-compose.yml" images -q api 2>/dev/null)"
  web_img="$(docker compose -f "${APP_ROOT}/docker-compose.yml" images -q web 2>/dev/null)"
  [ -n "$api_img" ] && docker image inspect --format 'api image built:  {{.Created}}' "$api_img" 2>&1
  [ -n "$web_img" ] && docker image inspect --format 'web image built:  {{.Created}}' "$web_img" 2>&1
fi

say "7/7 When did the host worker (non-Docker) last actually restart?"
systemctl show rabit-claude-worker.service -p ActiveEnterTimestamp,ExecMainStartTimestamp 2>&1
echo "current server time: $(date -u)"
