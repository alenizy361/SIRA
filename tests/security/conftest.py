import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://rabit:rabit_dev_local_only@localhost:5432/rabit_os_test"
)

for p in [
    REPO_ROOT / "apps" / "api",
    REPO_ROOT / "packages",
    REPO_ROOT / "packages" / "permission-engine",
    REPO_ROOT / "services",
    REPO_ROOT / "services" / "claude-worker",
]:
    sys.path.insert(0, str(p))
