"""WebSocket endpoint: /ws

Protocol (constitution section 15):
- client connects with a valid session cookie (same auth as REST) and an
  optional `?since_seq=N` query param to replay missed events on reconnect.
- server sends a `hello` control frame, then replays any buffered events
  with sequence > since_seq, then streams live events.
- server sends `heartbeat` frames every 20s; client is expected to
  reconnect with backoff if it misses two heartbeats.
"""
import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy.orm import Session as DbSession

from app.auth.security import parse_cookie_value, validate_session
from app.config import get_settings
from app.db import get_sessionmaker
from app.models.identity import Session as SessionModel, User
from app.realtime.bus import EventBus

router = APIRouter()

HEARTBEAT_INTERVAL_SECONDS = 20


def _authenticate_ws(cookie_header: str | None, db: DbSession) -> User | None:
    if not cookie_header:
        return None
    settings = get_settings()
    cookies = {}
    for part in cookie_header.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k] = v
    cookie_value = cookies.get(settings.session_cookie_name)
    if not cookie_value:
        return None
    parsed = parse_cookie_value(cookie_value)
    if not parsed:
        return None
    session_id, raw_token = parsed
    try:
        session = db.get(SessionModel, UUID(session_id))
    except ValueError:
        return None
    if not session or not validate_session(session, raw_token):
        return None
    return db.get(User, session.user_id)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, since_seq: int = 0):
    settings = get_settings()
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        user = _authenticate_ws(websocket.headers.get("cookie"), db)
        if user is None:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        organization_id = str(user.organization_id)
    finally:
        db.close()

    await websocket.accept()
    bus = EventBus(settings.redis_url)

    await websocket.send_text(json.dumps({"type": "hello", "organization_id": organization_id}))

    for event in bus.replay_since(organization_id, since_seq):
        await websocket.send_text(json.dumps(event))

    pubsub = bus.pubsub(organization_id)

    async def heartbeat_loop():
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await websocket.send_text(json.dumps({"type": "heartbeat"}))

    async def forward_loop():
        loop = asyncio.get_event_loop()
        while True:
            message = await loop.run_in_executor(None, pubsub.get_message, True, 1.0)
            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    forward_task = asyncio.create_task(forward_loop())
    try:
        while True:
            # Drain client->server frames (e.g. client-side acks); we don't
            # require any, but must read to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        forward_task.cancel()
        pubsub.close()
