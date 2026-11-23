"""Auth endpoints: login, refresh, logout, me, change password."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, get_principal_allow_password_change, password_expired
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeOut,
    RefreshRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.common import ErrorResponse, Message
from app.services import approval_service, auth_service, expense_defaults

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in",
    description="Returns a short-lived access token and a rotating refresh token. "
    "LOGIN_MAX_FAILED_ATTEMPTS wrong passwords in a row lock the account for "
    "LOGIN_LOCKOUT_MINUTES. Rate-limited per IP. An unknown user, a wrong password and a locked "
    "account with a wrong password all get the same 401 INVALID_CREDENTIALS (no username "
    "enumeration); only with the right password does the answer say the account is locked "
    "(423, errors[0].locked_until) or switched off (403 ACCOUNT_INACTIVE).",
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        423: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> dict:
    return auth_service.login(db, body.username, body.password)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh tokens",
    description="Exchanges a refresh token for a new pair. The old refresh token stops working. "
    "Fails after the idle timeout or when the session was revoked.",
    responses={401: {"model": ErrorResponse}},
)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)) -> dict:
    return auth_service.refresh(db, body.refresh_token)


@router.post("/logout", response_model=Message, summary="Log out (revokes the session)")
def logout(
    principal: Principal = Depends(get_principal_allow_password_change),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.logout(db, principal.user, principal.session_id)
    return {"message": "Logged out"}


@router.get(
    "/me",
    response_model=MeOut,
    summary="Current user, permissions and feature flags",
    description="A UI renders from this response and never hardcodes role logic. "
    "`permissions` maps each permission to its data scope (OWN, ASSIGNED, "
    "DEPARTMENT, ALL).",
)
def me(
    principal: Principal = Depends(get_principal_allow_password_change),
    db: Session = Depends(get_db),
) -> dict:
    user = principal.user
    return {
        "user": UserOut.model_validate(user),
        "role": principal.role,
        "permissions": principal.permissions,
        "must_change_password": user.must_change_password,
        "password_expired": password_expired(user),
        "feature_flags": {
            "totp_2fa": settings.FEATURE_TOTP_2FA,
            "enforce_gst_rules": settings.ENFORCE_GST_RULES,
            "approval_routing": settings.APPROVAL_ROUTING,
        },
        "password_policy": {
            "min_length": settings.PASSWORD_MIN_LENGTH,
            "history_count": settings.PASSWORD_HISTORY_COUNT,
            "expiry_days": settings.PASSWORD_EXPIRY_DAYS,
        },
        "expense_defaults": expense_defaults.for_user(db, user),
        "approves": approval_service.approves(db, principal),
    }


@router.post(
    "/change-password",
    response_model=Message,
    summary="Change own password",
    description="Policy: minimum length, upper, lower, digit, special character, not the "
    "username, not one of the last N passwords. Other sessions are signed out.",
    responses={422: {"model": ErrorResponse}},
)
def change_password(
    body: ChangePasswordRequest,
    principal: Principal = Depends(get_principal_allow_password_change),
    db: Session = Depends(get_db),
) -> dict:
    auth_service.change_password(
        db, principal.user, principal.session_id, body.current_password, body.new_password
    )
    return {"message": "Password changed"}
