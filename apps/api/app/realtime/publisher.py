"""Single place that turns a domain happening into a published realtime
event. Used by API routers (apps/api/app/routers/*.py) and by
services/claude-worker (which already imports app.* directly throughout
this monorepo - see worker.py) so both sides of "the dashboard reacts to
real backend activity" go through one sequence-numbered, Redis-backed path
instead of hand-rolling Event construction in three different places.
"""
import sys
from pathlib import Path
from typing import Optional

# parents[4] is the repo root: this file is apps/api/app/realtime/publisher.py,
# so [0]=realtime [1]=app [2]=api [3]=apps [4]=<repo root>. An earlier
# parents[3] here silently pointed at "apps/packages" (which does not exist);
# it went unnoticed only because PYTHONPATH already contained packages/, so
# the import worked anyway and the broken sys.path entry was inert.
_PACKAGES_ROOT = Path(__file__).resolve().parents[4] / "packages"
if str(_PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_ROOT))

from contracts.events import CoreState, EntityRef, Event, EventType  # noqa: E402

from app.config import get_settings
from app.realtime.bus import EventBus

_bus: Optional[EventBus] = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(get_settings().redis_url)
    return _bus


def publish(
    organization_id,
    event_type: EventType,
    actor: str,
    correlation_id: str,
    payload: Optional[dict] = None,
    entities: Optional[list[EntityRef]] = None,
) -> None:
    """organization_id may be a str or uuid.UUID - always stringified before
    it reaches the wire/Redis key so callers don't need to care."""
    bus = get_bus()
    org_id_str = str(organization_id)
    event = Event(
        sequence=bus.next_sequence(org_id_str),
        type=event_type,
        organization_id=org_id_str,
        correlation_id=correlation_id,
        actor=actor,
        entities=entities or [],
        payload=payload or {},
    )
    bus.publish(event)


def publish_core_state(organization_id, state: CoreState, actor: str, correlation_id: str) -> None:
    publish(organization_id, EventType.CORE_STATE_CHANGED, actor, correlation_id, payload={"state": state.value})
