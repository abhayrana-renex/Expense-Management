"""Login (rate limit + lockout), refresh rotation, logout, password policy and history."""

import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.context import get_request_context
from app.core.errors import AppError
from app.core.rate_limit import limiter
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    new_refresh_token,
    sha256_hex,
    utcnow,
    verify_password,
)
from app.models import AuthSession, PasswordHistory, User
from app.services import audit_service


def _token_response(db: Session, user: User, session: AuthSession, refresh: str) -> dict:
    access, expires_in = create_access_token(user.id, session.id)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
        "must_change_password": user.must_change_password,
    }


def _new_session(db: Session, user: User) -> tuple[AuthSession, str]:
    ctx = get_request_context()
    refresh, refresh_hash = new_refresh_token()
    now = utcnow()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=refresh_hash,
        last_used_at=now,
        expires_at=now + timedelta(hours=settings.REFRESH_TOKEN_EXPIRE_HOURS),
        ip_address=ctx.ip_address,
        user_agent=(ctx.user_agent or "")[:255] or None,
    )
    db.add(session)
    db.flush()
    return session, refresh


def login(db: Session, username: str, password: str) -> dict:
    ctx = get_request_context()
    if not limiter.hit(f"login-ip:{ctx.ip_address}", settings.LOGIN_RATE_LIMIT_PER_MINUTE):
        raise AppError(429, "RATE_LIMITED", "Too many login attempts; wait a minute")

    username = username.strip().lower()
    user = (
        db.execute(
            select(User).where(func.lower(User.username) == username).with_for_update(of=User)
        )
        .unique()
        .scalar_one_or_none()
    )
    now = utcnow()

    if user is None:
        verify_password(password, DUMMY_HASH)  # equalise timing
        audit_service.record(
            db,
            "LOGIN_FAILED",
            "user",
            None,
            details={"username": username, "reason": "unknown_user"},
        )
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid username or password")

    if user.locked_until and user.locked_until > now:
        audit_service.record(db, "LOGIN_BLOCKED_LOCKED", "user", user.id, actor=user)
        db.commit()
        raise AppError(423, "ACCOUNT_LOCKED", "Account is locked; try again later")

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        details = {"failed_count": user.failed_login_count}
        audit_service.record(db, "LOGIN_FAILED", "user", user.id, actor=user, details=details)
        if user.failed_login_count >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            user.failed_login_count = 0
            audit_service.record(
                db,
                "ACCOUNT_LOCKED",
                "user",
                user.id,
                actor=user,
                details={"locked_until": user.locked_until.isoformat()},
            )
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid username or password")

    if not user.is_active or not user.employee_active:
        audit_service.record(db, "LOGIN_BLOCKED_INACTIVE", "user", user.id, actor=user)
        db.commit()
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid username or password")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    session, refresh = _new_session(db, user)
    audit_service.record(
        db, "LOGIN_SUCCESS", "user", user.id, actor=user, details={"session_id": session.id}
    )
    db.commit()
    return _token_response(db, user, session, refresh)


def refresh(db: Session, refresh_token: str) -> dict:
    token_hash = sha256_hex(refresh_token.encode())
    session = db.execute(
        select(AuthSession).where(AuthSession.refresh_token_hash == token_hash).with_for_update()
    ).scalar_one_or_none()
    now = utcnow()
    invalid = AppError(401, "NOT_AUTHENTICATED", "Session has ended; please log in again")
    if session is None or session.revoked_at is not None or session.expires_at < now:
        raise invalid
    if session.last_used_at + timedelta(minutes=settings.IDLE_TIMEOUT_MINUTES) < now:
        session.revoked_at = now
        session.revoke_reason = "IDLE_TIMEOUT"
        db.commit()
        raise invalid
    user = db.get(User, session.user_id)
    if user is None or not user.is_active or not user.employee_active:
        raise invalid
    # Rotate: the old refresh token stops working immediately.
    new_token, new_hash = new_refresh_token()
    session.refresh_token_hash = new_hash
    session.last_used_at = now
    db.commit()
    return _token_response(db, user, session, new_token)


def logout(db: Session, user: User, session_id: int) -> None:
    session = db.get(AuthSession, session_id)
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoke_reason = "LOGOUT"
    audit_service.record(
        db, "LOGOUT", "user", user.id, actor=user, details={"session_id": session_id}
    )
    db.commit()


def revoke_all_sessions(db: Session, user_id: int, reason: str, keep: int | None = None) -> None:
    sessions = db.execute(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    ).scalars()
    for s in sessions:
        if s.id != keep:
            s.revoked_at = utcnow()
            s.revoke_reason = reason


# ---------- Password policy ----------


def password_policy_errors(password: str, user: User | None = None) -> list[str]:
    errors = []
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        errors.append(f"At least {settings.PASSWORD_MIN_LENGTH} characters")
    if not re.search(r"[A-Z]", password):
        errors.append("At least one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("At least one lowercase letter")
    if not re.search(r"\d", password):
        errors.append("At least one digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("At least one special character")
    if user and user.username.lower() in password.lower():
        errors.append("Must not contain your username")
    return errors


def _reused(db: Session, user: User, password: str) -> bool:
    recent = (
        db.execute(
            select(PasswordHistory.password_hash)
            .where(PasswordHistory.user_id == user.id)
            .order_by(PasswordHistory.id.desc())
            .limit(settings.PASSWORD_HISTORY_COUNT)
        )
        .scalars()
        .all()
    )
    return any(verify_password(password, h) for h in [user.password_hash, *recent])


def set_password(db: Session, user: User, new_password: str, must_change: bool) -> None:
    """Validate policy + history, store the hash, record history. Caller audits and commits."""
    errors = password_policy_errors(new_password, user)
    if errors:
        raise AppError(
            422,
            "PASSWORD_POLICY",
            "Password does not meet the policy",
            [{"msg": e} for e in errors],
        )
    if _reused(db, user, new_password):
        raise AppError(
            422,
            "PASSWORD_POLICY",
            f"Password was used in the last {settings.PASSWORD_HISTORY_COUNT} changes",
        )
    db.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.must_change_password = must_change


def change_password(
    db: Session, user: User, session_id: int, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        audit_service.record(db, "PASSWORD_CHANGE_FAILED", "user", user.id, actor=user)
        db.commit()
        raise AppError(422, "INVALID_CREDENTIALS", "Current password is incorrect")
    set_password(db, user, new_password, must_change=False)
    # Other sessions are signed out; the current one keeps working.
    revoke_all_sessions(db, user.id, "PASSWORD_CHANGED", keep=session_id)
    audit_service.record(db, "PASSWORD_CHANGED", "user", user.id, actor=user)
    db.commit()
