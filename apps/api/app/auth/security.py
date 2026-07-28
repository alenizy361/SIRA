"""Password hashing, session tokens, and login-attempt lockout.

Argon2id via passlib (constitution section 16: "password hashing using a
modern supported algorithm"). Sessions are server-side (app.models.identity.Session)
referenced by an opaque, high-entropy cookie value - not a JWT - so they can
be revoked immediately (constitution: "secure logout and session revocation").
"""
import secrets
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models.identity import Session as SessionModel, User

_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def generate_session_token() -> str:
    return secrets.token_urlsafe(48)


def is_locked_out(user: User) -> bool:
    if user.locked_until is None:
        return False
    return datetime.now(timezone.utc) < user.locked_until


def register_failed_login(user: User, db: DbSession) -> None:
    settings = get_settings()
    user.failed_login_count += 1
    if user.failed_login_count >= settings.access_lockout_max_attempts:
        user.locked_until = datetime.now(timezone.utc) + timedelta(
            seconds=settings.access_lockout_window_seconds
        )
    db.add(user)
    db.commit()


def register_successful_login(user: User, db: DbSession) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()


def create_session(user: User, db: DbSession, ip_address: str | None, user_agent: str | None) -> tuple[str, SessionModel]:
    settings = get_settings()
    token = generate_session_token()
    # Session.id doubles as the cookie value's lookup key - we store a hash,
    # never the raw token, in case of DB read exposure.
    token_hash = _pwd_context.hash(token)
    session = SessionModel(
        user_id=user.id,
        organization_id=user.organization_id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds),
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    # The cookie value encodes session id + raw token; DB stores only the hash.
    return f"{session.id}.{token}", session


def parse_cookie_value(cookie_value: str) -> tuple[str, str] | None:
    if "." not in cookie_value:
        return None
    session_id, _, raw_token = cookie_value.partition(".")
    if not session_id or not raw_token:
        return None
    return session_id, raw_token


def validate_session(session: SessionModel, raw_token: str) -> bool:
    if session.revoked_at is not None:
        return False
    if datetime.now(timezone.utc) >= session.expires_at:
        return False
    return _pwd_context.verify(raw_token, session.token_hash)


def revoke_session(session: SessionModel, db: DbSession) -> None:
    session.revoked_at = datetime.now(timezone.utc)
    db.add(session)
    db.commit()
