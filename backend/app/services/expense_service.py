"""Expense create / update / submit / read. Every write audits in the same transaction."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from app.core.context import get_request_context
from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import utcnow
from app.models import (
    ApprovalStep,
    BillExtraction,
    BillMonth,
    Branch,
    Department,
    Expense,
    ExpenseDocument,
    ExpenseHistory,
    ExpenseItem,
    ExpenseType,
    Partner,
)
from app.schemas.expense import ExpenseCreate, ExpenseIn, ExpenseOut, ExpenseUpdate
from app.services import (
    audit_service,
    contract_service,
    engagement_service,
    expense_rules,
    idempotency_service,
    invoice_check,
    routing_service,
)
from app.services.idempotency_service import IdempotencyContext
from app.services.scope import expense_scope_clause

SORTABLE = {
    "created_at": Expense.created_at,
    "submitted_at": Expense.submitted_at,
    "total_amount": Expense.total_amount,
    "expense_no": Expense.expense_no,
    "status": Expense.status,
}


# ---------- Reads ----------


def get_visible(
    db: Session, principal: Principal, expense_id: int, for_update: bool = False
) -> Expense:
    stmt = select(Expense).where(
        Expense.id == expense_id, expense_scope_clause(principal, "expense.read")
    )
    if for_update:
        stmt = stmt.with_for_update(of=Expense)
    expense = db.execute(stmt).unique().scalar_one_or_none()
    if expense is None:
        raise not_found("Expense")  # same answer whether missing or out of scope
    return expense


def list_query(
    principal: Principal,
    mine: bool = False,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    expense_type_id: int | None = None,
    department_id: int | None = None,
    branch_id: int | None = None,
    q: str | None = None,
    permission: str = "expense.read",
    payment_status: str | None = None,
) -> Select:
    stmt = select(Expense).where(expense_scope_clause(principal, permission))
    if mine:
        stmt = stmt.where(Expense.owner_id == principal.user.id)
    if status == "PENDING":
        stmt = stmt.where(Expense.status.like("PENDING\\_%"))
    elif status:
        stmt = stmt.where(Expense.status == status)
    when = func.coalesce(Expense.submitted_at, Expense.created_at)
    if date_from:
        stmt = stmt.where(when >= datetime.combine(date_from, time.min).astimezone())
    if date_to:
        stmt = stmt.where(
            when < datetime.combine(date_to + timedelta(days=1), time.min).astimezone()
        )
    if payment_status:
        stmt = stmt.where(Expense.payment_status == payment_status)
    if expense_type_id:
        stmt = stmt.where(Expense.expense_type_id == expense_type_id)
    if department_id:
        stmt = stmt.where(Expense.department_id == department_id)
    if branch_id:
        stmt = stmt.where(Expense.branch_id == branch_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Expense.title.ilike(like) | Expense.expense_no.ilike(like))
    return stmt


# ---------- Writes ----------


def _next_expense_no(db: Session) -> tuple[int, str]:
    new_id = db.execute(
        text("SELECT nextval(pg_get_serial_sequence('expenses', 'id'))")
    ).scalar_one()
    return new_id, f"EXP-{utcnow().year}-{new_id:06d}"


def _require_active(db: Session, model: type, obj_id: int | None, label: str):
    if obj_id is None:
        return None
    obj = db.get(model, obj_id)
    if obj is None or not obj.is_active:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"{label} does not exist or is inactive",
            [{"field": label.lower().replace(" ", "_") + "_id"}],
        )
    return obj


def _apply_header(db: Session, expense: Expense, data: ExpenseIn, principal: Principal) -> None:
    user = principal.user
    branch_id = data.branch_id or user.branch_id
    if branch_id is None:
        raise AppError(422, "VALIDATION_ERROR", "Choose a branch (you have no home branch)")
    department_id = data.department_id or user.effective_department_id
    if department_id is None:
        raise AppError(422, "VALIDATION_ERROR", "Choose a department")
    _require_active(db, Department, department_id, "Department")
    expense_type = _require_active(db, ExpenseType, data.expense_type_id, "Expense type")
    _require_active(db, Branch, branch_id, "Branch")
    _require_active(db, BillMonth, data.bill_month_id, "Bill month")
    expense.title = (data.title or "").strip() or _label(db, data, expense_type.name)
    expense.purpose = data.purpose
    expense.expense_type_id = data.expense_type_id
    expense.branch_id = branch_id
    expense.department_id = department_id
    expense.bill_month_id = data.bill_month_id
    expense.bill_period_start = data.bill_period_start
    expense.bill_period_end = data.bill_period_end
    expense.bill_type = data.bill_type
    # Only the chosen type's details are kept, so a switched type leaves nothing stale behind.
    po, adhoc, contract = (data.bill_type == t for t in ("PO", "ADHOC", "CONTRACT"))
    for field, keep in (
        ("po_number", po),
        ("approval_by_name", adhoc),
        ("approval_date", adhoc),
        ("approval_reference", adhoc),
        ("approval_amount", adhoc),
        ("contract_reference", contract),
        ("contract_party", contract),
        ("contract_start", contract),
        ("contract_end", contract),
        ("contract_value", contract),
        ("contract_terms", contract),
    ):
        value = getattr(data, field) if keep else None
        setattr(expense, field, value.strip() or None if isinstance(value, str) else value)


def _label(db: Session, data: ExpenseIn, type_name: str) -> str:
    """Claims are known by their expense number; this short label (vendor - month) is kept
    only for search and the ledger narration."""
    first = data.items[0] if data.items else None
    who = None
    if first is not None:
        vendor = db.get(Partner, first.vendor_id) if first.vendor_id else None
        who = (vendor.name if vendor else None) or (first.supplier_name or "").strip() or None
    when = data.bill_period_start or (first and (first.bill_date or first.expense_date))
    month = when.strftime("%b %Y") if when else None
    return " - ".join(p for p in (who or type_name, month) if p)[:200]


BASE_DOC_KIND = {"ADHOC": "APPROVAL", "CONTRACT": "CONTRACT"}


def _attach_base_docs(
    db: Session, principal: Principal, expense: Expense, doc_ids: list[int]
) -> None:
    """Attach the ad-hoc approval e-mail / contract; supersede ones no longer listed."""
    kind = BASE_DOC_KIND.get(expense.bill_type)
    wanted = list(dict.fromkeys(doc_ids)) if kind else []
    current = db.execute(
        select(ExpenseDocument).where(
            ExpenseDocument.expense_id == expense.id,
            ExpenseDocument.doc_kind != "BILL",
            ExpenseDocument.superseded_at.is_(None),
        )
    ).scalars()
    for doc in current:
        if doc.id not in wanted or doc.doc_kind != kind:
            doc.superseded_at = utcnow()
            doc.superseded_by_id = principal.user.id
    for doc_id in wanted:
        doc = db.execute(
            select(ExpenseDocument).where(ExpenseDocument.id == doc_id).with_for_update()
        ).scalar_one_or_none()
        ok = (
            doc is not None
            and doc.uploaded_by_id == principal.user.id
            and doc.superseded_at is None
            and doc.item_id is None
            and doc.expense_id in (None, expense.id)
        )
        if not ok:
            raise AppError(
                422,
                "VALIDATION_ERROR",
                f"File {doc_id} is not one of your unattached uploads",
                [{"field": "base_document_ids", "document_id": doc_id}],
            )
        if doc.expense_id == expense.id and doc.doc_kind == kind:
            continue
        doc.expense_id, doc.doc_kind = expense.id, kind
        audit_service.record(
            db,
            "BASE_DOCUMENT_ATTACHED",
            "expense_document",
            doc.id,
            actor=principal.user,
            details={"expense_id": expense.id, "kind": kind, "file": doc.original_filename},
        )


READ_TO_ITEM = {  # bill-reader field -> line item field (to record what the person changed)
    "invoice_number": "bill_number",
    "invoice_date": "bill_date",
    "taxable_amount": "taxable_amount",
    "cgst_amount": "cgst_amount",
    "sgst_amount": "sgst_amount",
    "igst_amount": "igst_amount",
    "vendor_id": "vendor_id",
    "vendor_gstin": "supplier_gstin",
    "vendor_name": "supplier_name",
}


def _apply_items(
    db: Session, expense: Expense, data: ExpenseIn, actor_id: int
) -> list[tuple[ExpenseItem, list[int]]]:
    """Apply lines; return (item, document_ids) to attach once the items have ids."""
    existing = {i.id: i for i in expense.items}
    seen: set[int] = set()
    kept: list[ExpenseItem] = []
    to_attach: list[tuple[ExpenseItem, list[int]]] = []
    for line_no, src in enumerate(data.items, start=1):
        if src.vendor_id:
            _require_active(db, Partner, src.vendor_id, "Vendor")
        if src.id is not None:
            item = existing.get(src.id)
            if item is None or src.id in seen:
                raise AppError(
                    422, "VALIDATION_ERROR", f"Line item {src.id} is not on this expense"
                )
            seen.add(src.id)
        else:
            item = ExpenseItem()
        for field in (
            "expense_date",
            "description",
            "vendor_id",
            "bill_number",
            "bill_date",
            "taxable_amount",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "cgst_rate",
            "sgst_rate",
            "igst_rate",
            "supplier_name",
            "supplier_gstin",
            "supplier_msme",
            "details_confirmed",
        ):
            setattr(item, field, getattr(src, field))
        item.line_no = line_no
        kept.append(item)
        if src.document_ids:
            to_attach.append((item, list(dict.fromkeys(src.document_ids))))
    # Removed lines: their bills are superseded, never deleted.
    removed = [i for i_id, i in existing.items() if i_id not in seen]
    if removed:
        for doc in db.execute(
            select(ExpenseDocument).where(
                ExpenseDocument.item_id.in_([i.id for i in removed]),
                ExpenseDocument.superseded_at.is_(None),
            )
        ).scalars():
            doc.superseded_at = utcnow()
            doc.superseded_by_id = actor_id
    expense.items = kept
    return to_attach


def _attach_bills(
    db: Session,
    principal: Principal,
    expense: Expense,
    to_attach: list[tuple[ExpenseItem, list[int]]],
) -> None:
    """Attach bills the user uploaded on the new-expense screen (staged) to their lines, and
    record which bill-reader values the person kept or changed."""
    for item, doc_ids in to_attach:
        for doc_id in doc_ids:
            doc = db.execute(
                select(ExpenseDocument).where(ExpenseDocument.id == doc_id).with_for_update()
            ).scalar_one_or_none()
            ok = (
                doc is not None
                and doc.uploaded_by_id == principal.user.id
                and doc.superseded_at is None
                and doc.doc_kind == "BILL"
                and doc.expense_id in (None, expense.id)
            )
            if not ok:
                raise AppError(
                    422,
                    "VALIDATION_ERROR",
                    f"Bill {doc_id} is not one of your unattached uploads",
                    [{"field": "document_ids", "document_id": doc_id}],
                )
            if doc.expense_id == expense.id and doc.item_id == item.id:
                continue  # already attached (idempotent)
            doc.expense_id, doc.item_id = expense.id, item.id
            reading = db.execute(
                select(BillExtraction).where(BillExtraction.document_id == doc.id)
            ).scalar_one_or_none()
            changed = {}
            if reading:
                for read_key, item_key in READ_TO_ITEM.items():
                    read_val = (reading.fields.get(read_key) or {}).get("value")
                    final = getattr(item, item_key)
                    final_s = (
                        None
                        if final is None
                        else (f"{final:.2f}" if isinstance(final, Decimal) else str(final))
                    )
                    if read_val is not None and str(read_val) != final_s:
                        changed[read_key] = [read_val, final_s]
            audit_service.record(
                db,
                "BILL_ATTACHED",
                "expense_document",
                doc.id,
                actor=principal.user,
                details={
                    "expense_id": expense.id,
                    "item_id": item.id,
                    "read_by_bill_reader": reading is not None,
                    "details_confirmed": item.details_confirmed,
                    "changed_from_reading": changed,
                },
            )


def _history(
    db: Session,
    expense: Expense,
    action: str,
    from_status: str | None,
    actor_id: int,
    comment: str | None = None,
    level_code: str | None = None,
) -> None:
    db.add(
        ExpenseHistory(
            expense_id=expense.id,
            action=action,
            from_status=from_status,
            to_status=expense.status,
            level_code=level_code,
            actor_id=actor_id,
            comment=comment,
            ip_address=get_request_context().ip_address,
        )
    )


def serialize(db: Session, expense_id: int) -> dict:
    db.flush()
    db.expire_all()
    expense = db.execute(select(Expense).where(Expense.id == expense_id)).unique().scalar_one()
    return ExpenseOut.model_validate(expense).model_dump(mode="json")


def create(
    db: Session, principal: Principal, data: ExpenseCreate, idem: IdempotencyContext | None
) -> tuple[int, dict]:
    new_id, number = _next_expense_no(db)
    expense = Expense(
        id=new_id,
        expense_no=number,
        owner_id=principal.user.id,
        status="DRAFT",
        cycle=0,
        currency="INR",
    )
    _apply_header(db, expense, data, principal)
    expense.items = []
    db.add(expense)
    to_attach = _apply_items(db, expense, data, principal.user.id)
    expense_rules.recalc_totals(expense)
    db.flush()
    invoice_check.assert_no_duplicates(db, expense, principal.user.id)
    _attach_bills(db, principal, expense, to_attach)
    _attach_base_docs(db, principal, expense, data.base_document_ids)
    db.flush()
    contract_service.link(db, principal, expense, data)
    _history(db, expense, "CREATED", None, principal.user.id)
    audit_service.record(
        db,
        "EXPENSE_CREATED",
        "expense",
        expense.id,
        actor=principal.user,
        details={
            "expense_no": number,
            "total": str(expense.total_amount),
            "bill_type": expense.bill_type,
        },
    )
    if data.submit:
        db.refresh(expense)
        _submit(db, principal, expense)
    body = serialize(db, expense.id)
    idempotency_service.store(db, idem, 201, body)
    db.commit()
    return 201, body


def update(db: Session, principal: Principal, expense_id: int, data: ExpenseUpdate) -> dict:
    expense = get_visible(db, principal, expense_id, for_update=True)
    expense_rules.assert_allowed("edit", expense, principal)
    expense_rules.check_version(expense, data.version)
    before = {
        "total": str(expense.total_amount),
        "title": expense.title,
        "bill_type": expense.bill_type,
    }
    _apply_header(db, expense, data, principal)
    to_attach = _apply_items(db, expense, data, principal.user.id)
    expense_rules.recalc_totals(expense)
    expense.updated_at = utcnow()  # always bump the row version
    db.flush()
    invoice_check.assert_no_duplicates(db, expense, principal.user.id)
    _attach_bills(db, principal, expense, to_attach)
    _attach_base_docs(db, principal, expense, data.base_document_ids)
    db.flush()
    contract_service.link(db, principal, expense, data)
    audit_service.record(
        db,
        "EXPENSE_UPDATED",
        "expense",
        expense.id,
        actor=principal.user,
        details=audit_service.diff(
            before,
            {
                "total": str(expense.total_amount),
                "title": expense.title,
                "bill_type": expense.bill_type,
            },
        ),
    )
    body = serialize(db, expense.id)
    db.commit()
    return body


def _snapshot(expense: Expense) -> None:
    expense.snapshot_department_name = expense.department.name
    expense.snapshot_branch_name = expense.branch.name
    expense.snapshot_expense_type_name = expense.expense_type.name
    for item in expense.items:
        item.snapshot_vendor_name = item.vendor.name if item.vendor else item.supplier_name
        item.snapshot_vendor_gstin = (
            item.vendor.gstin if item.vendor else None
        ) or item.supplier_gstin


def _submit(db: Session, principal: Principal, expense: Expense) -> None:
    """Validate, route and move to PENDING_<level> - all inside the caller's transaction."""
    expense_rules.assert_allowed("submit", expense, principal)
    issues = expense_rules.validate_for_submit(db, expense)
    blocking = [i for i in issues if i.severity == "BLOCK"]
    if blocking:
        raise AppError(
            422,
            "SUBMISSION_BLOCKED",
            "Fix the listed issues before submitting",
            [i.model_dump() for i in blocking],
        )
    steps = routing_service.plan_route(db, expense)
    from_status = expense.status
    expense.cycle += 1
    now = utcnow()
    for seq, planned in enumerate(steps, start=1):
        db.add(
            ApprovalStep(
                expense_id=expense.id,
                cycle=expense.cycle,
                sequence=seq,
                level_id=planned.rule.level_id,
                approver_id=planned.rule.approver_id,
                rule_id=planned.rule.id,
                status=planned.status,
                note=planned.note,
                activated_at=now if planned.status == "PENDING" else None,
            )
        )
    first = next(s for s in steps if s.status == "PENDING")
    expense.status = f"PENDING_{first.rule.level.code}"
    expense.current_level_id = first.rule.level_id
    expense.current_approver_id = first.rule.approver_id
    expense.submitted_at = now
    expense.current_stage_since = now
    _snapshot(expense)
    db.flush()
    engagement_service.notify_pending(db, expense)
    action = "RESUBMITTED" if expense.cycle > 1 else "SUBMITTED"
    _history(db, expense, action, from_status, principal.user.id)
    audit_service.record(
        db,
        f"EXPENSE_{action}",
        "expense",
        expense.id,
        actor=principal.user,
        details={
            "status": expense.status,
            "cycle": expense.cycle,
            "total": str(expense.total_amount),
            "route": [f"{s.rule.level.code}:{s.rule.approver_id}:{s.status}" for s in steps],
        },
    )


def submit(
    db: Session,
    principal: Principal,
    expense_id: int,
    version: int | None,
    idem: IdempotencyContext | None,
) -> dict:
    expense = get_visible(db, principal, expense_id, for_update=True)
    expense_rules.check_version(expense, version)
    _submit(db, principal, expense)
    body = serialize(db, expense.id)
    idempotency_service.store(db, idem, 200, body)
    db.commit()
    return body


def record_routing_failure(
    db: Session, principal: Principal, expense_id: int | None, err: AppError
) -> None:
    """After the failed transaction is rolled back, keep a trace for admins."""
    engagement_service.notify(
        db,
        engagement_service.holders_of(db, "approval_rule.manage"),
        "ROUTING_FAILED",
        "An expense couldn't be routed",
        f"{principal.user.full_name}: {err.detail}",
        expense_id,
    )
    audit_service.record(
        db,
        "ROUTING_FAILED",
        "expense",
        expense_id,
        actor=principal.user,
        details={"code": err.code, "detail": err.detail},
    )
    db.commit()
