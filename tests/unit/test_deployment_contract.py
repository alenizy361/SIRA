"""Guards the deployment path itself.

Every bug in this file's history came from the same root cause: the code was
developed and tested by running processes directly on a dev machine, while
the thing actually shipped is a *containerized* stack plus a *host* worker
process - two layouts that were never exercised until a real VPS deploy
failed on each of them in turn (missing module in the image, missing
standalone build output, missing host venv, wrong sys.path depth).

These tests assert the contract between the source tree and the deployment
artifacts, so a future change that breaks it fails here in seconds instead
of failing on someone's server. They are deliberately static (parsing the
Dockerfiles/scripts) so they run anywhere, with no Docker daemon required.
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DOCKERFILE = REPO_ROOT / "infra" / "docker" / "api.Dockerfile"
WEB_DOCKERFILE = REPO_ROOT / "infra" / "docker" / "web.Dockerfile"


def _copied_sources(dockerfile: Path) -> list[str]:
    """Source paths of every COPY that comes from the build context (i.e. not
    --from=<stage>)."""
    out = []
    for line in dockerfile.read_text().splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        if "--from=" in line:
            continue
        parts = line.split()[1:]
        if len(parts) >= 2:
            out.extend(parts[:-1])
    return out


# --- api image contract ----------------------------------------------------

@pytest.mark.parametrize(
    "required",
    ["packages", "services/orchestrator", "services/claude-worker", "apps/api"],
)
def test_api_image_copies_every_source_tree_it_imports(required):
    """apps/api imports across the monorepo (app.services.planning imports
    claude_worker.cli_adapter, routers import orchestrator, everything imports
    contracts/permission_engine). Anything it imports must be COPYed into the
    image - omitting services/claude-worker crash-looped the real deployment
    with ModuleNotFoundError."""
    copied = _copied_sources(API_DOCKERFILE)
    assert any(c.rstrip("/") == required for c in copied), (
        f"{required} is imported by apps/api but never COPYed in api.Dockerfile "
        f"(COPY sources found: {copied})"
    )


@pytest.mark.parametrize(
    "required",
    ["/app/apps/api", "/app/packages", "/app/services", "/app/services/claude-worker"],
)
def test_api_image_pythonpath_covers_every_import_root(required):
    text = API_DOCKERFILE.read_text()
    m = re.search(r"ENV\s+PYTHONPATH=(\S+)", text)
    assert m, "api.Dockerfile must set PYTHONPATH"
    roots = m.group(1).split(":")
    assert required in roots, f"PYTHONPATH is missing {required} (has: {roots})"


def test_api_dockerfile_pythonpath_entries_all_exist_in_repo():
    """Each PYTHONPATH entry maps to a real directory in the source tree
    (translating the image's /app prefix back to the repo root)."""
    text = API_DOCKERFILE.read_text()
    roots = re.search(r"ENV\s+PYTHONPATH=(\S+)", text).group(1).split(":")
    for r in roots:
        rel = r.replace("/app/", "", 1)
        assert (REPO_ROOT / rel).is_dir(), f"PYTHONPATH entry {r} -> {rel} does not exist in the repo"


# --- web image contract ----------------------------------------------------

def test_web_image_requires_standalone_output_and_config_enables_it():
    """web.Dockerfile's runner stage copies .next/standalone, which Next only
    emits when next.config sets output:'standalone'. Without it the build
    fails at COPY time ("/app/.next/standalone: not found")."""
    dockerfile = WEB_DOCKERFILE.read_text()
    if ".next/standalone" not in dockerfile:
        pytest.skip("web.Dockerfile no longer uses the standalone output")
    config = (REPO_ROOT / "apps" / "web" / "next.config.ts").read_text()
    assert re.search(r"output\s*:\s*[\"']standalone[\"']", config), (
        "web.Dockerfile copies .next/standalone but apps/web/next.config.ts does not set "
        "output: 'standalone', so that directory is never produced"
    )


# --- host worker contract --------------------------------------------------

def test_worker_run_script_and_installer_agree_on_the_host_venv():
    """The claude-worker runs on the host (not in Docker) because it needs the
    aicompany user's `claude` CLI session. run.sh therefore depends on a host
    venv - install.sh must actually create it."""
    run_sh = (REPO_ROOT / "services" / "claude-worker" / "run.sh").read_text()
    install_sh = (REPO_ROOT / "scripts" / "install.sh").read_text()
    assert ".venv" in run_sh, "run.sh no longer references a venv - update this test"
    assert re.search(r"-m\s+venv", install_sh), (
        "run.sh requires $APP_ROOT/.venv but install.sh never creates a venv - "
        "rabit-claude-worker.service cannot start"
    )
    assert "requirements.txt" in install_sh, (
        "install.sh creates a venv but never installs the worker's dependencies into it"
    )


def test_worker_run_script_puts_every_import_root_on_pythonpath():
    run_sh = (REPO_ROOT / "services" / "claude-worker" / "run.sh").read_text()
    for required in ["apps/api", "packages", "services", "services/claude-worker"]:
        assert required in run_sh, (
            f"run.sh's PYTHONPATH is missing {required}; `python -m claude_worker.worker` "
            "will fail to resolve its imports on the host"
        )


# --- sys.path depth contract ----------------------------------------------

def test_every_sys_path_parent_computation_resolves_to_a_real_directory():
    """Several modules bootstrap sys.path with Path(__file__).parents[N]. An
    off-by-one there is invisible in dev (PYTHONPATH masks it) but is a live
    landmine - publisher.py and bus.py both pointed at a nonexistent
    apps/packages until this was caught."""
    bad = []
    pattern = re.compile(r"parents\[(\d+)\]\s*(?:/\s*[\"']([^\"']+)[\"'])?")
    for py in REPO_ROOT.glob("**/*.py"):
        if any(part in py.parts for part in (".venv", "node_modules", "__pycache__", "tests", "alembic")):
            continue
        for line in py.read_text(errors="ignore").splitlines():
            if "parents[" not in line or "Path(__file__)" not in line:
                continue
            m = pattern.search(line)
            if not m:
                continue
            depth = int(m.group(1))
            suffix = m.group(2)
            try:
                resolved = py.resolve().parents[depth]
            except IndexError:
                bad.append(f"{py.relative_to(REPO_ROOT)}: parents[{depth}] exceeds path depth")
                continue
            if suffix:
                resolved = resolved.joinpath(*suffix.split("/"))
            if not resolved.exists():
                bad.append(f"{py.relative_to(REPO_ROOT)}: parents[{depth}]"
                           f"{'/' + suffix if suffix else ''} -> {resolved} (does not exist)")
    assert not bad, "sys.path bootstrap paths that do not resolve:\n" + "\n".join(bad)

def _run_repair_env(tmp_path, env_text: str) -> str:
    """Executes install.sh's real repair_env_urls() against a throwaway .env."""
    import subprocess

    (tmp_path / ".env").write_text(env_text)
    script = f'''
set -u
APP_ROOT="{tmp_path}"
AICOMPANY_USER="$(id -un)"
log_warn(){{ :; }}; log_ok(){{ :; }}
eval "$(sed -n '/^repair_env_urls() {{/,/^}}/p' "{REPO_ROOT}/scripts/install.sh")"
repair_env_urls
'''
    subprocess.run(["bash", "-c", script], check=True, capture_output=True)
    return (tmp_path / ".env").read_text()


def test_installer_repairs_a_corrupt_database_url(tmp_path):
    """Regression for the worker crash-loop
    `ArgumentError: Could not parse SQLAlchemy URL from string '<hex>'`.

    An older installer replaced the CHANGE_ME token wholesale, turning
    DATABASE_URL into a bare secret. generate_env never overwrites an existing
    .env, so that damage survived every re-install and the worker could never
    connect. repair_env_urls must rebuild the URL from the sibling POSTGRES_*
    values (which match the live container by construction)."""
    from sqlalchemy.engine.url import make_url

    out = _run_repair_env(
        tmp_path,
        "POSTGRES_USER=rabit\n"
        "POSTGRES_PASSWORD=deadbeefcafe\n"
        "POSTGRES_DB=rabit_os\n"
        "DATABASE_URL=ba4068687a63926d2684a98f67c7d6d63d56d9bf4b7b1753b283af0a576fb81a\n"
        "REDIS_URL=redis://localhost:6379/0\n",
    )
    url = next(l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith("DATABASE_URL="))
    parsed = make_url(url)  # must not raise
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.host == "localhost" and parsed.database == "rabit_os"
    # Must reuse the password the Postgres container was initialized with.
    assert parsed.password == "deadbeefcafe"


def test_installer_never_rewrites_a_valid_env(tmp_path):
    """A valid (possibly hand-customized) .env must be left byte-for-byte
    alone - repair only ever touches a value that is not a URL."""
    original = (
        "POSTGRES_USER=rabit\n"
        "POSTGRES_PASSWORD=pw\n"
        "DATABASE_URL=postgresql+psycopg://custom:pw@db.internal:6543/other\n"
        "REDIS_URL=redis://cache.internal:6379/3\n"
    )
    assert _run_repair_env(tmp_path, original) == original
