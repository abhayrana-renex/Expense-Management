"""Approve / reject / return / cancel / reopen, history and the approver queue."""

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.context import get_request_context
from app.core.deps import Principal
from app.core.errors import AppError
from app.core.security import utcnow
from app.models import ApprovalStep, Expense, ExpenseHistory, Partner
from app.schemas.common import money_str
from app.schemas.expense import CorrectionRequest
from app.services import (
    audit_service,
    engagement_service,
    expense_rules,
    idempotency_service,
    invoice_check,
    routing_service,
)
from app.services.expense_service import get_visible, serialize
from app.services.idempotency_service import IdempotencyContext


def _steps(db: Session, expense: Expense) -> list[ApprovalStep]:
    return list(
        db.execute(
            select(ApprovalStep)
            .where(ApprovalStep.expense_id == expense.id, ApprovalStep.cycle == expense.cycle)
            .order_by(ApprovalStep.sequence)
            .with_for_update(of=ApprovalStep)
        )
        .unique()
        .scalars()
    )


def _current_step(steps: list[ApprovalStep], expense: Expense) -> ApprovalStep:
    step = next((s for s in steps if s.status == "PENDING"), None)
    if step is None or step.approver_id != expense.current_approver_id:
        raise AppError(409, "INVALID_TRANSITION", "Expense has no pending approval step")
    return step


def _record(
    db: Session,
    principal: Principal,
    expense: Expense,
    action: str,
    from_status: str,
    comment: str | None,
    level_code: str | None,
) -> None:
    db.add(
        ExpenseHistory(
            expense_id=expense.id,
            action=action,
            from_status=from_status,
            to_status=expense.status,
            level_code=level_code,
            actor_id=principal.user.id,
            comment=comment,
            ip_address=get_request_context().ip_address,
        )
    )
    audit_service.record(
        db,
        f"EXPENSE_{action}",
        "expense",
        expense.id,
        actor=principal.user,
        details={
            "from": from_status,
            "to": expense.status,
            "level": level_code,
            "comment": comment,
        },
    )


def _close_open_steps(steps: list[ApprovalStep]) -> None:
    for s in steps:
        if s.status in ("WAITING", "PENDING"):
            s.status = "CANCELLED"


def _load(
    db: Session, principal: Principal, expense_id: int, action: str, version: int | None
) -> Expense:
    expense = get_visible(db, principal, expense_id, for_update=True)
    expense_rules.check_version(expense, version)
    expense_rules.assert_allowed(action, expense, principal)
    return expense


def act(
    db: Session,
    principal: Principal,
    expense_id: int,
    action: str,
    comment: str,
    version: int | None,
    idem: IdempotencyContext | None = None,
) -> dict:
    expense = _load(db, principal, expense_id, action, version)
    from_status = expense.status
    now = utcnow()
    level_code = expense.current_level.code if expense.current_level else None

    sent_to: ApprovalStep | None = None
    if action in ("approve", "reject", "return", "send_back"):
        steps = _steps(db, expense)
        step = _current_step(steps, expense)
        step.acted_by_id = principal.user.id
        step.acted_at = now
        step.comment = comment
        if step.approver_id != principal.user.id:  # acting under an out-of-office delegation
            step.note = f"By {principal.user.full_name} on behalf of {step.approver.full_name}"
            comment = f"{comment} (on behalf of {step.approver.full_name})"
        if action == "approve":
            step.status = "APPROVED"
            nxt = next(
                (s for s in steps if s.sequence > step.sequence and s.status == "WAITING"), None
            )
            expense.current_stage_since = now
            if nxt is not None:
                nxt.status = "PENDING"
                nxt.activated_at = now
                expense.status = f"PENDING_{nxt.level.code}"
                expense.current_level_id = nxt.level_id
                expense.current_approver_id = nxt.approver_id
            else:
                expense.status = "APPROVED"
                expense.approved_at = now
                expense.current_level_id = None
                expense.current_approver_id = None
                expense.payment_status = "PENDING"  # now queued for finance to pay
            history_action = "APPROVED"
        elif action == "send_back" and (prev := _previous(steps, step)) is not None:
            # Push back one step: the previous approver gets it again; nothing is cancelled.
            sent_to = prev
            for s_ in (step, prev):
                s_.acted_by_id = None
                s_.acted_at = None
            step.status, step.activated_at, step.comment = "WAITING", None, None
            prev.status, prev.activated_at = "PENDING", now
            prev.comment = comment
            prev.note = f"Sent back by {principal.user.full_name}"[:255]
            expense.status = f"PENDING_{prev.level.code}"
            expense.current_level_id = prev.level_id
            expense.current_approver_id = prev.approver_id
            expense.current_stage_since = now
            history_action = "SENT_BACK"
        else:
            if action == "send_back":  # first approver: back to the employee
                action = "return"
            step.status = "REJECTED" if action == "reject" else "RETURNED"
            _close_open_steps(steps)
            expense.status = step.status
            expense.current_level_id = None
            expense.current_approver_id = None
            if action == "reject":
                expense.closed_at = now
            history_action = step.status
    elif action == "cancel":
        was_with = expense.current_approver_id
        _close_open_steps(_steps(db, expense))
        expense.status = "CANCELLED"
        expense.closed_at = now
        expense.current_level_id = None
        expense.current_approver_id = None
        history_action = "CANCELLED"
    elif action == "reopen":
        expense.status = "RETURNED"
        expense.closed_at = None
        history_action = "REOPENED"
        level_code = None
    else:  # pragma: no cover - guarded by the route layer
        raise AppError(400, "INVALID_TRANSITION", f"Unknown action {action}")

    expense.updated_at = now
    db.flush()
    _record(db, principal, expense, history_action, from_status, comment, level_code)
    if sent_to is not None:
        _notify_sent_back(db, expense, sent_to, principal, comment)
    else:
        _notify(db, expense, action, comment, was_with if action == "cancel" else None)
    body = serialize(db, expense.id)
    idempotency_service.store(db, idem, 200, body)
    db.commit()
    return body


def _previous(steps: list[ApprovalStep], step: ApprovalStep) -> ApprovalStep | None:
    """The last approver before this step who approved it in the current cycle."""
    return next(
        (s for s in reversed(steps) if s.sequence < step.sequence and s.status == "APPROVED"),
        None,
    )


def send_back_target(db: Session, expense: Expense) -> str | None:
    """Who a send-back would reach now: the previous approver, or the employee."""
    if not expense.status.startswith("PENDING_"):
        return None
    steps = list(
        db.execute(
            select(ApprovalStep)
            .where(ApprovalStep.expense_id == expense.id, ApprovalStep.cycle == expense.cycle)
            .order_by(ApprovalStep.sequence)
        )
        .unique()
        .scalars()
    )
    current = next((s for s in steps if s.status == "PENDING"), None)
    prev = _previous(steps, current) if current else None
    if prev is None:
        return f"{expense.owner.full_name} (employee)"
    return f"{prev.approver.full_name} ({prev.level.code})"


def _notify_sent_back(
    db: Session, e: Expense, to: ApprovalStep, by: Principal, comment: str
) -> None:
    engagement_service.notify(
        db,
        [to.approver_id],
        "SENT_BACK",
        f"Sent back to you: {e.title}",
        f"{by.user.full_name}: {comment}",
        e.id,
    )
    engagement_service.notify(
        db,
        [e.owner_id],
        "SENT_BACK",
        f"Sent back to {to.approver.full_name} for a re-check: {e.title}",
        comment,
        e.id,
    )


def _notify(db: Session, e: Expense, action: str, comment: str, was_with: int | None) -> None:
    owner = [e.owner_id]
    if action == "approve" and e.status.startswith("PENDING_"):
        engagement_service.notify_pending(db, e)
    elif action == "approve":
        engagement_service.notify(
            db,
            owner,
            "APPROVED",
            f"Approved: {e.title}",
            f"{engagement_service.tag(e)} · queued for payment",
            e.id,
        )
        engagement_service.notify(
            db,
            engagement_service.holders_of(db, "payment.schedule"),
            "READY_TO_PAY",
            f"Ready to schedule for payment: {e.title}",
            f"{e.owner.full_name} · {engagement_service.tag(e)}",
            e.id,
        )
    elif action == "reject":
        engagement_service.notify(db, owner, "REJECTED", f"Rejected: {e.title}", comment, e.id)
    elif action == "return":
        engagement_service.notify(
            db, owner, "RETURNED", f"Returned for changes: {e.title}", comment, e.id
        )
    elif action == "reopen":
        engagement_service.notify(db, owner, "REOPENED", f"Reopened: {e.title}", comment, e.id)
    elif action == "cancel" and was_with:
        engagement_service.notify(
            db,
            [was_with],
            "WITHDRAWN",
            f"Withdrawn: {e.title}",
            f"{e.owner.full_name} cancelled it · {comment}",
            e.id,
        )


def bulk_approve(db: Session, principal: Principal, expense_ids: list[int], comment: str) -> dict:
    """Approve several clean, small expenses at once. Anything risky is skipped, with a reason."""
    approved, skipped = [], []
    limit = settings.BULK_APPROVE_MAX_AMOUNT
    for expense_id in dict.fromkeys(expense_ids):
        try:
            e = get_visible(db, principal, expense_id)
            if not expense_rules.allowed_actions(e, principal)["approve"]:
                raise AppError(409, "INVALID_TRANSITION", "Not waiting for your approval")
            if e.total_amount > limit:
                raise AppError(
                    422,
                    "OVER_BULK_LIMIT",
                    f"Above the bulk limit of ₹{money_str(limit)} - review it on its own",
                )
            flags = [
                i.message for i in expense_rules.validate_for_submit(db, e) if i.severity == "WARN"
            ]
            if flags:
                raise AppError(422, "HAS_WARNINGS", f"Needs a look: {flags[0]}")
            act(db, principal, expense_id, "approve", comment, e.version)
            approved.append(expense_id)
        except AppError as err:
            db.rollback()
            skipped.append({"id": expense_id, "code": err.code, "reason": err.detail})
    return {"approved": approved, "skipped": skipped}


def history(db: Session, principal: Principal, expense_id: int) -> list[ExpenseHistory]:
    get_visible(db, principal, expense_id)
    return list(
        db.execute(
            select(ExpenseHistory)
            .where(ExpenseHistory.expense_id == expense_id)
            .order_by(ExpenseHistory.id)
        )
        .unique()
        .scalars()
    )


def route(db: Session, principal: Principal, expense_id: int) -> list[ApprovalStep]:
    expense = get_visible(db, principal, expense_id)
    return list(
        db.execute(
            select(ApprovalStep)
            .where(ApprovalStep.expense_id == expense.id)
            .order_by(ApprovalStep.cycle.desc(), ApprovalStep.sequence)
        )
        .unique()
        .scalars()
    )


def queue_query(principal: Principal) -> Select:
    """Expenses waiting for this user's decision right now."""
    approvers = {principal.user.id, *principal.acting_for}
    return select(Expense).where(
        Expense.current_approver_id.in_(approvers),
        Expense.owner_id != principal.user.id,
        Expense.status.like("PENDING\\_%"),
    )


CORRECTABLE = (
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
)


def _text(v: object) -> str | None:
    return None if v is None else str(v)


def _resync_route(db: Session, expense: Expense, steps: list[ApprovalStep]) -> list[str]:
    """After an amount change, add or skip the later levels the new total needs (e.g. MD
    above the limit). Steps already approved or the current one are never touched."""
    planned = routing_service.plan_route(db, expense)
    needed = {p.rule.id: p for p in planned if p.status != "SKIPPED"}
    current = next(s for s in steps if s.status == "PENDING")
    changes: list[str] = []
    for s in steps:
        if s.sequence <= current.sequence:
            continue
        if s.status == "WAITING" and s.rule_id not in needed:
            s.status, s.note = "SKIPPED", AFTER_CORRECTION
            changes.append(f"{s.level.code} no longer needed")
        elif s.status == "SKIPPED" and s.note == AFTER_CORRECTION and s.rule_id in needed:
            s.status, s.note = "WAITING", None
            changes.append(f"{s.level.code} needed again")
    have = {s.rule_id for s in steps}
    seq = max(s.sequence for s in steps)
    for rule_id, p in needed.items():
        if rule_id in have or p.rule.level.rank <= current.level.rank:
            continue
        seq += 1
        db.add(
            ApprovalStep(
                expense_id=expense.id,
                cycle=expense.cycle,
                sequence=seq,
                level_id=p.rule.level_id,
                approver_id=p.rule.approver_id,
                rule_id=p.rule.id,
                status="WAITING",
                note="Added after correction",
            )
        )
        changes.append(f"{p.rule.level.code} added ({p.rule.approver.full_name})")
    return changes


AFTER_CORRECTION = "Not needed after correction"


def correct(db: Session, principal: Principal, expense_id: int, data: CorrectionRequest) -> dict:
    """Finance check (F&A ratification) corrects bill details while the claim is with them.
    Every changed value is audited old -> new; the employee is told what changed."""
    expense = _load(db, principal, expense_id, "correct", data.version)
    by_id = {i.id: i for i in expense.items}
    changed: dict[str, dict[str, list[str | None]]] = {}
    for src in data.items:
        item = by_id.get(src.id)
        if item is None:
            raise AppError(422, "VALIDATION_ERROR", f"Line item {src.id} is not on this expense")
        if src.vendor_id and src.vendor_id != item.vendor_id:
            vendor = db.get(Partner, src.vendor_id)
            if vendor is None or not vendor.is_active:
                raise AppError(422, "VALIDATION_ERROR", "Vendor does not exist or is inactive")
        diff = {}
        for field in CORRECTABLE:
            old, new = getattr(item, field), getattr(src, field)
            if _text(old) != _text(new):
                diff[field] = [_text(old), _text(new)]
                setattr(item, field, new)
        if diff:
            changed[f"line {item.line_no}"] = diff
    if not changed:
        raise AppError(422, "VALIDATION_ERROR", "Nothing was changed")
    before_total = expense.total_amount
    expense_rules.recalc_totals(expense)
    expense.updated_at = utcnow()
    db.flush()
    db.expire(expense, ["items"])
    invoice_check.assert_no_duplicates(db, expense, expense.owner_id)
    for item in expense.items:
        item.snapshot_vendor_name = item.vendor.name if item.vendor else item.supplier_name
        item.snapshot_vendor_gstin = (
            item.vendor.gstin if item.vendor else None
        ) or item.supplier_gstin
    route_changes = (
        _resync_route(db, expense, _steps(db, expense))
        if expense.total_amount != before_total
        else []
    )
    level_code = expense.current_level.code if expense.current_level else None
    note = data.comment
    if expense.total_amount != before_total:
        note += f" (total {money_str(before_total)} -> {money_str(expense.total_amount)})"
    if route_changes:
        note += "; route: " + ", ".join(route_changes)
    db.add(
        ExpenseHistory(
            expense_id=expense.id,
            action="CORRECTED",
            from_status=expense.status,
            to_status=expense.status,
            level_code=level_code,
            actor_id=principal.user.id,
            comment=note,
            ip_address=get_request_context().ip_address,
        )
    )
    audit_service.record(
        db,
        "EXPENSE_CORRECTED",
        "expense",
        expense.id,
        actor=principal.user,
        details={
            "changes": changed,
            "total": [money_str(before_total), money_str(expense.total_amount)],
            "route": route_changes,
            "comment": data.comment,
        },
    )
    engagement_service.notify(
        db,
        [expense.owner_id],
        "CORRECTED",
        f"Finance corrected your claim: {expense.title}",
        note,
        expense.id,
    )
    body = serialize(db, expense.id)
    db.commit()
    return body
