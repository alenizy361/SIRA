"""The separate, always-available casual-chat surface (constitution: a plain
"hi" must not spin up a full CEO planning run and a multi-agent task tree).
Genuinely independent of the Goal -> Plan -> Task pipeline - no goal, no
plan, no task is ever created here. claude_worker.worker's poll loop
(_pending_chat_requests/_execute_chat_turn) picks up a posted human message
and resumes the org's one ChatSession CLI session for the reply."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.db import get_db
from app.models.chat import ChatMessage, ChatSession
from app.models.identity import User
from app.realtime.publisher import publish
from contracts.events import EntityRef, EventType

router = APIRouter(tags=["chat"])


def _get_or_create_session(db: DbSession, org_id) -> ChatSession:
    session = db.query(ChatSession).filter(ChatSession.organization_id == org_id).first()
    if session is not None:
        return session
    session = ChatSession(organization_id=org_id, created_at=datetime.now(timezone.utc))
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _serialize_message(m: ChatMessage) -> dict:
    return {
        "id": str(m.id),
        "chat_session_id": str(m.chat_session_id),
        "role": m.role,
        "body": m.body,
        "created_at": m.created_at.isoformat(),
    }


@router.get("/chat/messages")
def list_chat_messages(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)):
    session = _get_or_create_session(db, user.organization_id)
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.chat_session_id == session.id, ChatMessage.organization_id == user.organization_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return [_serialize_message(m) for m in rows]


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


@router.post("/chat/messages")
def send_chat_message(
    body: SendChatMessageRequest,
    user: User = Depends(get_current_user),
    db: DbSession = Depends(get_db),
):
    """Posts the human side of a casual-chat turn. Unlike /tasks/{id}/messages,
    there is no "no session yet" precondition: a brand-new org's first message
    always has somewhere to go, since _get_or_create_session makes the
    ChatSession right here, and claude_worker.worker's _execute_chat_turn
    handles resume_session_id=None (no prior CLI session) the same way a
    fresh task's first run does - by simply starting one."""
    session = _get_or_create_session(db, user.organization_id)

    message = ChatMessage(
        organization_id=user.organization_id, chat_session_id=session.id, role="human",
        body=body.content, created_at=datetime.now(timezone.utc),
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    publish(
        user.organization_id, EventType.CHAT_MESSAGE, str(user.id), str(session.id),
        payload={"role": "human", "body": body.content},
        entities=[EntityRef(type="chat_session", id=str(session.id))],
    )
    return _serialize_message(message)
