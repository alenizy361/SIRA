"""Regression tests for the host-worker crash-loop:
`PermissionError: [Errno 13] Permission denied: '.env'`.

The worker runs as the non-root `aicompany` user while .env is locked to root
(it holds secrets). systemd injects .env's values via EnvironmentFile=, so the
worker doesn't need the file - but pydantic-settings used to try to OPEN it and
crash the whole process. app.config._readable_env_file must therefore point
pydantic at .env only when it is actually readable, and Settings must still
load from the plain environment when it is not.
"""
import os

import pytest


def test_readable_env_file_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from app.config import _readable_env_file

    assert _readable_env_file() is None


def test_readable_env_file_uses_readable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("SESSION_SECRET_KEY=abc\n")
    from app.config import _readable_env_file

    assert _readable_env_file() == ".env"


def test_readable_env_file_skips_unreadable(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions; cannot simulate PermissionError")
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql+psycopg://u:p@localhost:5432/db\n")
    env.chmod(0o000)
    from app.config import _readable_env_file

    try:
        # Must NOT return the path (which would make pydantic crash); the
        # worker falls back to the injected environment instead.
        assert _readable_env_file() is None
    finally:
        env.chmod(0o600)  # let tmp cleanup remove it


def test_settings_load_from_environment_without_env_file(tmp_path, monkeypatch):
    """The worker's real situation: no readable .env in cwd, every value present
    in the environment (systemd EnvironmentFile injected them). Settings must
    construct cleanly and pick those values up."""
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/rabit_os")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/2")
    from app.config import Settings

    s = Settings(_env_file=None)  # mirror an unreadable/absent .env
    assert s.database_url.endswith("/rabit_os")
    assert s.redis_url.endswith("/2")
