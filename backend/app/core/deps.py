"""Auth dependencies: who is calling, what they may do, and over which data."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AppError, forbidden
from app.core.security import decode_access_token, utcnow
from app.models import AuthSession, User

bearer = HTTPBearer(auto_error=False)

SCOPE_RANK = {"OWN": 0, "ASSIGNED": 1, "DEPARTMENT": 2, "ALL": 3}
ADMIN_ROLES = {"ADMIN", "SUPER_ADMIN"}


@dataclass
class Principal:
    user: User
    session_id: int
    permissions: dict[str, str] = field(default_factory=dict)  # code -> scope
    # Approvers this user is covering for today (out-of-office delegation).
    acting_for: set[int] = field(default_factory=set)

    @property
    def role(self) -> str:
        return self.user.role.code

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def scope(self, permission: str) -> str | None:
        return self.permissions.get(permission)


def password_expired(user: User) -> bool:
    if settings.PASSWORD_EXPIRY_DAYS <= 0:
        return False
    return user.password_changed_at + timedelta(days=settings.PASSWORD_EXPIRY_DAYS) < utcnow()


def _unauthenticated(detail: str = "Not authenticated") -> AppError:
    return AppError(401, "NOT_AUTHENTICATED", detail)


def _load_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None,
    db: Session,
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _unauthenticated()
    try:
        payload = decode_access_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise _unauthenticated("Invalid or expired token") from exc

    session = db.get(AuthSession, int(payload["sid"]))
    now = utcnow()
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at < now
        or str(session.user_id) != payload["sub"]
    ):
        raise _unauthenticated("Session has ended; please log in again")

    user = db.execute(select(User).where(User.id == session.user_id)).unique().scalar_one()
    if not user.is_active or not user.employee_active:
        raise AppError(401, "ACCOUNT_INACTIVE", "User is deactivated")
    perms = {g.permission.code: g.scope for g in user.role.grants}
    from app.services.engagement_service import delegators_of

    principal = Principal(
        user=user, session_id=session.id, permissions=perms, acting_for=delegators_of(db, user.id)
    )
    request.state.principal = principal
    return principal


def get_principal_allow_password_change(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    """For the few endpoints usable while a password change is pending."""
    return _load_principal(request, creds, db)


def get_principal(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    principal = _load_principal(request, creds, db)
    if principal.user.must_change_password or password_expired(principal.user):
        raise AppError(
            403, "PASSWORD_CHANGE_REQUIRED", "You must change your password before continuing"
        )
    return principal


def require(permission: str) -> Callable[..., Principal]:
    def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(permission):
            raise forbidden()
        return principal

    _dep.__name__ = f"require_{permission.replace('.', '_')}"
    return _dep
