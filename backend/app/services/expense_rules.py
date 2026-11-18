"""Expense state machine, totals and submit validation. The single source of business rules."""

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import AppError
from app.models import BillExtraction, Expense, ExpenseItem
from app.schemas.expense import ValidationIssue
from app.services import invoice_check, routing_service

ZERO = Decimal("0.00")
EDITABLE = frozenset({"DRAFT", "RETURNED"})
FINAL = frozenset({"APPROVED", "REJECTED", "CANCELLED"})
STATUSES = ("DRAFT", "SUBMITTED", "PENDING_Ln", "APPROVED", "REJECTED", "RETURNED", "CANCELLED")


def is_pending(status: str) -> bool:
    return status.startswith("PENDING_")


@dataclass(frozen=True)
class Transition:
    from_states: str  # "EDITABLE" | "PENDING" | "EDITABLE_OR_PENDING" | a single status
    actor: str  # "owner" | "current_approver" | "permission:<code>"
    description: str


# Transition table: action -> who may do it from which state.
TRANSITIONS: dict[str, Transition] = {
    "edit": Transition("EDITABLE", "owner", "Owner edits a draft or returned expense"),
    "upload_bill": Transition("EDITABLE", "owner", "Owner attaches/replaces bills while editable"),
    "submit": Transition("EDITABLE", "owner", "DRAFT/RETURNED -> PENDING_<first level>"),
    "cancel": Transition("EDITABLE_OR_PENDING", "owner", "Owner withdraws before final approval"),
    "approve": Transition("PENDING", "current_approver", "PENDING_Ln -> next level or APPROVED"),
    "reject": Transition("PENDING", "current_approver", "PENDING_Ln -> REJECTED (final)"),
    "return": Transition("PENDING", "current_approver", "PENDING_Ln -> RETURNED to owner"),
    "send_back": Transition(
        "PENDING",
        "current_approver",
        "One step back: to the previous approver, or RETURNED to the owner from the first level",
    ),
    "correct": Transition(
        "PENDING", "verifier", "Finance check corrects bill details (amounts, GST, invoice)"
    ),
    "reopen": Transition("REJECTED", "permission:expense.reopen", "REJECTED -> RETURNED (admin)"),
}


def _state_ok(spec: str, status: str) -> bool:
    if spec == "EDITABLE":
        return status in EDITABLE
    if spec == "PENDING":
        return is_pending(status)
    if spec == "EDITABLE_OR_PENDING":
        return status in EDITABLE or is_pending(status)
    return status == spec


def _actor_ok(spec: str, expense: Expense, principal: Principal) -> bool:
    uid = principal.user.id
    if spec == "owner":
        return expense.owner_id == uid
    if spec == "current_approver":
        mine = expense.current_approver_id == uid or (
            expense.current_approver_id in principal.acting_for
        )
        return mine and expense.owner_id != uid and principal.can("expense.approve")
    if spec == "verifier":
        return _actor_ok("current_approver", expense, principal) and principal.can(
            "expense.correct"
        )
    if spec.startswith("permission:"):
        return principal.can(spec.split(":", 1)[1])
    return False


def allowed_actions(expense: Expense, principal: Principal) -> dict[str, bool]:
    out = {
        action: _state_ok(t.from_states, expense.status) and _actor_ok(t.actor, expense, principal)
        for action, t in TRANSITIONS.items()
    }
    out["record_payment"] = (
        expense.status == "APPROVED"
        and expense.payment_status in ("PENDING", "PROCESSING", "ON_HOLD")
        and principal.can("payment.record")
    )
    return out


def assert_allowed(action: str, expense: Expense, principal: Principal) -> None:
    t = TRANSITIONS[action]
    if not _state_ok(t.from_states, expense.status):
        raise AppError(
            409, "INVALID_TRANSITION", f"Cannot {action} an expense in status {expense.status}"
        )
    if not _actor_ok(t.actor, expense, principal):
        raise AppError(403, "FORBIDDEN", f"You are not allowed to {action} this expense")


def check_version(expense: Expense, version: int | None) -> None:
    if version is not None and version != expense.version:
        raise AppError(
            409,
            "CONCURRENT_MODIFICATION",
            "This expense was changed by someone else; reload and try again",
        )


# ---------- Totals ----------


def recalc_totals(expense: Expense) -> None:
    """Server-side recalculation; client totals are never trusted."""
    taxable = tax = ZERO
    for item in expense.items:
        item_tax = item.cgst_amount + item.sgst_amount + item.igst_amount
        item.total_amount = item.taxable_amount + item_tax
        taxable += item.taxable_amount
        tax += item_tax
    expense.total_taxable = taxable
    expense.total_tax = tax
    expense.total_amount = taxable + tax


# ---------- Submit validation ----------


def _gst_issues(expense: Expense, item: ExpenseItem) -> list[ValidationIssue]:
    severity = "BLOCK" if settings.ENFORCE_GST_RULES else "WARN"
    issues = []
    has_igst = item.igst_amount > 0
    has_local = item.cgst_amount > 0 or item.sgst_amount > 0

    def add(code: str, msg: str) -> None:
        issues.append(
            ValidationIssue(
                code=code, message=msg, severity=severity, item_id=item.id, line_no=item.line_no
            )
        )

    if has_igst and has_local:
        add("GST_MIXED", "IGST and CGST/SGST cannot both apply to one bill")
    if item.cgst_amount != item.sgst_amount:
        add("GST_CGST_SGST_MISMATCH", "CGST and SGST should be equal on an intra-state bill")
    supplier_gstin = (item.vendor.gstin if item.vendor else None) or item.supplier_gstin
    vendor_state = supplier_gstin[:2] if supplier_gstin else None
    branch_state = expense.branch.state_code if expense.branch else None
    if vendor_state and branch_state and (has_igst or has_local):
        if vendor_state == branch_state and has_igst:
            add(
                "GST_PLACE_OF_SUPPLY", "Vendor and branch are in the same state: expected CGST+SGST"
            )
        if vendor_state != branch_state and has_local:
            add("GST_PLACE_OF_SUPPLY", "Vendor and branch are in different states: expected IGST")
    return issues


def _rate_issues(item: ExpenseItem) -> list[ValidationIssue]:
    """Tax amount should match taxable x rate (bills may round per item: allow Rs 1)."""
    severity = "BLOCK" if settings.ENFORCE_GST_RULES else "WARN"
    out = []
    for kind in ("cgst", "sgst", "igst"):
        rate = getattr(item, f"{kind}_rate")
        amount = getattr(item, f"{kind}_amount")
        if rate is None:
            continue
        expected = (item.taxable_amount * rate / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        if abs(expected - amount) > Decimal("1.00"):
            out.append(
                ValidationIssue(
                    code="GST_RATE_MISMATCH",
                    severity=severity,
                    item_id=item.id,
                    line_no=item.line_no,
                    field=f"{kind}_amount",
                    message=f"{kind.upper()} {amount} doesn't match {rate}% of "
                    f"{item.taxable_amount} (expected {expected})",
                )
            )
    if (
        item.cgst_rate is not None
        and item.sgst_rate is not None
        and item.cgst_rate != item.sgst_rate
    ):
        out.append(
            ValidationIssue(
                code="GST_CGST_SGST_RATE_MISMATCH",
                severity=severity,
                item_id=item.id,
                line_no=item.line_no,
                field="sgst_rate",
                message="CGST and SGST rates should be the same",
            )
        )
    return out


def _duplicate_bill_issues(db: Session, expense: Expense) -> list[ValidationIssue]:
    """Same company + same invoice number already claimed (here or elsewhere) blocks."""
    try:
        invoice_check.assert_no_duplicates(db, expense, expense.owner_id)
    except AppError as err:
        return [
            ValidationIssue(
                code="DUPLICATE_INVOICE",
                severity="BLOCK",
                field="bill_number",
                line_no=e.get("line_no"),
                item_id=next((i.id for i in expense.items if i.line_no == e.get("line_no")), None),
                message=e["message"],
            )
            for e in err.errors
        ]
    return []


def _bill_type_issues(expense: Expense, today: date) -> list[ValidationIssue]:
    """The base document each type of bill needs (AP process: PO / ad-hoc / contract)."""
    out: list[ValidationIssue] = []

    def add(code: str, msg: str, field: str, severity: str = "BLOCK") -> None:
        out.append(ValidationIssue(code=code, message=msg, severity=severity, field=field))

    kinds = {d.doc_kind for d in expense.base_documents}
    if expense.bill_type == "ADHOC":
        if "APPROVAL" not in kinds:
            add(
                "APPROVAL_DOC_MISSING",
                "Upload the approval e-mail (PDF or screenshot)",
                "base_document_ids",
            )
        if not expense.approval_by_name:
            add("APPROVAL_DETAILS_MISSING", "Enter who approved this expense", "approval_by_name")
        if not expense.approval_date:
            add("APPROVAL_DETAILS_MISSING", "Enter the date of the approval", "approval_date")
        elif expense.approval_date > today:
            add("FUTURE_DATE", "Approval date cannot be in the future", "approval_date")
        if expense.approval_amount is not None and expense.total_amount > expense.approval_amount:
            add(
                "OVER_APPROVED_AMOUNT",
                f"Claim {expense.total_amount} is more than the approved "
                f"{expense.approval_amount}",
                "approval_amount",
                "WARN",
            )
    elif expense.bill_type == "CONTRACT":
        if "CONTRACT" not in kinds:
            add("CONTRACT_DOC_MISSING", "Upload the contract / MSA", "base_document_ids")
        if not expense.contract_reference:
            add("CONTRACT_DETAILS_MISSING", "Enter the contract / MSA number", "contract_reference")
        if not (expense.contract_start and expense.contract_end):
            add(
                "CONTRACT_DETAILS_MISSING",
                "Enter the contract start and end dates",
                "contract_start",
            )
        else:
            outside = [
                i.line_no
                for i in expense.items
                if i.bill_date
                and not (expense.contract_start <= i.bill_date <= expense.contract_end)
            ]
            if outside:
                add(
                    "OUTSIDE_CONTRACT_PERIOD",
                    f"Bill date on line(s) {', '.join(map(str, outside))} is outside the "
                    f"contract period {expense.contract_start} to {expense.contract_end}",
                    "contract_end",
                    "WARN",
                )
    return out


def _bill_period_issues(expense: Expense) -> list[ValidationIssue]:
    """Bill duration: both ends or neither; the bill month should fall inside it."""
    start, end = expense.bill_period_start, expense.bill_period_end
    if bool(start) != bool(end):
        return [
            ValidationIssue(
                code="BILL_PERIOD_INCOMPLETE",
                severity="BLOCK",
                field="bill_period_end" if start else "bill_period_start",
                message="Enter both the from and to dates of the bill period (or neither)",
            )
        ]
    month = expense.bill_month
    if start and end and month:
        last = calendar.monthrange(month.month_start.year, month.month_start.month)[1]
        month_end = month.month_start.replace(day=last)
        if start > month_end or end < month.month_start:
            return [
                ValidationIssue(
                    code="BILL_MONTH_OUTSIDE_PERIOD",
                    severity="WARN",
                    field="bill_month_id",
                    message=f"Bill month {month.label} is outside the bill period "
                    f"{start:%d %b %Y} - {end:%d %b %Y}",
                )
            ]
    return []


def _was_read(db: Session, item: ExpenseItem) -> bool:
    ids = [d.id for d in item.documents]
    return (
        bool(ids)
        and db.execute(
            select(func.count())
            .select_from(BillExtraction)
            .where(BillExtraction.document_id.in_(ids))
        ).scalar_one()
        > 0
    )


def validate_for_submit(db: Session, expense: Expense) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    def block(
        code: str, msg: str, item: ExpenseItem | None = None, field: str | None = None
    ) -> None:
        issues.append(
            ValidationIssue(
                code=code,
                message=msg,
                severity="BLOCK",
                field=field,
                item_id=item.id if item else None,
                line_no=item.line_no if item else None,
            )
        )

    if not expense.items:
        block("NO_ITEMS", "Add at least one line item")
    if not expense.expense_type.is_active:
        block("INACTIVE_MASTER", "Expense type is inactive", field="expense_type_id")
    if not expense.branch.is_active:
        block("INACTIVE_MASTER", "Branch is inactive", field="branch_id")
    if expense.cost_centre and not expense.cost_centre.is_active:
        block("INACTIVE_MASTER", "Cost centre is inactive", field="cost_centre_id")
    if expense.bill_month and not (expense.bill_month.is_open and expense.bill_month.is_active):
        block(
            "BILL_MONTH_CLOSED",
            f"Bill month {expense.bill_month.label} is closed",
            field="bill_month_id",
        )

    today = date.today()
    issues.extend(_bill_type_issues(expense, today))
    issues.extend(_bill_period_issues(expense))
    for item in expense.items:
        if item.expense_date > today:
            block("FUTURE_DATE", "Expense date cannot be in the future", item, "expense_date")
        if item.bill_date and item.bill_date > today:
            block("FUTURE_DATE", "Bill date cannot be in the future", item, "bill_date")
        if item.vendor and not item.vendor.is_active:
            block("INACTIVE_MASTER", "Vendor is inactive", item, "vendor_id")
        if not item.details_confirmed and item.documents and _was_read(db, item):
            block(
                "BILL_DETAILS_NOT_CONFIRMED",
                f"Line {item.line_no}: confirm the details read from the bill",
                item,
                "details_confirmed",
            )
        if expense.expense_type.requires_bill and not item.documents:
            block("MISSING_BILL", f"Line {item.line_no}: upload the bill (PDF)", item, "documents")
        issues.extend(_gst_issues(expense, item))
        issues.extend(_rate_issues(item))
    issues.extend(_duplicate_bill_issues(db, expense))

    if expense.items and expense.total_amount > 0:
        try:
            routing_service.plan_route(db, expense)
        except AppError as err:
            block(err.code, err.detail)
    return issues
