"""Application settings.

All secrets come from environment variables (see .env.example at repo root).
Nothing here is a real credential - CHANGE_ME placeholders are rejected by
scripts/install.sh which generates real values with `openssl rand`.
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _readable_env_file() -> str | None:
    """Point pydantic at the repo-root .env ONLY when this process can read it.

    The host claude-worker runs as the non-root `aicompany` user, while .env is
    intentionally locked to root (it holds the DB/session secrets). systemd's
    `EnvironmentFile=` already injects those values into the worker's
    environment, so the file itself is redundant there - but pydantic-settings
    would still try to OPEN it and crash the entire process with
    `PermissionError: [Errno 13] Permission denied: '.env'` (the real cause of
    the worker's status=1/FAILURE crash-loop). Only hand pydantic the path when
    the file is present AND readable; otherwise fall back to the real
    environment, which already carries every value it needs.
    """
    path = Path(".env")
    try:
        if path.is_file() and os.access(path, os.R_OK):
            return str(path)
    except OSError:
        pass
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_readable_env_file(), extra="ignore")

    environment: str = "development"
    organization_default_currency: str = "SAR"
    organization_default_timezone: str = "Asia/Riyadh"

    database_url: str = "postgresql+psycopg://rabit:rabit@localhost:5432/rabit_os"
    redis_url: str = "redis://localhost:6379/0"

    session_secret_key: str = "CHANGE_ME"
    session_cookie_name: str = "rabit_session"
    session_ttl_seconds: int = 60 * 60 * 12
    # Whether the session cookie carries the Secure flag. Left unset it derives
    # from `environment` (Secure everywhere except development), but a
    # deployment can force it on explicitly - important because a production
    # .env that forgot to set environment=production would otherwise ship the
    # sole admin cookie without Secure.
    session_cookie_secure: bool | None = None

    access_lockout_max_attempts: int = 5
    access_lockout_window_seconds: int = 15 * 60

    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def cookie_secure(self) -> bool:
        """Resolved Secure flag for the session cookie."""
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.environment != "development"

    agent_specs_dir: str = "packages/agent-specs"

    claude_cli_path: str = "claude"
    claude_worker_workspace_root: str = "workspace"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except PermissionError:
        # Final safety net for the host worker: if a .env becomes unreadable
        # between import and construction (or cwd differs from where the
        # readability probe ran), never take the whole process down - systemd's
        # EnvironmentFile= has already injected every value we need.
        return Settings(_env_file=None)
