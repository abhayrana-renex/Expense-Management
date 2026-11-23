"""Expense endpoints: CRUD, validate, submit, workflow actions, history, documents."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, get_principal, require
from app.core.errors import AppError
from app.models import Expense, Partner
from app.schemas.common import ERROR_RESPONSES, ErrorResponse, Message, PageParams
from app.schemas.expense import (
    ActionRequest,
    AllowedActions,
    DocumentOut,
    ExpenseCreate,
    ExpenseOut,
    ExpenseSummaryOut,
    ExpenseUpdate,
    HistoryOut,
    InvoiceCheckOut,
    SignedUrlOut,
    StepOut,
    ValidationResult,
)
from app.services import (
    approval_service,
    document_service,
    expense_rules,
    expense_service,
    idempotency_service,
    invoice_check,
    payment_service,
    routing_service,
)
from app.utils.pagination import page_params, paginate

router = APIRouter(prefix="/expenses", tags=["Expenses"], responses=ERROR_RESPONSES)
can_read = require("expense.read")
can_create = require("expense.create")
SORTABLE = {
    "created_at": Expense.created_at,
    "submitted_at": Expense.submitted_at,
    "total_amount": Expense.total_amount,
    "expense_no": Expense.expense_no,
    "status": Expense.status,
}
CONFLICT = {
    409: {"model": ErrorResponse, "description": "INVALID_TRANSITION or " "CONCURRENT_MODIFICATION"}
}
ROUTING_ERRORS = {
    "NO_APPROVAL_MAPPING",
    "AMOUNT_EXCEEDS_APPROVAL_LIMIT",
    "NO_INDEPENDENT_APPROVER",
    "APPROVER_INACTIVE",
}
IdemKey = Header(
    None,
    alias="Idempotency-Key",
    description="Unique per logical request (8-100 "
    "chars); a retry returns the "
    "original response",
)


def _after_failed_submit(
    db: Session, principal: Principal, expense_id: int | None, err: AppError
) -> None:
    """Nothing from the failed attempt is kept; routing failures are audited for admins."""
    db.rollback()
    routing_issue = err.code in ROUTING_ERRORS or any(
        e.get("code") in ROUTING_ERRORS for e in err.errors
    )
    if routing_issue:
        expense_service.record_routing_failure(db, principal, expense_id, err)


def _file_response(doc, disposition: str = "attachment") -> StreamingResponse:
    safe_name = (
        "".join(c for c in doc.original_filename if c.isalnum() or c in "._- ") or "bill.pdf"
    )
    # Only PDF / JPG / PNG may ever be shown inline; e-mails etc. are opaque downloads.
    previewable = doc.content_type in document_service.PREVIEWABLE
    if not previewable:
        disposition = "attachment"
    return StreamingResponse(
        document_service.stream(doc),
        media_type=doc.content_type if previewable else "application/octet-stream",
        headers={
            # Inline (signed) bills may be shown in the portal's own preview frame only.
            **(
                {"Content-Security-Policy": "frame-ancestors " + " ".join(settings.cors_origins)}
                if disposition == "inline"
                else {}
            ),
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "",
    response_model=list[ExpenseSummaryOut],
    summary="List expenses in your scope",
    description="`mine=true` for My Expenses. `status=PENDING` matches any PENDING_Ln. "
    "Dates filter on submitted date (created date for drafts). Sort by: "
    + ", ".join(SORTABLE)
    + ". Total in X-Total-Count.",
)
def list_expenses(
    response: Response,
    mine: bool = False,
    status: str | None = Query(None, max_length=30),
    date_from: date | None = None,
    date_to: date | None = None,
    expense_type_id: int | None = None,
    department_id: int | None = None,
    branch_id: int | None = None,
    q: str | None = Query(None, max_length=100),
    payment_status: str | None = Query(None, max_length=20),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(can_read),
    db: Session = Depends(get_db),
) -> list:
    stmt = expense_service.list_query(
        principal,
        mine,
        status,
        date_from,
        date_to,
        expense_type_id,
        department_id,
        branch_id,
        q,
        payment_status=payment_status,
    )
    return paginate(db, stmt, page, SORTABLE, Expense.id, response)


@router.post(
    "",
    response_model=ExpenseOut,
    status_code=201,
    summary="Create expense",
    description="Creates a DRAFT. With `submit=true` it is created and submitted in one "
    "transaction: if validation or routing fails, nothing is saved.",
    responses={422: {"model": ErrorResponse}},
)
def create_expense(
    body: ExpenseCreate,
    principal: Principal = Depends(can_create),
    idempotency_key: str | None = IdemKey,
    db: Session = Depends(get_db),
):
    ctx, replay = idempotency_service.begin(
        db, principal.user.id, idempotency_key, "expense.create", body.model_dump(mode="json")
    )
    if replay:
        return JSONResponse(replay.response_body, status_code=replay.status_code)
    try:
        status_code, payload = expense_service.create(db, principal, body, ctx)
    except AppError as err:
        _after_failed_submit(db, principal, None, err)
        raise
    return JSONResponse(payload, status_code=status_code)


@router.get(
    "/invoice-check",
    response_model=InvoiceCheckOut,
    summary="Is this company's invoice number already claimed?",
    description="Same invoice number + same company (GSTIN, else FAR vendor, else supplier "
    "name ignoring case/spaces/punctuation) on any live expense = duplicate, and saving "
    "will be refused with DUPLICATE_INVOICE. The same number from another company is fine.",
)
def invoice_check_endpoint(
    bill_number: str = Query(..., min_length=1, max_length=60),
    supplier_gstin: str | None = Query(None, max_length=15),
    supplier_name: str | None = Query(None, max_length=200),
    vendor_id: int | None = Query(None),
    exclude_expense_id: int | None = Query(None, description="The expense being edited"),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    vendor = db.get(Partner, vendor_id) if vendor_id else None
    company = invoice_check.company_of(vendor, supplier_gstin, supplier_name)
    matches, others = invoice_check.find(
        db,
        bill_number,
        company,
        viewer_id=principal.user.id,
        exclude_expense_id=exclude_expense_id,
    )
    return {
        "duplicate": bool(matches),
        "matches": [m.__dict__ for m in matches],
        "same_number_other_companies": others,
    }


@router.get("/{expense_id}", response_model=ExpenseOut, summary="Get expense")
def get_expense(
    expense_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> Expense:
    return expense_service.get_visible(db, principal, expense_id)


@router.put(
    "/{expense_id}",
    response_model=ExpenseOut,
    summary="Edit a DRAFT or RETURNED expense",
    description="Send the full expense. Lines with `id` are updated, lines without are "
    "added, missing lines are removed (their bills are superseded).",
    responses=CONFLICT,
)
def update_expense(
    expense_id: int,
    body: ExpenseUpdate,
    principal: Principal = Depends(can_create),
    db: Session = Depends(get_db),
):
    return JSONResponse(expense_service.update(db, principal, expense_id, body))


@router.get(
    "/{expense_id}/allowed-actions",
    response_model=AllowedActions,
    response_model_by_alias=True,
    summary="What you may do right now",
    description="Computed by the server state machine. UIs show buttons from this.",
)
def allowed_actions(
    expense_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> dict:
    expense = expense_service.get_visible(db, principal, expense_id)
    out = expense_rules.allowed_actions(expense, principal)
    out["send_back_to"] = (
        approval_service.send_back_target(db, expense) if out["send_back"] else None
    )
    out["send_back_targets"] = (
        approval_service.send_back_targets(db, expense) if out["send_back"] else []
    )
    out["payment_actions"] = payment_service.allowed(expense, principal)
    out["record_payment"] = bool(out["payment_actions"])
    return out


@router.post(
    "/{expense_id}/validate",
    response_model=ValidationResult,
    summary="List blocking issues before submit",
)
def validate_expense(
    expense_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> dict:
    expense = expense_service.get_visible(db, principal, expense_id)
    issues = expense_rules.validate_for_submit(db, expense)
    route = []
    if expense.items and expense.total_amount > 0:
        try:
            route = [
                {
                    "level": s.rule.level.code,
                    "approver": s.rule.approver.full_name,
                    "status": s.status,
                    "note": s.note,
                    "approval_limit": s.rule.approval_limit,
                }
                for s in routing_service.plan_route(db, expense)
            ]
        except AppError:
            route = []  # the routing problem is already listed in `issues`
    return {
        "can_submit": not any(i.severity == "BLOCK" for i in issues),
        "issues": issues,
        "route": route,
    }


@router.post(
    "/{expense_id}/submit",
    response_model=ExpenseOut,
    summary="Submit for approval",
    description="Validates (bills required per line), routes and moves to "
    "PENDING_<level> atomically. Resubmitting a RETURNED expense re-routes.",
    responses={**CONFLICT, 422: {"model": ErrorResponse}},
)
def submit_expense(
    expense_id: int,
    version: int | None = Query(None),
    principal: Principal = Depends(can_create),
    idempotency_key: str | None = IdemKey,
    db: Session = Depends(get_db),
):
    ctx, replay = idempotency_service.begin(
        db, principal.user.id, idempotency_key, "expense.submit", {"id": expense_id}
    )
    if replay:
        return JSONResponse(replay.response_body, status_code=replay.status_code)
    try:
        return JSONResponse(expense_service.submit(db, principal, expense_id, version, ctx))
    except AppError as err:
        _after_failed_submit(db, principal, expense_id, err)
        raise


Action = Literal["approve", "send_back", "reject", "return", "cancel", "reopen"]


@router.post(
    "/{expense_id}/{action}",
    response_model=ExpenseOut,
    summary="Workflow action",
    description="approve / send_back / reject / return (current approver), cancel (owner, "
    "before final approval), reopen (ADMIN, REJECTED -> RETURNED). send_back never cancels: "
    "`to` = \"owner\" sends it to the initiator to rewrite the bill (it is routed from the "
    "first approver again on resubmit); `to` = a step id from allowed-actions "
    "`send_back_targets` sends it to that earlier approver with a question; left out = one "
    "step back. Only the initiator ever edits the bill. A comment is mandatory. Supports "
    "Idempotency-Key.",
    responses=CONFLICT,
)
def workflow_action(
    expense_id: int,
    action: Action,
    body: ActionRequest,
    principal: Principal = Depends(get_principal),
    idempotency_key: str | None = IdemKey,
    db: Session = Depends(get_db),
):
    ctx, replay = idempotency_service.begin(
        db,
        principal.user.id,
        idempotency_key,
        f"expense.{action}",
        {"id": expense_id, **body.model_dump()},
    )
    if replay:
        return JSONResponse(replay.response_body, status_code=replay.status_code)
    return JSONResponse(
        approval_service.act(
            db, principal, expense_id, action, body.comment, body.version, ctx, to=body.to
        )
    )


@router.get(
    "/{expense_id}/history",
    response_model=list[HistoryOut],
    summary="Approval and change history (append-only)",
)
def get_history(
    expense_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> list:
    return approval_service.history(db, principal, expense_id)


@router.get(
    "/{expense_id}/route",
    response_model=list[StepOut],
    summary="Planned approval steps for every submission cycle",
)
def get_route(
    expense_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> list:
    return approval_service.route(db, principal, expense_id)


# ---------- Documents ----------


@router.post(
    "/{expense_id}/items/{item_id}/documents",
    response_model=DocumentOut,
    status_code=201,
    tags=["Documents"],
    summary="Upload a bill (PDF, JPG or PNG) for a line",
    description=f"PDF, JPG or PNG (checked by content), max {settings.MAX_UPLOAD_SIZE_MB} MB. "
    "Only while the expense is DRAFT or RETURNED.",
    responses={**CONFLICT, 413: {"model": ErrorResponse}},
)
async def upload_document(
    expense_id: int,
    item_id: int,
    file: UploadFile = File(...),
    principal: Principal = Depends(can_create),
    db: Session = Depends(get_db),
):
    data = await file.read(settings.max_upload_bytes + 1)
    return document_service.upload(
        db, principal, expense_id, item_id, file.filename or "", file.content_type, data
    )


@router.delete(
    "/{expense_id}/documents/{document_id}",
    response_model=Message,
    tags=["Documents"],
    summary="Remove a bill (marks it superseded; never deleted)",
)
def supersede_document(
    expense_id: int,
    document_id: int,
    principal: Principal = Depends(can_create),
    db: Session = Depends(get_db),
) -> dict:
    document_service.supersede(db, principal, expense_id, document_id)
    return {"message": "Document superseded"}


@router.get(
    "/documents/{document_id}/download",
    tags=["Documents"],
    summary="Download a bill (Bearer auth)",
    response_class=StreamingResponse,
)
def download_document(
    document_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> StreamingResponse:
    doc = document_service.download(db, principal, document_id, via="bearer")
    return _file_response(doc)


@router.get(
    "/documents/{document_id}/download-url",
    response_model=SignedUrlOut,
    tags=["Documents"],
    summary="Get a 60-second signed URL for a bill",
    description="For plain links / new tabs. The URL carries an HMAC over document id, "
    "user id and expiry - never a JWT. Access is re-checked when used.",
)
def document_url(
    document_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> dict:
    url, expires_at = document_service.signed_url(db, principal, document_id)
    return {"url": url, "expires_at": expires_at}


@router.get(
    "/documents/{document_id}/signed",
    tags=["Documents"],
    summary="Open a bill via signed URL (no Bearer header)",
    response_class=StreamingResponse,
    include_in_schema=True,
)
def signed_download(
    document_id: int,
    u: int,
    e: int,
    s: str = Query(..., max_length=64),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    doc, _ = document_service.resolve_signed(db, document_id, u, e, s)
    return _file_response(doc, disposition="inline")
