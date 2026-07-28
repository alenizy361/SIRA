#!/usr/bin/env bash
#
# END-TO-END PROOF: does this company actually produce results?
#
# Answers the only question that matters — "I send something, does anything
# real come back?" — without needing the dashboard password. It drives a REAL
# goal through the REAL pipeline and prints what the CEO agent actually said:
#
#   1. verify the host worker is alive + its Claude session is authenticated
#   2. insert a real goal flagged plan_status=requested (exactly what the
#      dashboard's Send button does)
#   3. wait for the host worker's next poll tick to run the CEO agent
#   4. print the CEO's real plan (title + summary) and the tasks it assigned
#
# Nothing here is simulated. If this prints a plan, the pipeline works and the
# dashboard will show the same thing.
#
# Usage (as root on the server):
#   curl -fsSL <raw-url-of-this-file> | bash
#   bash scripts/prove-it-works.sh "بناء صفحة هبوط عربية سريعة"
set -uo pipefail

APP_ROOT="${APP_ROOT:-/opt/rabit-ai-company-os}"
UNIT="rabit-claude-worker.service"
GOAL_TITLE="${1:-افحص النظام وأعطني خطة تحسين بسيطة}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-300}"

ok()   { printf '\033[0;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[WARN]\033[0m %s\n' "$*"; }
bad()  { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; }
say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { bad "Run as root."; exit 1; }
PY="$APP_ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { bad "No worker venv at $PY — run scripts/install.sh first."; exit 1; }

# ---------------------------------------------------------------------------
say "1/3 is the company actually running?"
# ---------------------------------------------------------------------------
if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
  ok "$UNIT is active"
else
  bad "$UNIT is NOT running — nothing can execute. Run: bash scripts/worker-doctor.sh"
  exit 1
fi

HEARTBEAT="$(redis-cli --raw get rabit:worker:heartbeat 2>/dev/null || true)"
if printf '%s' "$HEARTBEAT" | grep -q '"authed": *true'; then
  ok "Worker heartbeat is fresh and its Claude session is authenticated"
elif [[ -n "$HEARTBEAT" ]]; then
  bad "Worker is running but NOT authenticated. Run: sudo -u aicompany -H claude auth login"
  exit 1
else
  warn "No heartbeat in Redis yet — continuing anyway (it may be mid-restart)."
fi

# ---------------------------------------------------------------------------
say "2/3 sending a real goal to the CEO"
# ---------------------------------------------------------------------------
echo "    goal: ${GOAL_TITLE}"
echo "    (this makes a real Claude CLI call — allow up to $((TIMEOUT_SECONDS/60)) minutes)"

cd "$APP_ROOT"
PYTHONPATH="$APP_ROOT/apps/api:$APP_ROOT/packages:$APP_ROOT/packages/permission-engine:$APP_ROOT/services:$APP_ROOT/services/claude-worker" \
GOAL_TITLE="$GOAL_TITLE" TIMEOUT_SECONDS="$TIMEOUT_SECONDS" "$PY" - <<'PY'
import os, sys, time

from app.db import get_sessionmaker
from app.models.company import Goal
from app.models.identity import Organization, User
from app.models.work import Plan, Task

title = os.environ["GOAL_TITLE"]
timeout = int(os.environ["TIMEOUT_SECONDS"])

db = get_sessionmaker()()

org_id = db.query(Organization.id).scalar()
if not org_id:
    print("[FAIL] No organization exists yet — finish onboarding in the dashboard first.")
    sys.exit(1)
user_id = db.query(User.id).filter(User.organization_id == org_id).scalar()

goal = Goal(
    organization_id=org_id,
    created_by=user_id,
    title=title,
    description=title,
    source="user",
    state="goal_captured",
    # Exactly what the dashboard's Send button sets - the worker polls for this.
    metadata_json={"plan_status": "requested"},
)
db.add(goal)
db.commit()
db.refresh(goal)
print(f"[ OK ] Goal created: {goal.id}")

print("\n==> 3/3 waiting for the CEO agent to respond...")
deadline = time.time() + timeout
plan = None
last = ""
while time.time() < deadline:
    db.expire_all()
    plan = db.query(Plan).filter(Plan.goal_id == goal.id).first()
    if plan:
        break
    g = db.get(Goal, goal.id)
    meta = dict(g.metadata_json or {})
    status = f"{meta.get('plan_status')} / goal={g.state}"
    if status != last:
        print(f"      ...{status}")
        last = status
    if meta.get("plan_status") == "failed":
        print(f"\n[FAIL] Planning FAILED: {meta.get('plan_error')}")
        sys.exit(1)
    time.sleep(5)

if not plan:
    print("\n[FAIL] No plan after the timeout. Check: journalctl -u rabit-claude-worker.service -n 40")
    sys.exit(1)

tasks = db.query(Task).filter(Task.plan_id == plan.id).all()
print("\n" + "=" * 66)
print("THE CEO ACTUALLY RESPONDED — this is real output, not a demo:")
print("=" * 66)
print(f"\nPLAN: {plan.title}\n")
print(f"{plan.summary or '(no summary)'}\n")
print(f"TASKS IT ASSIGNED ({len(tasks)}):")
for i, t in enumerate(tasks, 1):
    print(f"  {i}. [{t.risk_level}] {t.title}")
    print(f"     -> {t.assigned_agent_key}   (state: {t.state})")
print("\n" + "=" * 66)
print("Open the dashboard: this same plan and these tasks are shown there,")
print("and each agent lights up on the board as it works.")
print("=" * 66)
PY
rc=$?
[[ $rc -eq 0 ]] && ok "END-TO-END PROOF PASSED — the company produces real results." \
               || bad "End-to-end proof did not complete (see above)."
exit $rc
