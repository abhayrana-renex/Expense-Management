"""Roles & rights, tracker, and the payment workflow (schedule -> approve -> bank -> UTR -> GL)."""

import csv
import io
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import Principal, get_principal, require
from app.models import Expense
from app.schemas.common import ERROR_RESPONSES, ErrorResponse, PageParams, money_str
from app.schemas.expense import (
    BankFileRequest,
    ExpenseOut,
    ExpenseSummaryOut,
    PaymentBulkRequest,
    PaymentRequest,
    TrackerOut,
)
from app.services import ledger_service, payment_service, role_service, tracker_service
from app.services.scope import expense_scope_clause
from app.utils.pagination import page_params, paginate

router = APIRouter(responses=ERROR_RESPONSES)


class RoleOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    is_system: bool
    is_active: bool
    locked: bool
    active_users: int
    grants: dict[str, str] = Field(description="permission code -> data scope")


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Za-z][A-Za-z0-9_]+$")
    name: str = Field(min_length=2, max_length=100)
    description: str | None = Field(None, max_length=255)
    grants: dict[str, str] = Field(default_factory=dict)


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=100)
    description: str | None = Field(None, max_length=255)
    grants: dict[str, str] | None = None
    is_active: bool | None = None


class PermissionOut(BaseModel):
    code: str
    description: str
    module: str
    scopes: list[str]


@router.get(
    "/permissions",
    response_model=list[PermissionOut],
    tags=["Roles & rights"],
    summary="Every permission, its module and the data scopes it supports",
)
def permissions(_: Principal = Depends(get_principal)) -> list:
    return role_service.catalogue()


@router.get(
    "/roles",
    response_model=list[RoleOut],
    tags=["Roles & rights"],
    summary="Roles with their rights and active user counts",
)
def roles(_: Principal = Depends(require("user.manage")), db: Session = Depends(get_db)) -> list:
    return role_service.list_roles(db)


@router.post(
    "/roles",
    response_model=RoleOut,
    status_code=201,
    tags=["Roles & rights"],
    summary="Create a role",
    responses={409: {"model": ErrorResponse}},
)
def create_role(
    body: RoleCreate,
    actor: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_db),
) -> dict:
    return role_service.create(db, actor, body.code, body.name, body.description, body.grants)


@router.patch(
    "/roles/{role_id}",
    response_model=RoleOut,
    tags=["Roles & rights"],
    summary="Change a role's rights, name or active flag",
    description="SUPER_ADMIN's rights are fixed; built-in roles can't be renamed or "
    "switched off; some active role must always keep role.manage and "
    "user.manage. Every change is audited.",
    responses={409: {"model": ErrorResponse}},
)
def update_role(
    role_id: int,
    body: RoleUpdate,
    actor: Principal = Depends(require("role.manage")),
    db: Session = Depends(get_db),
) -> dict:
    return role_service.update(
        db, actor, role_id, body.name, body.description, body.grants, body.is_active
    )


# ---------- tracker & payments ----------


@router.get(
    "/expenses/{expense_id}/tracker",
    response_model=TrackerOut,
    tags=["Expenses"],
    summary="Order-style status: submitted -> L1 -> L2 -> L3 -> approved -> paid",
)
def tracker(
    expense_id: int,
    principal: Principal = Depends(require("expense.read")),
    db: Session = Depends(get_db),
) -> dict:
    return tracker_service.build(db, principal, expense_id)


@router.post(
    "/expenses/{expense_id}/payment",
    response_model=ExpenseOut,
    tags=["Payments"],
    summary="Payment step: schedule / approve / send back / sent to bank / paid / hold",
    description="Approved claims only. schedule needs `scheduled_for` (today or later); "
    "approve is for the payment approver (never the claimant or who scheduled it); paid needs "
    "mode, UTR (except cash) and paid_on. Push-back and hold need a `note`. PAID is final.",
    responses={409: {"model": ErrorResponse}},
)
def payment_action(
    expense_id: int,
    body: PaymentRequest,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    return payment_service.act(
        db,
        principal,
        expense_id,
        body.action,
        scheduled_for=body.scheduled_for,
        mode=body.mode,
        reference=body.reference,
        paid_on=body.paid_on,
        note=body.note,
        version=body.version,
    )


@router.post(
    "/payments/bulk",
    tags=["Payments"],
    summary="Same payment step on many claims (schedule a run, MD approves the day's run)",
    description="Each claim is done or skipped on its own, with the reason.",
)
def payment_bulk(
    body: PaymentBulkRequest,
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    return payment_service.bulk(
        db,
        principal,
        body.expense_ids,
        body.action,
        scheduled_for=body.scheduled_for,
        mode=body.mode,
        note=body.note,
    )


@router.get(
    "/payments/queue",
    response_model=list[ExpenseSummaryOut],
    tags=["Payments"],
    summary="Payments by stage: PENDING, SCHEDULED, PAY_APPROVED, PROCESSING, PAID, ON_HOLD",
)
def payment_queue(
    response: Response,
    payment_status: Literal[
        "PENDING", "SCHEDULED", "PAY_APPROVED", "PROCESSING", "PAID", "ON_HOLD"
    ] = "PENDING",
    scheduled_from: date | None = None,
    scheduled_to: date | None = Query(None, description="e.g. today: everything due so far"),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list:
    stmt = payment_service.queue_query(principal, payment_status, scheduled_from, scheduled_to)
    sortable = {
        "approved_at": Expense.approved_at,
        "total_amount": Expense.total_amount,
        "current_stage_since": Expense.current_stage_since,
        "payment_scheduled_for": Expense.payment_scheduled_for,
        "paid_on": Expense.paid_on,
    }
    return paginate(db, stmt, page, sortable, Expense.id, response)


@router.get("/payments/summary", tags=["Payments"], summary="Counts and amounts per stage")
def payment_summary(
    principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> dict:
    return payment_service.summary(db, principal)


def _sheet(name: str, header: list[str], rows: list[list[Any]], fmt: str) -> Response:
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        data, media, ext = ("﻿" + buf.getvalue()).encode(), "text/csv", "csv"
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = name[:31]
        ws.append(header)
        for r in rows:
            ws.append(r)
        out = io.BytesIO()
        wb.save(out)
        data, ext = out.getvalue(), "xlsx"
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{ext}"'},
    )


@router.post(
    "/payments/bank-file",
    tags=["Payments"],
    summary="Download the bank upload file for payments approved for payment",
    description="Generic Excel/CSV (demo: beneficiary bank details are blank). Every claim "
    "in the file moves to PROCESSING (sent to bank) under one batch reference.",
    response_class=Response,
)
def payment_bank_file(
    body: BankFileRequest,
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    principal: Principal = Depends(require("payment.record")),
    db: Session = Depends(get_db),
) -> Response:
    batch, rows = payment_service.bank_file(db, principal, body.expense_ids)
    return _sheet(batch, payment_service.BANK_COLUMNS, rows, fmt)


PAID_COLUMNS = [
    "Claim no.",
    "Title",
    "Initiator",
    "Department",
    "Payee",
    "Amount",
    "Mode",
    "UTR",
    "Paid on",
    "Payment date",
    "Bank batch",
]


@router.get(
    "/payments/paid-export",
    tags=["Payments"],
    summary="Download paid claims (finance: all; initiators: their own)",
    response_class=Response,
)
def paid_export(
    date_from: date | None = None,
    date_to: date | None = None,
    fmt: Literal["xlsx", "csv"] = Query("xlsx", alias="format"),
    principal: Principal = Depends(require("expense.read")),
    db: Session = Depends(get_db),
) -> Response:
    perm = next(
        (p for p in ("payment.record", "payment.schedule", "payment.approve") if principal.can(p)),
        "expense.read",
    )
    stmt = select(Expense).where(
        Expense.payment_status == "PAID", expense_scope_clause(principal, perm)
    )
    if date_from:
        stmt = stmt.where(Expense.paid_on >= date_from)
    if date_to:
        stmt = stmt.where(Expense.paid_on <= date_to)
    rows = [
        [
            e.expense_no,
            e.title,
            e.owner.full_name,
            e.department.name,
            payment_service.payee_of(e),
            money_str(e.paid_amount or e.total_amount),
            e.payment_mode,
            e.payment_reference or "",
            e.paid_on.isoformat() if e.paid_on else "",
            e.payment_scheduled_for.isoformat() if e.payment_scheduled_for else "",
            e.payment_batch_ref or "",
        ]
        for e in db.execute(stmt.order_by(Expense.paid_on, Expense.id)).unique().scalars()
    ]
    return _sheet(f"paid-claims-{date.today():%Y%m%d}", PAID_COLUMNS, rows, fmt)


@router.get(
    "/payments/general-ledger",
    tags=["Payments"],
    summary="General ledger (double entry) for paid claims, by payment date",
    response_class=Response,
)
def general_ledger(
    date_from: date | None = None,
    date_to: date | None = None,
    principal: Principal = Depends(require("ledger.export")),
    db: Session = Depends(get_db),
) -> Response:
    data = ledger_service.xlsx(db, principal, date_from, date_to)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; "
            f'filename="general-ledger-{date.today():%Y%m%d}.xlsx"'
        },
    )
