"""Notifications, out-of-office delegation and bulk approval."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, get_principal, require
from app.models import Notification, User
from app.schemas.common import ERROR_RESPONSES, ErrorResponse, Message, ORMModel, PageParams
from app.services import approval_service, engagement_service
from app.utils.pagination import page_params, paginate

router = APIRouter(responses=ERROR_RESPONSES)


class NotificationOut(ORMModel):
    id: int
    kind: str
    title: str
    body: str | None
    expense_id: int | None
    created_at: datetime
    read_at: datetime | None


class UnreadCount(BaseModel):
    unread: int


class Person(ORMModel):
    id: int
    full_name: str


class DelegationIn(BaseModel):
    delegate_id: int
    starts_on: date
    ends_on: date
    reason: str | None = Field(None, max_length=255)
    approver_id: int | None = Field(None, description="Admins only: delegate someone else's")


class DelegationOut(ORMModel):
    id: int
    approver: Person
    delegate: Person
    starts_on: date
    ends_on: date
    reason: str | None


class BulkApproveIn(BaseModel):
    expense_ids: list[int] = Field(min_length=1, max_length=50)
    comment: str = Field(min_length=3, max_length=2000)


class BulkSkipped(BaseModel):
    id: int
    code: str
    reason: str


class BulkApproveOut(BaseModel):
    approved: list[int]
    skipped: list[BulkSkipped]
    limit: str


# ---------- notifications ----------


@router.get(
    "/notifications",
    response_model=list[NotificationOut],
    tags=["Notifications"],
    summary="My notifications, newest first",
)
def notifications(
    response: Response,
    unread_only: bool = False,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list:
    stmt = engagement_service.list_query(principal.user.id, unread_only)
    return paginate(
        db, stmt, page, {"created_at": Notification.created_at}, Notification.id, response
    )


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCount,
    tags=["Notifications"],
    summary="Number of unread notifications (for the bell badge)",
)
def unread(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    return {"unread": engagement_service.unread_count(db, principal.user.id)}


@router.post(
    "/notifications/{notification_id}/read",
    response_model=Message,
    tags=["Notifications"],
    summary="Mark one notification read",
)
def read_one(
    notification_id: int,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    engagement_service.mark_read(db, principal.user.id, notification_id)
    return {"message": "ok"}


@router.post(
    "/notifications/read-all",
    response_model=Message,
    tags=["Notifications"],
    summary="Mark all my notifications read",
)
def read_all(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    n = engagement_service.mark_read(db, principal.user.id, None)
    return {"message": f"{n} marked read"}


# ---------- delegation ----------


@router.get(
    "/delegations",
    response_model=list[DelegationOut],
    tags=["Approvals"],
    summary="Current and upcoming out-of-office delegations (mine; all for admins)",
)
def delegations(
    principal: Principal = Depends(require("expense.approve")), db: Session = Depends(get_db)
) -> list:
    return engagement_service.list_delegations(db, principal)


@router.post(
    "/delegations",
    response_model=DelegationOut,
    status_code=201,
    tags=["Approvals"],
    summary="Hand my approvals to a colleague for a date range",
    description="The delegate sees and acts on approvals assigned to you during the "
    "dates (recorded as 'on behalf of'). Max 90 days; no overlaps.",
    responses={409: {"model": ErrorResponse}},
)
def create_delegation(
    body: DelegationIn,
    principal: Principal = Depends(require("expense.approve")),
    db: Session = Depends(get_db),
):
    return engagement_service.create_delegation(
        db, principal, body.delegate_id, body.starts_on, body.ends_on, body.reason, body.approver_id
    )


@router.get(
    "/delegations/candidates",
    response_model=list[Person],
    tags=["Approvals"],
    summary="Colleagues who can approve (to pick a delegate)",
)
def delegate_candidates(
    principal: Principal = Depends(require("expense.approve")), db: Session = Depends(get_db)
) -> list:
    ids = set(engagement_service.holders_of(db, "expense.approve")) - {principal.user.id}
    users = db.execute(select(User).where(User.id.in_(ids)).order_by(User.full_name))
    return list(users.unique().scalars())


@router.post(
    "/delegations/{delegation_id}/revoke",
    response_model=Message,
    tags=["Approvals"],
    summary="End a delegation now",
)
def revoke_delegation(
    delegation_id: int, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> dict:
    engagement_service.revoke_delegation(db, principal, delegation_id)
    return {"message": "Delegation ended"}


# ---------- bulk approve ----------


@router.post(
    "/approvals/bulk-approve",
    response_model=BulkApproveOut,
    tags=["Approvals"],
    summary="Approve several clean, small expenses at once",
    description="Each expense must be waiting for you (or someone you cover for), be at "
    "or below BULK_APPROVE_MAX_AMOUNT and have no warnings (possible "
    "duplicate bill, GST mismatch...). Others are skipped with a reason.",
)
def bulk_approve(
    body: BulkApproveIn,
    principal: Principal = Depends(require("expense.approve")),
    db: Session = Depends(get_db),
) -> dict:
    result = approval_service.bulk_approve(db, principal, body.expense_ids, body.comment)
    return {**result, "limit": f"{settings.BULK_APPROVE_MAX_AMOUNT:.2f}"}
