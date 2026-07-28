#!/usr/bin/env bash
# Entrypoint for infra/systemd/rabit-claude-worker.service.
#
# Runs as the non-root `aicompany` user. Unlike api/web (which run in Docker
# containers), this process runs directly on the host because it shells out
# to the `claude` CLI, whose login/session state is per-OS-user and lives in
# ~aicompany - a container would not have it.
#
# That means the worker needs its Python dependencies installed ON THE HOST,
# in the venv scripts/install.sh creates at $REPO_ROOT/.venv. If that venv is
# missing this script fails loudly rather than silently falling back to a
# system python that has neither the dependencies nor the right import paths
# (an earlier version did fall back, which made the service crash-loop with a
# confusing "No module named 'claude_worker'" instead of naming the real
# problem).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -x "$REPO_ROOT/.venv/bin/python" ]; then
  echo "FATAL: no Python venv at $REPO_ROOT/.venv" >&2
  echo "       The claude-worker runs on the host (not in Docker) and needs its" >&2
  echo "       dependencies installed there. Re-run: sudo scripts/install.sh" >&2
  exit 1
fi

# The worker imports across the whole monorepo: app.* (apps/api), contracts
# (packages), permission_engine, orchestrator (services), and claude_worker
# itself (services/claude-worker). Set this explicitly rather than relying on
# cwd - `python -m claude_worker.worker` from REPO_ROOT cannot find the
# claude_worker package otherwise, since it lives one level down under
# services/claude-worker/.
export PYTHONPATH="$REPO_ROOT/apps/api:$REPO_ROOT/packages:$REPO_ROOT/packages/permission-engine:$REPO_ROOT/services:$REPO_ROOT/services/claude-worker${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO_ROOT"
exec "$REPO_ROOT/.venv/bin/python" -m claude_worker.worker
