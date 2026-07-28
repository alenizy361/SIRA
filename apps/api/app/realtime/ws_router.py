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
from concurrent.futures import ThreadPoolExecutor
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
    user = db.get(User, session.user_id)
    # Mirror the REST dependency (get_current_user): a deactivated account with
    # a still-valid cookie must not keep streaming the org's realtime feed.
    if user is None or not user.is_active:
        return None
    return user


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

    # A dedicated single-thread executor for this connection's BLOCKING Redis
    # calls, so nothing here (subscribe, replay LRANGE, epoch GET, the pubsub
    # poll) ever runs synchronously on the event loop and freezes the whole
    # single-process API, nor occupies asyncio's shared default executor.
    poll_executor = ThreadPoolExecutor(max_workers=1)
    loop = asyncio.get_event_loop()

    async def run_blocking(fn, *args):
        return await loop.run_in_executor(poll_executor, fn, *args)

    try:
        epoch = await run_blocking(bus.epoch, organization_id)
        # hello carries the epoch: if it differs from what the client last saw,
        # the client resets its high-water mark (its since_seq is meaningless
        # after a Redis counter rewind).
        await websocket.send_text(json.dumps({"type": "hello", "organization_id": organization_id, "epoch": epoch}))

        # Subscribe to the live channel BEFORE reading the replay slice so an
        # event published in that window is not lost (it arrives on the live
        # channel; we de-dup against the replay slice's max sequence).
        pubsub = await run_blocking(bus.pubsub, organization_id)

        replay_max = since_seq
        for event in await run_blocking(bus.replay_since, organization_id, since_seq):
            await websocket.send_text(json.dumps(event))
            seq = event.get("sequence")
            if isinstance(seq, int) and seq > replay_max:
                replay_max = seq
    except Exception:  # noqa: BLE001 - Redis unreachable at connect: fail the socket cleanly
        poll_executor.shutdown(wait=False)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    async def heartbeat_loop():
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await websocket.send_text(json.dumps({"type": "heartbeat"}))

    async def forward_loop():
        # Live events are de-duped ONLY against the replay slice (replay_max),
        # never against a running max - so a concurrently-published lower-seq
        # event that arrives slightly out of order is still forwarded once
        # (the client de-dups by event_id). A single Redis error must not
        # silently kill this loop while heartbeats keep the socket "alive": on
        # error we close the socket so the client reconnects and replays.
        while True:
            message = await run_blocking(pubsub.get_message, True, 1.0)
            if not message or message.get("type") != "message":
                continue
            data = message["data"]
            try:
                seq = json.loads(data).get("sequence")
            except (ValueError, TypeError):
                seq = None
            if isinstance(seq, int) and seq <= replay_max:
                continue  # already delivered in the replay slice
            await websocket.send_text(data)

    heartbeat_task = asyncio.create_task(heartbeat_loop())
    forward_task = asyncio.create_task(forward_loop())
    try:
        # If the forwarder dies (e.g. Redis blip) close the socket rather than
        # sitting on a dead feed behind live heartbeats. Wait on either the
        # client's receive loop OR the forwarder ending.
        async def receive_loop():
            while True:
                await websocket.receive_text()

        receive_task = asyncio.create_task(receive_loop())
        await asyncio.wait(
            {receive_task, forward_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        receive_task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        forward_task.cancel()
        try:
            pubsub.close()
        except Exception:  # noqa: BLE001
            pass
        poll_executor.shutdown(wait=False)
