"""Import every model module so Base.metadata is complete for Alembic
autogenerate and for `Base.metadata.create_all()` in tests."""
from app.db import Base  # noqa: F401
from app.models import (  # noqa: F401
    agents,
    chat,
    company,
    governance,
    identity,
    integrations,
    memory,
    reliability,
    work,
)
