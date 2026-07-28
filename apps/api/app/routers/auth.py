from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.auth.security import (
    create_session,
    hash_password,
    is_locked_out,
    register_failed_login,
    register_successful_login,
    revoke_session,
    verify_password,
    parse_cookie_value,
    validate_session,
)
from app.config import get_settings
from app.db import get_db
from app.models.identity import Organization, Session as SessionModel, User

router = APIRouter(prefix="/auth", tags=["auth"])


class OnboardRequest(BaseModel):
    # Bounded to the DB column widths (String(200)) so an over-long value is a
    # clean 422, never a mid-transaction StringDataRightTruncation that could
    # brick bootstrap after the org row is already durable.
    organization_name: str = Field(min_length=1, max_length=200)
    admin_email: EmailStr
    admin_password: str
    admin_display_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/onboard", status_code=status.HTTP_201_CREATED)
def onboard(payload: OnboardRequest, response: Response, request: Request, db: DbSession = Depends(get_db)):
    """First-run administrator onboarding. Refuses to run if ANY organization
    already exists - this is a one-time bootstrap endpoint, not a general
    signup flow (constitution: secure administrator onboarding)."""
    if db.query(Organization).first() is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Onboarding already completed")

    if len(payload.admin_password) < 12:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 12 characters")

    settings = get_settings()
    # Org + admin in ONE transaction: a failure creating the user must not
    # leave a durable org with zero users (which the guard above would then
    # treat as "onboarding done" forever, bricking bootstrap).
    org = Organization(
        name=payload.organization_name,
        default_currency=settings.organization_default_currency,
        default_timezone=settings.organization_default_timezone,
    )
    db.add(org)
    db.flush()  # assign org.id without committing

    user = User(
        organization_id=org.id,
        email=payload.admin_email,
        password_hash=hash_password(payload.admin_password),
        display_name=payload.admin_display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(org)
    db.refresh(user)

    cookie_value, _ = create_session(user, db, request.client.host if request.client else None, request.headers.get("user-agent"))
    response.set_cookie(
        settings.session_cookie_name,
        cookie_value,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
    )
    return {"organization_id": str(org.id), "user_id": str(user.id)}


@router.post("/login")
def login(payload: LoginRequest, response: Response, request: Request, db: DbSession = Depends(get_db)):
    # Deterministic under any duplicate email (e.g. a bootstrap-race that
    # created two orgs): pick the earliest rather than raising
    # MultipleResultsFound, which would 500 every login permanently.
    user = (
        db.query(User)
        .filter(User.email == payload.email)
        .order_by(User.created_at.asc())
        .first()
    )
    generic_error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if user is None:
        raise generic_error
    if is_locked_out(user):
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail="Account temporarily locked due to failed login attempts")
    if not verify_password(payload.password, user.password_hash):
        register_failed_login(user, db)
        raise generic_error

    register_successful_login(user, db)
    settings = get_settings()
    cookie_value, _ = create_session(user, db, request.client.host if request.client else None, request.headers.get("user-agent"))
    response.set_cookie(
        settings.session_cookie_name,
        cookie_value,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_seconds,
    )
    return {"user_id": str(user.id)}


@router.post("/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(get_db)):
    settings = get_settings()
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if cookie_value:
        parsed = parse_cookie_value(cookie_value)
        if parsed:
            session_id, raw_token = parsed
            try:
                session = db.get(SessionModel, session_id)
            except Exception:  # noqa: BLE001
                session = None
            if session and validate_session(session, raw_token):
                revoke_session(session, db)
    response.delete_cookie(settings.session_cookie_name)
    return {"status": "logged_out"}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "organization_id": str(user.organization_id),
        "email": user.email,
        "display_name": user.display_name,
        "totp_enabled": user.totp_enabled,
    }
