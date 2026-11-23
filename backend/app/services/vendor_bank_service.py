"""Vendor bank accounts, maker-checker.

* A master admin (master.manage) enters a new account or a change: it is PENDING and is not
  used for anything yet.
* Someone else holding vendor_bank.approve (Finance) approves it: it becomes ACTIVE and the
  previous account SUPERSEDED. The maker can never approve their own entry (also a DB check).
* Nothing is edited or deleted; every step is audited, and the bank file uses only the
  ACTIVE account.
* The full account number is shown only to payment roles; everyone else sees XXXXXX1234.
"""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import utcnow
from app.models import Partner, User, VendorBankAccount
from app.models.vendor_master import ACCOUNT_TYPES
from app.services import audit_service, engagement_service

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
ACCOUNT_RE = re.compile(r"^[0-9]{6,18}$")
FULL_VIEW = ("vendor_bank.approve", "payment.schedule", "payment.approve", "payment.record")


def sees_full_number(principal: Principal) -> bool:
    return any(principal.can(p) for p in FULL_VIEW)


def mask(account_no: str) -> str:
    return "X" * max(len(account_no) - 4, 2) + account_no[-4:]


def _name(db: Session, user_id: int | None) -> str | None:
    u = db.get(User, user_id) if user_id else None
    return u.full_name if u else None


def serialize(db: Session, principal: Principal, a: VendorBankAccount) -> dict:
    return {
        "id": a.id,
        "account_type": a.account_type,
        "account_no": a.account_no if sees_full_number(principal) else mask(a.account_no),
        "ifsc": a.ifsc,
        "bank_name": a.bank_name,
        "bank_branch": a.bank_branch,
        "signing_authority": a.signing_authority,
        "state": a.state,
        "wef": a.wef,
        "status": a.status,
        "requested_by": _name(db, a.requested_by_id),
        "requested_at": a.created_at,
        "decided_by": _name(db, a.decided_by_id),
        "decided_at": a.decided_at,
        "decision_note": a.decision_note,
        "can_decide": a.status == "PENDING"
        and principal.can("vendor_bank.approve")
        and a.requested_by_id != principal.user.id,
    }


def history(db: Session, partner_id: int) -> list[VendorBankAccount]:
    return list(
        db.execute(
            select(VendorBankAccount)
            .where(VendorBankAccount.partner_id == partner_id)
            .order_by(VendorBankAccount.id.desc())
        ).scalars()
    )


def active_account(
    db: Session, partner_id: int, on: date | None = None
) -> VendorBankAccount | None:
    """The approved account in use (for the bank file), if it is effective on `on`."""
    acc = db.execute(
        select(VendorBankAccount).where(
            VendorBankAccount.partner_id == partner_id, VendorBankAccount.status == "ACTIVE"
        )
    ).scalar_one_or_none()
    if acc is None or (on is not None and acc.wef > on):
        return None
    return acc


def _clean(data: dict) -> dict:
    out = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items()}
    out["ifsc"] = (out.get("ifsc") or "").upper()
    out["account_no"] = re.sub(r"\s", "", out.get("account_no") or "")
    errors = []
    if out.get("account_type") not in ACCOUNT_TYPES:
        errors.append({"field": "account_type", "msg": "Savings, Current, CC or OD"})
    if not ACCOUNT_RE.fullmatch(out["account_no"]):
        errors.append({"field": "account_no", "msg": "6 to 18 digits"})
    if not IFSC_RE.fullmatch(out["ifsc"]):
        errors.append({"field": "ifsc", "msg": "11 characters, e.g. HDFC0001234"})
    if not out.get("bank_name"):
        errors.append({"field": "bank_name", "msg": "Required"})
    if not out.get("wef"):
        errors.append({"field": "wef", "msg": "Required"})
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Check the bank details", errors)
    for key in ("bank_branch", "signing_authority", "state"):
        out[key] = out.get(key) or None
    return out


def request(db: Session, principal: Principal, partner_id: int, data: dict) -> dict:
    """Maker: enter a new account or a change. Waits for a second person's approval."""
    vendor = db.get(Partner, partner_id)
    if vendor is None:
        raise not_found("Vendor")
    clean = _clean(data)
    waiting = db.execute(
        select(VendorBankAccount.id).where(
            VendorBankAccount.partner_id == partner_id, VendorBankAccount.status == "PENDING"
        )
    ).first()
    if waiting:
        raise AppError(
            409,
            "BANK_CHANGE_PENDING",
            "A bank change for this vendor is already waiting for approval; approve or reject "
            "it first",
        )
    acc = VendorBankAccount(
        partner_id=partner_id, status="PENDING", requested_by_id=principal.user.id, **clean
    )
    db.add(acc)
    db.flush()
    audit_service.record(
        db,
        "VENDOR_BANK_REQUESTED",
        "vendor",
        partner_id,
        actor=principal.user,
        details={
            "bank_account_id": acc.id,
            "account_no": mask(acc.account_no),
            "ifsc": acc.ifsc,
            "bank_name": acc.bank_name,
            "wef": acc.wef.isoformat(),
        },
    )
    engagement_service.notify(
        db,
        [
            u
            for u in engagement_service.holders_of(db, "vendor_bank.approve")
            if u != principal.user.id
        ],
        "VENDOR_BANK",
        f"Approve bank details for {vendor.name}",
        f"{principal.user.full_name} entered {acc.bank_name} {mask(acc.account_no)} "
        f"(IFSC {acc.ifsc}), effective {acc.wef:%d %b %Y}",
    )
    out = serialize(db, principal, acc)
    db.commit()
    return out


def decide(db: Session, principal: Principal, account_id: int, approve: bool, note: str) -> dict:
    """Checker: approve (becomes the account in use) or reject. Never the maker."""
    acc = db.execute(
        select(VendorBankAccount).where(VendorBankAccount.id == account_id).with_for_update()
    ).scalar_one_or_none()
    if acc is None:
        raise not_found("Bank account")
    if acc.status != "PENDING":
        raise AppError(409, "INVALID_TRANSITION", "This bank change was already decided")
    if acc.requested_by_id == principal.user.id:
        raise AppError(
            403, "FORBIDDEN", "Someone other than the person who entered it must approve"
        )
    if not approve and len(note.strip()) < 3:
        raise AppError(422, "VALIDATION_ERROR", "Say why it is rejected")
    now = utcnow()
    previous = None
    if approve:
        previous = db.execute(
            select(VendorBankAccount)
            .where(
                VendorBankAccount.partner_id == acc.partner_id,
                VendorBankAccount.status == "ACTIVE",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if previous is not None:
            previous.status = "SUPERSEDED"
            db.flush()
    acc.status = "ACTIVE" if approve else "REJECTED"
    acc.decided_by_id, acc.decided_at = principal.user.id, now
    acc.decision_note = note.strip()[:500] or None
    db.flush()
    vendor = db.get(Partner, acc.partner_id)
    audit_service.record(
        db,
        "VENDOR_BANK_APPROVED" if approve else "VENDOR_BANK_REJECTED",
        "vendor",
        acc.partner_id,
        actor=principal.user,
        details={
            "bank_account_id": acc.id,
            "superseded": previous.id if previous else None,
            "note": acc.decision_note,
        },
    )
    engagement_service.notify(
        db,
        [acc.requested_by_id],
        "VENDOR_BANK",
        f"Bank details {'approved' if approve else 'rejected'}: {vendor.name if vendor else ''}",
        acc.decision_note,
    )
    out = serialize(db, principal, acc)
    db.commit()
    return out


def pending(db: Session, principal: Principal) -> list[dict]:
    """Changes waiting for a checker, oldest first, with the vendor's name."""
    rows = db.execute(
        select(VendorBankAccount, Partner)
        .join(Partner, Partner.id == VendorBankAccount.partner_id)
        .where(VendorBankAccount.status == "PENDING")
        .order_by(VendorBankAccount.id)
    ).all()
    return [
        {**serialize(db, principal, acc), "vendor": {"id": p.id, "name": p.name}} for acc, p in rows
    ]
