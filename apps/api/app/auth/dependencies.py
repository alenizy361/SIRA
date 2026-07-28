from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.auth.security import parse_cookie_value, validate_session
from app.config import get_settings
from app.db import get_db
from app.logging_config import bind_context
from app.models.identity import Session as SessionModel, User


def get_current_user(
    request: Request,
    db: DbSession = Depends(get_db),
) -> User:
    settings = get_settings()
    cookie_value = request.cookies.get(settings.session_cookie_name)
    unauthorized = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not cookie_value:
        raise unauthorized

    parsed = parse_cookie_value(cookie_value)
    if not parsed:
        raise unauthorized
    session_id, raw_token = parsed

    try:
        session_uuid = UUID(session_id)
    except ValueError:
        raise unauthorized

    session = db.get(SessionModel, session_uuid)
    if not session or not validate_session(session, raw_token):
        raise unauthorized

    user = db.get(User, session.user_id)
    if not user or not user.is_active:
        raise unauthorized

    bind_context(actor=str(user.id), organization_id=str(user.organization_id))
    return user


def require_permission(permission: str):
    """Dependency factory: require_permission('approvals.resolve')."""

    def _check(user: User = Depends(get_current_user), db: DbSession = Depends(get_db)) -> User:
        from app.models.identity import Role, UserRole  # local import avoids cycle

        role_ids = [ur.role_id for ur in db.query(UserRole).filter(UserRole.user_id == user.id).all()]
        roles = db.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []
        granted = set()
        for role in roles:
            granted.update(role.permissions or [])
        if permission not in granted and "*" not in granted:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")
        return user

    return _check
