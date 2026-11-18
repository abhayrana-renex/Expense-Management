"""Payment workflow after final approval (AP process flow):

    PENDING (approved, to schedule)
      -> SCHEDULED      finance schedules a payment date            (payment.schedule)
      -> PAY_APPROVED   payment approver (MD) approves the day's run (payment.approve)
      -> PROCESSING     bank file downloaded / sent to bank          (payment.record)
      -> PAID           UTR recorded - final                         (payment.record)

Every stage can push back exactly one step, with a reason. ON_HOLD parks a payment from any
open stage and is released back to PENDING. Nothing here moves money: the bank file is
uploaded on the bank portal outside the portal.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.context import get_request_context
from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import utcnow
from app.models import Expense, ExpenseHistory
from app.schemas.common import money_str
from app.services import audit_service, engagement_service, expense_rules
from app.services.expense_service import serialize
from app.services.scope import expense_scope_clause

MODES = ("NEFT", "RTGS", "IMPS", "UPI", "CHEQUE", "CASH", "OTHER")
OPEN = ("PENDING", "SCHEDULED", "PAY_APPROVED", "PROCESSING")
PAYMENT_PERMS = ("payment.schedule", "payment.approve", "payment.record")
LABEL = {
    "PENDING": "To schedule",
    "SCHEDULED": "Scheduled - awaiting payment approval",
    "PAY_APPROVED": "Approved for payment",
    "PROCESSING": "Sent to bank",
    "PAID": "Paid",
    "ON_HOLD": "On hold",
}


@dataclass(frozen=True)
class Move:
    from_states: tuple[str, ...]
    to: str
    permission: str
    needs_note: bool = False
    verb: str = ""


ACTIONS: dict[str, Move] = {
    "schedule": Move(("PENDING", "SCHEDULED"), "SCHEDULED", "payment.schedule", verb="scheduled"),
    "unschedule": Move(("SCHEDULED",), "PENDING", "payment.schedule", verb="unscheduled"),
    "approve": Move(("SCHEDULED",), "PAY_APPROVED", "payment.approve", verb="approved for payment"),
    # push back one step at every stage
    "send_back": Move(("SCHEDULED",), "PENDING", "payment.approve", True, "sent back to schedule"),
    "reopen": Move(("PAY_APPROVED",), "SCHEDULED", "payment.schedule", True, "sent back to the MD"),
    "sent_to_bank": Move(("PAY_APPROVED",), "PROCESSING", "payment.record", verb="sent to bank"),
    "bank_returned": Move(
        ("PROCESSING",), "PAY_APPROVED", "payment.record", True, "returned from bank"
    ),
    "paid": Move(("PROCESSING",), "PAID", "payment.record", verb="paid"),
    "hold": Move(OPEN, "ON_HOLD", "payment.schedule", True, "put on hold"),
    "release": Move(("ON_HOLD",), "PENDING", "payment.schedule", verb="released from hold"),
}


def allowed(e: Expense, principal: Principal) -> list[str]:
    """Payment actions this person may take on this expense right now."""
    if e.status != "APPROVED":
        return []
    out = []
    for name, m in ACTIONS.items():
        if e.payment_status in m.from_states and principal.can(m.permission):
            if name == "approve" and not _independent(e, principal):
                continue
            out.append(name)
    return out


def _independent(e: Expense, principal: Principal) -> bool:
    """Maker-checker: the payment approver is neither the claimant nor who scheduled it."""
    uid = principal.user.id
    return uid != e.owner_id and uid != e.payment_scheduled_by_id


def _load(db: Session, principal: Principal, expense_id: int, permission: str) -> Expense:
    e = (
        db.execute(
            select(Expense)
            .where(Expense.id == expense_id, expense_scope_clause(principal, permission))
            .with_for_update(of=Expense)
        )
        .unique()
        .scalar_one_or_none()
    )
    if e is None:
        raise not_found("Expense")
    return e


def _apply(
    db: Session,
    principal: Principal,
    e: Expense,
    action: str,
    *,
    scheduled_for: date | None,
    mode: str | None,
    reference: str | None,
    paid_on: date | None,
    note: str | None,
    batch_ref: str | None = None,
    now: datetime,
) -> None:
    m = ACTIONS.get(action)
    if m is None:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown payment action {action}")
    if not principal.can(m.permission):
        raise AppError(403, "FORBIDDEN", "You are not allowed to do this payment step")
    if e.status != "APPROVED":
        raise AppError(409, "INVALID_TRANSITION", "Only approved expenses enter payment")
    if e.payment_status not in m.from_states:
        raise AppError(
            409,
            "INVALID_TRANSITION",
            f"{e.expense_no}: can't {action.replace('_', ' ')} a payment that is "
            f"{LABEL.get(e.payment_status, e.payment_status).lower()}",
        )
    if m.needs_note and not (note and len(note.strip()) >= 3):
        raise AppError(422, "VALIDATION_ERROR", "Give a reason (it is shown to the previous step)")
    if action == "approve" and not _independent(e, principal):
        raise AppError(
            403, "FORBIDDEN", "The payment approver can't be the claimant or who scheduled it"
        )

    if action == "schedule":
        if scheduled_for is None or scheduled_for < date.today():
            raise AppError(422, "VALIDATION_ERROR", "Pick a payment date (today or later)")
        if mode is not None and mode not in MODES:
            raise AppError(422, "VALIDATION_ERROR", f"Mode must be one of {', '.join(MODES)}")
        e.payment_scheduled_for = scheduled_for
        e.payment_scheduled_by_id = principal.user.id
        e.payment_mode = mode or e.payment_mode or "NEFT"
        e.payment_approved_by_id = e.payment_approved_at = None
    elif action in ("unschedule", "send_back", "release"):
        e.payment_scheduled_for = e.payment_scheduled_by_id = None
        e.payment_approved_by_id = e.payment_approved_at = None
    elif action == "approve":
        e.payment_approved_by_id, e.payment_approved_at = principal.user.id, now
    elif action == "reopen":
        e.payment_approved_by_id = e.payment_approved_at = None
    elif action == "sent_to_bank":
        e.payment_batch_ref = batch_ref or e.payment_batch_ref
    elif action == "bank_returned":
        e.payment_batch_ref = None
    elif action == "paid":
        mode = mode or e.payment_mode
        if mode not in MODES:
            raise AppError(422, "VALIDATION_ERROR", f"Mode must be one of {', '.join(MODES)}")
        if mode != "CASH" and not (reference and reference.strip()):
            raise AppError(422, "VALIDATION_ERROR", "Enter the UTR / transaction reference")
        if paid_on is None or paid_on > date.today():
            raise AppError(422, "VALIDATION_ERROR", "Enter the payment date (not in the future)")
        e.payment_mode, e.paid_on, e.paid_amount = mode, paid_on, e.total_amount
        e.payment_reference = (reference or "").strip() or None
        e.closed_at = now

    before = e.payment_status
    e.payment_status = m.to
    e.payment_updated_at = e.current_stage_since = e.updated_at = now
    e.payment_updated_by_id = principal.user.id
    db.flush()
    db.add(
        ExpenseHistory(
            expense_id=e.id,
            action=f"PAYMENT_{action.upper()}"[:30],
            from_status=e.status,
            to_status=e.status,
            actor_id=principal.user.id,
            comment=_comment(e, action, note),
            ip_address=get_request_context().ip_address,
        )
    )
    audit_service.record(
        db,
        f"PAYMENT_{action.upper()}",
        "expense",
        e.id,
        actor=principal.user,
        details={
            "from": before,
            "to": m.to,
            "scheduled_for": e.payment_scheduled_for.isoformat()
            if e.payment_scheduled_for
            else None,
            "mode": e.payment_mode,
            "reference": e.payment_reference,
            "batch": e.payment_batch_ref,
            "amount": money_str(e.total_amount),
            "paid_on": e.paid_on.isoformat() if e.paid_on else None,
            "note": note,
        },
    )


def _comment(e: Expense, action: str, note: str | None) -> str | None:
    bits = []
    if action == "schedule" and e.payment_scheduled_for:
        bits.append(f"Payment date {e.payment_scheduled_for:%d %b %Y} by {e.payment_mode}")
    if action == "sent_to_bank" and e.payment_batch_ref:
        bits.append(f"Bank file {e.payment_batch_ref}")
    if action == "paid":
        bits.append(
            f"₹{e.paid_amount:,.2f} by {e.payment_mode}"
            + (f" · UTR {e.payment_reference}" if e.payment_reference else "")
        )
    if note:
        bits.append(note.strip())
    return " · ".join(bits) or None


def act(
    db: Session,
    principal: Principal,
    expense_id: int,
    action: str,
    *,
    scheduled_for: date | None = None,
    mode: str | None = None,
    reference: str | None = None,
    paid_on: date | None = None,
    note: str | None = None,
    version: int | None = None,
) -> dict:
    m = ACTIONS.get(action)
    if m is None:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown payment action {action}")
    if not principal.can(m.permission):
        raise AppError(403, "FORBIDDEN", "You are not allowed to do this payment step")
    e = _load(db, principal, expense_id, m.permission)
    expense_rules.check_version(e, version)
    _apply(
        db,
        principal,
        e,
        action,
        scheduled_for=scheduled_for,
        mode=mode,
        reference=reference,
        paid_on=paid_on,
        note=note,
        now=utcnow(),
    )
    _notify(db, principal, [e], action, note)
    body = serialize(db, e.id)
    db.commit()
    return body


def bulk(
    db: Session,
    principal: Principal,
    expense_ids: list[int],
    action: str,
    *,
    scheduled_for: date | None = None,
    mode: str | None = None,
    note: str | None = None,
) -> dict:
    """One action on many payments, all-or-nothing per item (each item its own savepoint)."""
    m = ACTIONS.get(action)
    if m is None or action in ("paid", "sent_to_bank"):
        raise AppError(422, "VALIDATION_ERROR", f"{action} can't be done in bulk here")
    if not principal.can(m.permission):
        raise AppError(403, "FORBIDDEN", "You are not allowed to do this payment step")
    done, skipped, moved = [], [], []
    now = utcnow()
    for expense_id in dict.fromkeys(expense_ids):
        try:
            with db.begin_nested():
                e = _load(db, principal, expense_id, m.permission)
                _apply(
                    db,
                    principal,
                    e,
                    action,
                    scheduled_for=scheduled_for,
                    mode=mode,
                    reference=None,
                    paid_on=None,
                    note=note,
                    now=now,
                )
            done.append(expense_id)
            moved.append(e)
        except AppError as err:
            skipped.append({"id": expense_id, "code": err.code, "reason": err.detail})
    if moved:
        _notify(db, principal, moved, action, note)
    db.commit()
    return {"done": done, "skipped": skipped}


def _notify(
    db: Session, principal: Principal, items: list[Expense], action: str, note: str | None
) -> None:
    total = sum((e.total_amount for e in items), Decimal("0.00"))
    n = len(items)
    what = f"{n} payment{'s' if n != 1 else ''} · ₹{total:,.2f}"
    first = items[0]
    if action == "schedule":
        when = first.payment_scheduled_for
        engagement_service.notify(
            db,
            engagement_service.holders_of(db, "payment.approve"),
            "PAYMENT_SCHEDULED",
            f"Payments to approve for {when:%d %b %Y}",
            f"{what} scheduled by {principal.user.full_name}",
            first.id if n == 1 else None,
        )
    elif action == "approve":
        engagement_service.notify(
            db,
            {e.payment_scheduled_by_id for e in items},
            "PAYMENT_APPROVED",
            "Payments approved - download the bank file",
            f"{what} approved by {principal.user.full_name}",
            first.id if n == 1 else None,
        )
    elif action == "send_back":
        engagement_service.notify(
            db,
            engagement_service.holders_of(db, "payment.schedule"),
            "PAYMENT_SENT_BACK",
            f"Payment sent back: {first.title}" if n == 1 else f"{n} payments sent back",
            f"{principal.user.full_name}: {note}",
            first.id if n == 1 else None,
        )
    elif action == "reopen":
        engagement_service.notify(
            db,
            engagement_service.holders_of(db, "payment.approve"),
            "PAYMENT_SENT_BACK",
            f"Payment back for approval: {first.title}",
            f"{principal.user.full_name}: {note}",
            first.id,
        )
    elif action == "paid":
        engagement_service.notify(
            db,
            [first.owner_id],
            "PAID",
            f"Paid: {first.title}",
            f"₹{first.paid_amount:,.2f} by {first.payment_mode}"
            + (f" · UTR {first.payment_reference}" if first.payment_reference else ""),
            first.id,
        )
    elif action == "hold":
        engagement_service.notify(
            db,
            [e.owner_id for e in items],
            "PAYMENT_ON_HOLD",
            f"Payment on hold: {first.title}" if n == 1 else "Payment on hold",
            note,
            first.id if n == 1 else None,
        )


# ---------- queues, summary, bank file ----------


def scope_permission(principal: Principal) -> str:
    perm = next((p for p in PAYMENT_PERMS if principal.can(p)), None)
    if perm is None:
        raise AppError(403, "FORBIDDEN", "You have no payment rights")
    return perm


def queue_query(
    principal: Principal,
    payment_status: str,
    scheduled_from: date | None = None,
    scheduled_to: date | None = None,
) -> Select:
    stmt = select(Expense).where(
        Expense.status == "APPROVED",
        Expense.payment_status == payment_status,
        expense_scope_clause(principal, scope_permission(principal)),
    )
    if scheduled_from:
        stmt = stmt.where(Expense.payment_scheduled_for >= scheduled_from)
    if scheduled_to:
        stmt = stmt.where(Expense.payment_scheduled_for <= scheduled_to)
    return stmt


def summary(db: Session, principal: Principal) -> dict[str, Any]:
    scope = expense_scope_clause(principal, scope_permission(principal))
    rows = db.execute(
        select(
            Expense.payment_status, func.count(), func.coalesce(func.sum(Expense.total_amount), 0)
        )
        .where(Expense.status == "APPROVED", scope)
        .group_by(Expense.payment_status)
    ).all()
    today = date.today()
    due = db.execute(
        select(func.count(), func.coalesce(func.sum(Expense.total_amount), 0)).where(
            Expense.status == "APPROVED",
            Expense.payment_status == "SCHEDULED",
            Expense.payment_scheduled_for <= today,
            scope,
        )
    ).one()
    by = {r[0]: {"count": r[1], "amount": money_str(Decimal(r[2]))} for r in rows}
    return {
        "by_status": {
            s: by.get(s, {"count": 0, "amount": "0.00"}) for s in (*OPEN, "PAID", "ON_HOLD")
        },
        "approval_due_today": {"count": due[0], "amount": money_str(Decimal(due[1]))},
    }


def payee_of(e: Expense) -> str:
    names = list(
        dict.fromkeys(
            (i.vendor.name if i.vendor else None) or i.supplier_name or "" for i in e.items
        )
    )
    names = [n for n in names if n]
    return names[0] if len(names) == 1 else f"{e.owner.full_name} (reimbursement)"


BANK_COLUMNS = [
    "Batch ref",
    "Payment ref",
    "Value date",
    "Beneficiary name",
    "Beneficiary account no.",
    "IFSC",
    "Amount",
    "Mode",
    "Narration",
    "Initiator",
    "Initiator e-mail",
]


def bank_file(
    db: Session, principal: Principal, expense_ids: list[int] | None
) -> tuple[str, list[list[Any]]]:
    """Generic bank upload sheet for MD-approved payments; marks them sent to bank."""
    if not principal.can("payment.record"):
        raise AppError(403, "FORBIDDEN", "You are not allowed to download the bank file")
    stmt = queue_query(principal, "PAY_APPROVED").order_by(
        Expense.payment_scheduled_for, Expense.id
    )
    if expense_ids:
        stmt = stmt.where(Expense.id.in_(expense_ids))
    items = list(db.execute(stmt.with_for_update(of=Expense)).unique().scalars())
    if not items:
        raise AppError(409, "INVALID_TRANSITION", "No payments approved for payment to download")
    now = utcnow()
    batch = f"PAY-{datetime.now():%Y%m%d-%H%M%S}"  # local time: what finance sees
    rows = []
    for e in items:
        rows.append(
            [
                batch,
                e.expense_no,
                e.payment_scheduled_for.isoformat() if e.payment_scheduled_for else "",
                payee_of(e),
                "",  # demo: bank details are not kept in the portal yet
                "",
                money_str(e.total_amount),
                e.payment_mode or "NEFT",
                f"{e.expense_no} {e.title}"[:60],
                e.owner.full_name,
                e.owner.email,
            ]
        )
        _apply(
            db,
            principal,
            e,
            "sent_to_bank",
            scheduled_for=None,
            mode=None,
            reference=None,
            paid_on=None,
            note=None,
            batch_ref=batch,
            now=now,
        )
    audit_service.record(
        db,
        "BANK_FILE_DOWNLOADED",
        "payment_batch",
        batch,
        actor=principal.user,
        details={
            "count": len(items),
            "total": money_str(sum((e.total_amount for e in items), Decimal("0.00"))),
            "expenses": [e.expense_no for e in items],
        },
    )
    db.commit()
    return batch, rows
