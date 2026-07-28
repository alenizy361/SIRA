"""Application settings.

All secrets come from environment variables (see .env.example at repo root).
Nothing here is a real credential - CHANGE_ME placeholders are rejected by
scripts/install.sh which generates real values with `openssl rand`.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    organization_default_currency: str = "SAR"
    organization_default_timezone: str = "Asia/Riyadh"

    database_url: str = "postgresql+psycopg://rabit:rabit@localhost:5432/rabit_os"
    redis_url: str = "redis://localhost:6379/0"

    session_secret_key: str = "CHANGE_ME"
    session_cookie_name: str = "rabit_session"
    session_ttl_seconds: int = 60 * 60 * 12

    access_lockout_max_attempts: int = 5
    access_lockout_window_seconds: int = 15 * 60

    cors_allowed_origins: str = "http://localhost:3000"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    agent_specs_dir: str = "packages/agent-specs"

    claude_cli_path: str = "claude"
    claude_worker_workspace_root: str = "workspace"

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
