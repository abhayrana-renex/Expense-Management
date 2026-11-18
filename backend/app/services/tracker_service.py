"""Order-style status tracker for an expense: Submitted -> L1 -> L2 -> L3 -> Approved ->
Payment processing -> Paid. Built only from recorded facts (approval steps and history)."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.security import utcnow
from app.models import ApprovalStep, Expense, ExpenseHistory
from app.services.expense_service import get_visible

PAYMENT_LABEL = {
    "NEFT": "NEFT",
    "RTGS": "RTGS",
    "IMPS": "IMPS",
    "UPI": "UPI",
    "CHEQUE": "cheque",
    "CASH": "cash",
    "OTHER": "other",
}


def _waited(since: datetime | None) -> str | None:
    if since is None:
        return None
    secs = int((utcnow() - since).total_seconds())
    if secs < 3600:
        return f"{max(1, secs // 60)} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    days = secs // 86400
    return f"{days} day{'s' if days != 1 else ''}"


def _stage(
    key: str,
    label: str,
    state: str,
    at: datetime | None = None,
    actor: str | None = None,
    note: str | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "state": state,
        "at": at,
        "actor": actor,
        "note": note,
        "comment": comment,
    }


def build(db: Session, principal: Principal, expense_id: int) -> dict[str, Any]:
    e: Expense = get_visible(db, principal, expense_id)
    steps = (
        list(
            db.execute(
                select(ApprovalStep)
                .where(ApprovalStep.expense_id == e.id, ApprovalStep.cycle == e.cycle)
                .order_by(ApprovalStep.sequence)
            )
            .unique()
            .scalars()
        )
        if e.cycle
        else []
    )
    history = list(
        db.execute(
            select(ExpenseHistory)
            .where(ExpenseHistory.expense_id == e.id)
            .order_by(ExpenseHistory.id)
        )
        .unique()
        .scalars()
    )
    last = {h.action: h for h in history}
    stages: list[dict[str, Any]] = []

    created = last.get("CREATED")
    stages.append(
        _stage(
            "created",
            "Created",
            "done",
            e.created_at,
            created.actor.full_name if created else e.owner.full_name,
        )
    )

    # Submitted
    sub = last.get("RESUBMITTED") or last.get("SUBMITTED")
    if e.status in ("DRAFT", "RETURNED") or e.cycle == 0:
        returned = last.get("RETURNED") or last.get("REOPENED")
        note = (
            "Returned to you - make the changes and resubmit"
            if e.status == "RETURNED"
            else "Waiting for you to submit"
        )
        stages.append(
            _stage(
                "submitted",
                "Submitted",
                "current",
                None,
                e.owner.full_name,
                note,
                returned.comment if returned and e.status == "RETURNED" else None,
            )
        )
    else:
        stages.append(
            _stage(
                "submitted",
                "Resubmitted" if e.cycle > 1 else "Submitted",
                "done",
                sub.created_at if sub else e.submitted_at,
                e.owner.full_name,
            )
        )

    # Approval levels (current cycle's route; planned route if not yet submitted)
    final_state = e.status
    for s in steps:
        label = f"{s.level.code} approval"
        name = s.approver.full_name
        if s.acted_by is not None and s.acted_by_id != s.approver_id:
            name = f"{s.acted_by.full_name} for {s.approver.full_name}"
        if s.status == "APPROVED":
            stages.append(
                _stage(f"step-{s.id}", label, "done", s.acted_at, name, s.note, s.comment)
            )
        elif s.status == "PENDING":
            stages.append(
                _stage(
                    f"step-{s.id}",
                    label,
                    "current",
                    s.activated_at,
                    name,
                    (
                        f"Waiting {_waited(s.activated_at or e.current_stage_since)}"
                        if (s.activated_at or e.current_stage_since)
                        else "Waiting"
                    )
                    + (f" · {s.note}" if s.note else ""),
                    s.comment if s.note and s.note.startswith("Sent back") else None,
                )
            )
        elif s.status == "SKIPPED":
            stages.append(_stage(f"step-{s.id}", label, "skipped", None, name, s.note))
        elif s.status in ("REJECTED", "RETURNED"):
            stages.append(
                _stage(
                    f"step-{s.id}",
                    label,
                    "failed",
                    s.acted_at,
                    name,
                    "Rejected" if s.status == "REJECTED" else "Returned for changes",
                    s.comment,
                )
            )
        elif s.status == "WAITING":
            stages.append(_stage(f"step-{s.id}", label, "upcoming", None, name))
        # CANCELLED steps (after a return/reject/cancel) are not shown as stages
    if not steps and e.status in ("DRAFT", "RETURNED"):
        stages.append(
            _stage(
                "approval", "Approval", "upcoming", None, None, "Route is decided when you submit"
            )
        )

    # Outcome + payment
    if final_state == "REJECTED":
        h = last.get("REJECTED")
        stages.append(
            _stage(
                "rejected",
                "Rejected",
                "failed",
                e.closed_at,
                h.actor.full_name if h else None,
                None,
                h.comment if h else None,
            )
        )
    elif final_state == "CANCELLED":
        h = last.get("CANCELLED")
        stages.append(
            _stage(
                "cancelled",
                "Cancelled",
                "failed",
                e.closed_at,
                h.actor.full_name if h else None,
                None,
                h.comment if h else None,
            )
        )
    else:
        approved = final_state == "APPROVED"
        stages.append(
            _stage("approved", "Approved", "done" if approved else "upcoming", e.approved_at)
        )
        stages.extend(_payment_stages(e, approved, last))

    return {
        "expense_id": e.id,
        "expense_no": e.expense_no,
        "status": e.status,
        "payment_status": e.payment_status,
        "headline": _headline(e, steps),
        "stages": stages,
    }


PAY_RANK = {
    "PENDING": 0,
    "ON_HOLD": 0,
    "SCHEDULED": 1,
    "PAY_APPROVED": 2,
    "PROCESSING": 3,
    "PAID": 4,
}


PUSH_BACKS = ("PAYMENT_SEND_BACK", "PAYMENT_REOPEN", "PAYMENT_BANK_RETURNED")


def _payment_stages(e: Expense, approved: bool, last: dict) -> list[dict[str, Any]]:
    """Approved -> scheduled (finance) -> payment approval (MD) -> sent to bank -> paid."""
    labels = ["Payment scheduled", "Payment approval", "Sent to bank", "Paid"]
    keys = ["pay-scheduled", "pay-approved", "pay-bank", "paid"]
    if not approved:
        return [_stage(k, lbl, "upcoming") for k, lbl in zip(keys, labels, strict=True)]
    rank = PAY_RANK.get(e.payment_status, 0)
    hold = e.payment_status == "ON_HOLD"
    waited = _waited(e.current_stage_since)
    sched, bank = last.get("PAYMENT_SCHEDULE"), last.get("PAYMENT_SENT_TO_BANK")
    done_info = [
        (
            sched.created_at if sched else None,
            e.payment_scheduled_by.full_name if e.payment_scheduled_by else None,
            f"For {e.payment_scheduled_for:%d %b %Y} by {e.payment_mode}"
            if e.payment_scheduled_for
            else None,
        ),
        (
            e.payment_approved_at,
            e.payment_approved_by.full_name if e.payment_approved_by else None,
            None,
        ),
        (
            bank.created_at if bank else None,
            bank.actor.full_name if bank else None,
            f"Bank file {e.payment_batch_ref}" if e.payment_batch_ref else None,
        ),
        (
            e.payment_updated_at if e.payment_status == "PAID" else None,
            None,
            (
                f"₹{e.paid_amount:,.2f} by {PAYMENT_LABEL.get(e.payment_mode or '', '')}"
                + (f" · UTR {e.payment_reference}" if e.payment_reference else "")
                + (f" · on {e.paid_on:%d %b %Y}" if e.paid_on else "")
            )
            if e.paid_amount
            else None,
        ),
    ]
    current_note = [
        "Waiting for finance to schedule the payment",
        "Waiting for the payment approver"
        + (f" · due {e.payment_scheduled_for:%d %b %Y}" if e.payment_scheduled_for else ""),
        "Approved for payment - bank file next",
        "Sent to bank - waiting for the UTR",
    ]
    out = []
    for i, (key, label) in enumerate(zip(keys, labels, strict=True), start=1):
        if rank >= i:
            at, actor, note = done_info[i - 1]
            out.append(_stage(key, label, "done", at, actor, note))
        elif rank == i - 1:
            # The latest payment event explains the current stage if it was a push-back.
            latest = max(
                (h for k, h in last.items() if k.startswith("PAYMENT_")),
                key=lambda h: h.id,
                default=None,
            )
            comment = None
            if hold and "PAYMENT_HOLD" in last:
                comment = last["PAYMENT_HOLD"].comment
            elif latest is not None and latest.action in PUSH_BACKS:
                comment = latest.comment
            note = "On hold" if hold else current_note[i - 1]
            out.append(
                _stage(
                    key,
                    label,
                    "failed" if hold else "current",
                    e.current_stage_since,
                    None,
                    f"{note}{f' · {waited}' if waited else ''}",
                    comment,
                )
            )
        else:
            out.append(_stage(key, label, "upcoming"))
    return out


def _headline(e: Expense, steps: list[ApprovalStep]) -> str:
    if e.status == "DRAFT":
        return "Draft - not submitted yet"
    if e.status == "RETURNED":
        return "Returned to you for changes"
    if e.status == "REJECTED":
        return "Rejected"
    if e.status == "CANCELLED":
        return "Cancelled"
    if e.status.startswith("PENDING_"):
        who = e.current_approver.full_name if e.current_approver else "approver"
        waited = _waited(e.current_stage_since)
        remaining = sum(1 for s in steps if s.status == "WAITING")
        more = (
            f" · {remaining} more level{'s' if remaining != 1 else ''} after this"
            if remaining
            else " · last approval"
        )
        return f"Pending with {who} ({e.status[8:]}){f' for {waited}' if waited else ''}{more}"
    if e.payment_status == "PAID":
        return f"Paid{f' on {e.paid_on:%d %b %Y}' if e.paid_on else ''}"
    if e.payment_status == "PROCESSING":
        return "Payment sent to bank - waiting for the UTR"
    if e.payment_status == "PAY_APPROVED":
        return "Payment approved - bank file next"
    if e.payment_status == "SCHEDULED" and e.payment_scheduled_for:
        return f"Payment scheduled for {e.payment_scheduled_for:%d %b %Y} - awaiting approval"
    if e.payment_status == "ON_HOLD":
        return "Approved - payment on hold"
    return "Approved - waiting to be scheduled for payment"
