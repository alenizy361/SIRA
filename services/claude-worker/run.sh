#!/usr/bin/env bash
# Entrypoint for infra/systemd/rabit-claude-worker.service.
# Runs as the non-root `aicompany` user; activates the repo's Python venv
# (created by scripts/install.sh) and starts the poll loop.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
fi

cd "$REPO_ROOT"
exec python3 -m claude_worker.worker
