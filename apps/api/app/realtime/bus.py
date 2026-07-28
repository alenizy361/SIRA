"""Realtime event bus: Redis-backed pub/sub + bounded replay log.

Implements the section-15 dashboard protocol requirements that matter most
for a single-VPS deployment: sequence numbers (per organization), event
replay after reconnect, and a payload shape that can never leak secrets
(enforced via contracts.events.FORBIDDEN_PAYLOAD_KEYS).
"""
import json
import sys
import uuid
from pathlib import Path

import redis

# parents[4] is the repo root (apps/api/app/realtime/bus.py -> [3]=apps,
# [4]=<repo root>). See publisher.py for why the earlier parents[3] was wrong
# but harmless.
_PACKAGES_ROOT = Path(__file__).resolve().parents[4] / "packages"
if str(_PACKAGES_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_ROOT))

from contracts.events import Event, FORBIDDEN_PAYLOAD_KEYS  # noqa: E402

REPLAY_LOG_MAX_LEN = 2000


class EventBus:
    def __init__(self, redis_url: str) -> None:
        # Bounded timeouts so a STALLED Redis (as opposed to one refusing
        # connections) can never block a caller forever - critical on the async
        # /ws path where a blocked call would freeze the whole event loop.
        self._redis = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            health_check_interval=30,
        )

    def _channel(self, organization_id: str) -> str:
        return f"rabit:events:{organization_id}"

    def _seq_key(self, organization_id: str) -> str:
        return f"rabit:events:seq:{organization_id}"

    def _replay_key(self, organization_id: str) -> str:
        return f"rabit:events:replay:{organization_id}"

    def _epoch_key(self, organization_id: str) -> str:
        return f"rabit:events:epoch:{organization_id}"

    def epoch(self, organization_id: str) -> str:
        """A stable id for the current sequence-counter generation. If Redis
        loses its keyspace (OOM/restart without persistence), the seq counter
        rewinds; regenerating the epoch when it's absent lets clients detect
        the rewind and reset their high-water mark instead of muting forever."""
        key = self._epoch_key(organization_id)
        existing = self._redis.get(key)
        if existing:
            return existing
        new = uuid.uuid4().hex
        # SET NX: first caller wins, everyone reads the same value.
        self._redis.set(key, new, nx=True)
        return self._redis.get(key) or new

    def _assert_safe_payload(self, payload) -> None:
        """Recursively reject any forbidden key (e.g. private_key) at ANY depth,
        not just the top level - run.tool.* payloads nest tool input/output."""
        def walk(value) -> None:
            if isinstance(value, dict):
                leaked = {k.lower() for k in value.keys()} & FORBIDDEN_PAYLOAD_KEYS
                if leaked:
                    raise ValueError(f"Refusing to publish event with forbidden payload keys: {leaked}")
                for v in value.values():
                    walk(v)
            elif isinstance(value, list):
                for v in value:
                    walk(v)

        walk(payload)

    def next_sequence(self, organization_id: str) -> int:
        return self._redis.incr(self._seq_key(organization_id))

    def publish(self, event: Event) -> None:
        self._assert_safe_payload(event.payload)
        wire = event.to_wire()
        data = json.dumps(wire)
        pipe = self._redis.pipeline()
        pipe.rpush(self._replay_key(event.organization_id), data)
        pipe.ltrim(self._replay_key(event.organization_id), -REPLAY_LOG_MAX_LEN, -1)
        pipe.publish(self._channel(event.organization_id), data)
        pipe.execute()

    def replay_since(self, organization_id: str, since_sequence: int) -> list[dict]:
        raw_events = self._redis.lrange(self._replay_key(organization_id), 0, -1)
        events = [json.loads(e) for e in raw_events]
        return [e for e in events if e["sequence"] > since_sequence]

    def pubsub(self, organization_id: str):
        p = self._redis.pubsub()
        p.subscribe(self._channel(organization_id))
        return p
