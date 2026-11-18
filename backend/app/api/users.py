"""User management (ADMIN / SUPER_ADMIN)."""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import Principal, require
from app.models import User
from app.schemas.auth import ResetPasswordRequest, UserCreate, UserOut, UserUpdate
from app.schemas.common import ERROR_RESPONSES, ErrorResponse, PageParams
from app.services import user_service
from app.utils.pagination import page_params, paginate

router = APIRouter(prefix="/users", tags=["Users"], responses=ERROR_RESPONSES)
manage = require("user.manage")
SORTABLE = {
    "full_name": User.full_name,
    "employee_code": User.employee_code,
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
}


@router.get(
    "",
    response_model=list[UserOut],
    summary="List users",
    description="Sort by: " + ", ".join(SORTABLE) + ". Total in X-Total-Count.",
)
def list_users(
    response: Response,
    q: str | None = Query(None, max_length=100),
    role: str | None = None,
    department_id: int | None = None,
    is_active: bool | None = None,
    page: PageParams = Depends(page_params),
    _: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> list:
    stmt = user_service.list_query(q, role, department_id, is_active)
    return paginate(db, stmt, page, SORTABLE, User.id, response)


@router.post(
    "",
    response_model=UserOut,
    status_code=201,
    summary="Create user",
    description="The user must change the temporary password at first login.",
    responses={409: {"model": ErrorResponse}},
)
def create_user(
    body: UserCreate, actor: Principal = Depends(manage), db: Session = Depends(get_db)
) -> User:
    return user_service.create(db, actor, body)


@router.get("/{user_id}", response_model=UserOut, summary="Get user")
def get_user(user_id: int, _: Principal = Depends(manage), db: Session = Depends(get_db)) -> User:
    return user_service.get(db, user_id)


@router.patch("/{user_id}", response_model=UserOut, summary="Update user")
def update_user(
    user_id: int,
    body: UserUpdate,
    actor: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> User:
    return user_service.update(db, actor, user_id, body)


@router.post(
    "/{user_id}/deactivate",
    response_model=UserOut,
    summary="Deactivate user",
    description="Users are never deleted (expenses and audit keep their reference). "
    "Deactivation revokes all sessions immediately.",
    responses={409: {"model": ErrorResponse}},
)
def deactivate_user(
    user_id: int, actor: Principal = Depends(manage), db: Session = Depends(get_db)
) -> User:
    return user_service.set_active(db, actor, user_id, False)


@router.post("/{user_id}/activate", response_model=UserOut, summary="Reactivate user")
def activate_user(
    user_id: int, actor: Principal = Depends(manage), db: Session = Depends(get_db)
) -> User:
    return user_service.set_active(db, actor, user_id, True)


@router.post(
    "/{user_id}/force-password-change",
    response_model=UserOut,
    summary="Force password change at next request",
)
def force_password_change(
    user_id: int, actor: Principal = Depends(manage), db: Session = Depends(get_db)
) -> User:
    return user_service.force_password_change(db, actor, user_id)


@router.post(
    "/{user_id}/reset-password",
    response_model=UserOut,
    summary="Set a temporary password (user must change it)",
)
def reset_password(
    user_id: int,
    body: ResetPasswordRequest,
    actor: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> User:
    return user_service.reset_password(db, actor, user_id, body.temporary_password)


@router.post("/{user_id}/unlock", response_model=UserOut, summary="Clear a login lockout")
def unlock_user(
    user_id: int, actor: Principal = Depends(manage), db: Session = Depends(get_db)
) -> User:
    return user_service.unlock(db, actor, user_id)
