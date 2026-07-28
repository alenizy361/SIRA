"""Wires the monorepo's Python packages onto sys.path for pytest.

Mirrors how apps/api and services/* resolve these in-repo packages -
see docs/DECISIONS.md for why this is plain sys.path insertion rather than
editable pip installs (kept dependency-light for this build phase).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATHS = [
    REPO_ROOT / "packages" / "contracts",
    REPO_ROOT / "packages" / "permission-engine",
    REPO_ROOT / "apps" / "api",
    REPO_ROOT / "services",
    REPO_ROOT / "services" / "claude-worker",
]
for p in PACKAGE_PATHS:
    sys.path.insert(0, str(p))

# packages/contracts is imported as top-level `contracts` - its parent
# (packages/) must also be on the path since risk_levels.py does
# `from contracts.risk_levels import ...` internally in some modules.
sys.path.insert(0, str(REPO_ROOT / "packages"))
