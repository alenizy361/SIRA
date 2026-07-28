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


def test_installer_verifies_docker_daemon_not_just_the_package():
    """Regression: `systemctl enable --now docker || true` silently swallowed a
    daemon that never actually started, so install.sh barreled ahead for a
    dozen more steps and only failed much later at `docker compose up` with
    "Cannot connect to the Docker daemon" - a real reported symptom that gave
    no hint the actual cause was here. The installer must probe the daemon
    itself (docker info) right after enabling it, not just trust the unit
    "enabled" state."""
    install_sh = (REPO_ROOT / "scripts" / "install.sh").read_text()
    assert "wait_for_docker_daemon" in install_sh, (
        "install_docker no longer verifies the daemon actually answers before continuing"
    )
    assert re.search(r"docker info", install_sh), (
        "no `docker info` probe found - the installer can't tell an enabled unit from a dead daemon"
    )


def test_installer_auto_installs_the_claude_cli():
    """Regression: a fresh host needed a SECOND manual command
    (npm install -g @anthropic-ai/claude-code) before the worker could even
    attempt `claude auth login`. Node is already installed by this same
    script and a global npm install needs no interaction, so only the
    interactive login itself should remain manual."""
    install_sh = (REPO_ROOT / "scripts" / "install.sh").read_text()
    assert "install_claude_cli" in install_sh
    assert "npm install -g @anthropic-ai/claude-code" in install_sh
    # Must actually be called from the main install path, not just defined.
    assert re.search(r"^\s*install_claude_cli\s*$", install_sh, re.MULTILINE), (
        "install_claude_cli is defined but never invoked from main()"
    )


def test_expose_remote_checks_local_health_before_installing_anything():
    """Tunneling a dead local service just hands remote visitors a working
    HTTPS connection to nothing - the script must verify the company is
    actually running BEFORE touching apt/cloudflared."""
    script = (REPO_ROOT / "scripts" / "expose-remote.sh").read_text()
    assert "health/live" in script
    # The health check must appear before cloudflared installation begins.
    check_pos = script.index("health/live")
    install_pos = script.index("Installing cloudflared")
    assert check_pos < install_pos


def test_expose_remote_tunnel_is_a_restart_always_systemd_service():
    """The tunnel must survive crashes without any start-rate limit latching
    it off - the same class of bug fixed earlier in rabit-claude-worker.service."""
    script = (REPO_ROOT / "scripts" / "expose-remote.sh").read_text()
    assert "Restart=always" in script
    assert "StartLimitIntervalSec=0" in script


def test_expose_remote_url_extraction_regex_matches_real_cloudflared_output():
    """Regression check for the exact quick-tunnel banner cloudflared prints -
    if the format ever drifts this must be updated, not silently return
    nothing."""
    import re

    script = (REPO_ROOT / "scripts" / "expose-remote.sh").read_text()
    m = re.search(r"grep -oE '([^']+trycloudflare[^']+)'", script)
    assert m, "no trycloudflare.com extraction regex found"
    pattern = m.group(1)
    sample = (
        "2026-07-28T20:15:03Z INF |  https://random-adjective-noun-42.trycloudflare.com"
        "                                          |\n"
    )
    found = re.findall(pattern, sample)
    assert found and found[-1] == "https://random-adjective-noun-42.trycloudflare.com"


def test_enable_phone_terminal_targets_the_invoking_user_not_root():
    """SSH guidance must point at the human's own Linux account
    ($SUDO_USER, set by sudo itself regardless of what it executes), never
    at root - suggesting `ssh root@...` would be actively bad security advice."""
    script = (REPO_ROOT / "scripts" / "enable-phone-terminal.sh").read_text()
    assert "SUDO_USER" in script
    assert "ssh ${TARGET_USER}@" in script


def test_enable_phone_terminal_installs_ssh_server_before_tailscale():
    """Tailscale's SSH feature still needs a real SSH server for password/key
    auth; openssh-server must be installed first so the machine is at least
    reachable on the LAN even before Tailscale is configured."""
    script = (REPO_ROOT / "scripts" / "enable-phone-terminal.sh").read_text()
    ssh_pos = script.index("openssh-server")
    tailscale_pos = script.index("Installing Tailscale")
    assert ssh_pos < tailscale_pos


def test_enable_phone_terminal_ssh_install_is_noninteractive():
    """A bare `apt-get install openssh-server` can hit a debconf prompt (e.g.
    on a re-run with a modified sshd_config) and hang forever with no TTY to
    answer it - DEBIAN_FRONTEND=noninteractive must be set."""
    script = (REPO_ROOT / "scripts" / "enable-phone-terminal.sh").read_text()
    assert "DEBIAN_FRONTEND=noninteractive" in script
