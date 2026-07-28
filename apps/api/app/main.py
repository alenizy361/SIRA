import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "packages"))

from app.agents.loader import AgentSpecError, load_agent_specs
from app.config import get_settings
from app.db import get_sessionmaker
from app.logging_config import configure_logging
from app.models.agents import AgentDefinition
from app.realtime.ws_router import router as ws_router
from app.routers import agents, approvals, audit, auth, chat_views, goals, health, reliability_views, work_views

import logging

logger = logging.getLogger("rabit_api")


def _seed_agent_specs(settings) -> None:
    """Constitution section 8: "Validate them at startup." A malformed
    spec must fail loudly - never silently skip an invalid agent
    contract."""
    specs_dir = _REPO_ROOT / settings.agent_specs_dir
    try:
        specs = load_agent_specs(specs_dir)
    except AgentSpecError as exc:
        logger.error("Agent-spec validation failed at startup: %s", exc)
        raise

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        existing = {d.agent_key: d for d in db.query(AgentDefinition).all()}
        for key, spec in specs.items():
            if key in existing:
                definition = existing[key]
                definition.display_name_en = spec["display_name"]["en"]
                definition.display_name_ar = spec["display_name"]["ar"]
                definition.mission = spec["mission"]
                definition.risk_ceiling = spec["risk_ceiling"]
                definition.spec_json = spec
            else:
                definition = AgentDefinition(
                    agent_key=key,
                    display_name_en=spec["display_name"]["en"],
                    display_name_ar=spec["display_name"]["ar"],
                    mission=spec["mission"],
                    risk_ceiling=spec["risk_ceiling"],
                    spec_json=spec,
                )
            db.add(definition)
        db.commit()
        logger.info("Loaded and seeded %d agent definitions", len(specs))
    finally:
        db.close()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _seed_agent_specs(get_settings())
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="Rabit AI Company OS API", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(goals.router)
    app.include_router(approvals.router)
    app.include_router(audit.router)
    app.include_router(work_views.router)
    app.include_router(chat_views.router)
    app.include_router(reliability_views.router)
    app.include_router(ws_router)

    return app


app = create_app()
