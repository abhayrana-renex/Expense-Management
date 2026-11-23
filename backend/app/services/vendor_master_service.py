"""Vendor master details: escalation contacts, GST registrations and contracts (bank accounts
are in vendor_bank_service). Every change is audited old -> new; nothing is deleted."""

import re
from datetime import date
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import sha256_hex
from app.models import (
    Partner,
    VendorContact,
    VendorContract,
    VendorGstRegistration,
    VendorProfile,
)
from app.models.vendor_master import CONTACT_KINDS, CONTRACT_TYPES
from app.services import audit_service, vendor_bank_service
from app.services.bills import gstin as gst
from app.services.document_service import ALLOWED, validate_bill
from app.services.storage import get_storage

MOBILE_RE = re.compile(r"^\+?[0-9][0-9 -]{6,18}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _vendor(db: Session, partner_id: int) -> Partner:
    vendor = db.get(Partner, partner_id)
    if vendor is None:
        raise not_found("Vendor")
    return vendor


def _contract_out(c: VendorContract) -> dict:
    today = date.today()
    return {
        "id": c.id,
        "contract_type": c.contract_type,
        "contract_no": c.contract_no,
        "start_date": c.start_date,
        "end_date": c.end_date,
        "is_active": c.is_active,
        "expired": bool(c.end_date and c.end_date < today),
        "document": {"name": c.original_filename, "size_bytes": c.size_bytes}
        if c.storage_key
        else None,
    }


def detail(db: Session, principal: Principal, partner_id: int) -> dict:
    vendor = _vendor(db, partner_id)
    profile = db.get(VendorProfile, partner_id)
    contacts = {
        c.kind: c
        for c in db.execute(
            select(VendorContact).where(VendorContact.partner_id == partner_id)
        ).scalars()
    }
    gsts = db.execute(
        select(VendorGstRegistration)
        .where(VendorGstRegistration.partner_id == partner_id)
        .order_by(VendorGstRegistration.is_active.desc(), VendorGstRegistration.id)
    ).scalars()
    contracts = db.execute(
        select(VendorContract)
        .where(VendorContract.partner_id == partner_id)
        .order_by(VendorContract.is_active.desc(), VendorContract.start_date.desc())
    ).scalars()
    return {
        "vendor": {
            "id": vendor.id,
            "name": vendor.name,
            "type": vendor.type,
            "is_active": vendor.is_active,
            "gstin": profile.gstin if profile else None,
            "pan": profile.pan if profile else None,
            "is_msme": profile.is_msme if profile else True,
        },
        "contacts": [
            {
                "kind": kind,
                "name": contacts[kind].name if kind in contacts else None,
                "mobile": contacts[kind].mobile if kind in contacts else None,
                "email": contacts[kind].email if kind in contacts else None,
            }
            for kind in CONTACT_KINDS
        ],
        "bank_accounts": [
            vendor_bank_service.serialize(db, principal, a)
            for a in vendor_bank_service.history(db, partner_id)
        ],
        "gst_registrations": [
            {
                "id": g.id,
                "gstin": g.gstin,
                "state": g.state,
                "branch_name": g.branch_name,
                "is_active": g.is_active,
            }
            for g in gsts
        ],
        "contracts": [_contract_out(c) for c in contracts],
        "can_manage": principal.can("master.manage"),
        "can_approve_bank": principal.can("vendor_bank.approve"),
        "sees_full_account": vendor_bank_service.sees_full_number(principal),
        "states": sorted({v for k, v in gst.STATE_CODES.items() if k != "97"}),
    }


# ---------- contacts ----------


def save_contacts(db: Session, principal: Principal, partner_id: int, rows: list[dict]) -> dict:
    _vendor(db, partner_id)
    errors = []
    for r in rows:
        if r["kind"] not in CONTACT_KINDS:
            errors.append({"field": "kind", "msg": f"Unknown contact {r['kind']}"})
        if r.get("mobile") and not MOBILE_RE.fullmatch(r["mobile"].strip()):
            errors.append({"field": f"{r['kind']}.mobile", "msg": "Enter a phone number"})
        if r.get("email") and not EMAIL_RE.fullmatch(r["email"].strip()):
            errors.append({"field": f"{r['kind']}.email", "msg": "Enter an e-mail address"})
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Check the contacts", errors)
    current = {
        c.kind: c
        for c in db.execute(
            select(VendorContact).where(VendorContact.partner_id == partner_id)
        ).scalars()
    }
    changes: dict[str, dict] = {}
    for r in rows:
        new = {k: (r.get(k) or "").strip() or None for k in ("name", "mobile", "email")}
        row = current.get(r["kind"])
        old = {k: getattr(row, k) for k in new} if row else dict.fromkeys(new)
        if old == new:
            continue
        if row is None:
            row = VendorContact(partner_id=partner_id, kind=r["kind"])
            db.add(row)
        for k, v in new.items():
            setattr(row, k, v)
        row.updated_by_id = principal.user.id
        changes[r["kind"]] = {k: [old[k], new[k]] for k in new if old[k] != new[k]}
    if changes:
        db.flush()
        audit_service.record(
            db,
            "VENDOR_CONTACTS_UPDATED",
            "vendor",
            partner_id,
            actor=principal.user,
            details=changes,
        )
    db.commit()
    return detail(db, principal, partner_id)


# ---------- GST registrations ----------


def add_gst(db: Session, principal: Principal, partner_id: int, data: dict) -> dict:
    _vendor(db, partner_id)
    gstin = (data.get("gstin") or "").strip().upper()
    if not gst.is_valid(gstin):
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "Not a valid GSTIN (check digit or state code)",
            [{"field": "gstin"}],
        )
    taken = db.execute(
        select(VendorGstRegistration).where(VendorGstRegistration.gstin == gstin)
    ).scalar_one_or_none()
    if taken is not None:
        other = db.get(Partner, taken.partner_id)
        raise AppError(
            409,
            "DUPLICATE",
            f"GSTIN {gstin} is already registered for {other.name if other else 'a vendor'}",
        )
    reg = VendorGstRegistration(
        partner_id=partner_id,
        gstin=gstin,
        state=(data.get("state") or "").strip() or gst.state_of(gstin),
        branch_name=(data.get("branch_name") or "").strip() or None,
        is_active=data.get("is_active", True),
        updated_by_id=principal.user.id,
    )
    db.add(reg)
    db.flush()
    audit_service.record(
        db,
        "VENDOR_GST_ADDED",
        "vendor",
        partner_id,
        actor=principal.user,
        details={"gstin": gstin, "state": reg.state, "branch_name": reg.branch_name},
    )
    db.commit()
    return detail(db, principal, partner_id)


def update_gst(db: Session, principal: Principal, reg_id: int, data: dict) -> dict:
    reg = db.get(VendorGstRegistration, reg_id)
    if reg is None:
        raise not_found("GST registration")
    changes = {}
    for key in ("state", "branch_name", "is_active"):
        if key not in data or data[key] is None:
            continue
        new = data[key].strip() or None if isinstance(data[key], str) else data[key]
        if getattr(reg, key) != new:
            changes[key] = [getattr(reg, key), new]
            setattr(reg, key, new)
    if changes:
        reg.updated_by_id = principal.user.id
        db.flush()
        audit_service.record(
            db,
            "VENDOR_GST_UPDATED",
            "vendor",
            reg.partner_id,
            actor=principal.user,
            details={"gstin": reg.gstin, **changes},
        )
    db.commit()
    return detail(db, principal, reg.partner_id)


# ---------- contracts ----------


def add_contract(
    db: Session,
    principal: Principal,
    partner_id: int,
    data: dict,
    file: tuple[str, bytes] | None,
) -> dict:
    _vendor(db, partner_id)
    if data.get("contract_type") not in CONTRACT_TYPES:
        raise AppError(422, "VALIDATION_ERROR", "Contract type must be NDA, MSA or DO")
    number = (data.get("contract_no") or "").strip()
    if not number:
        raise AppError(422, "VALIDATION_ERROR", "Enter the contract number")
    start, end = data.get("start_date"), data.get("end_date")
    if start is None:
        raise AppError(422, "VALIDATION_ERROR", "Enter the start date")
    if end is not None and end < start:
        raise AppError(422, "VALIDATION_ERROR", "The end date is before the start date")
    c = VendorContract(
        partner_id=partner_id,
        contract_type=data["contract_type"],
        contract_no=number[:100],
        start_date=start,
        end_date=end,
        is_active=data.get("is_active", True),
        created_by_id=principal.user.id,
    )
    if file is not None:
        name, raw = file
        content_type = validate_bill(name, raw)
        c.original_filename = PurePath(name).name[:255]
        c.storage_key = get_storage().save(raw, ALLOWED[content_type][0])
        c.content_type, c.size_bytes, c.sha256 = content_type, len(raw), sha256_hex(raw)
    db.add(c)
    db.flush()
    audit_service.record(
        db,
        "VENDOR_CONTRACT_ADDED",
        "vendor",
        partner_id,
        actor=principal.user,
        details={
            "contract_id": c.id,
            "type": c.contract_type,
            "no": c.contract_no,
            "start": c.start_date.isoformat(),
            "end": c.end_date.isoformat() if c.end_date else None,
            "document_sha256": c.sha256,
        },
    )
    db.commit()
    return detail(db, principal, partner_id)


def set_contract_active(db: Session, principal: Principal, contract_id: int, active: bool) -> dict:
    c = db.get(VendorContract, contract_id)
    if c is None:
        raise not_found("Contract")
    if c.is_active != active:
        c.is_active = active
        db.flush()
        audit_service.record(
            db,
            "VENDOR_CONTRACT_ACTIVATED" if active else "VENDOR_CONTRACT_DEACTIVATED",
            "vendor",
            c.partner_id,
            actor=principal.user,
            details={"contract_id": c.id, "no": c.contract_no},
        )
    db.commit()
    return detail(db, principal, c.partner_id)


def contract_document(db: Session, principal: Principal, contract_id: int) -> VendorContract:
    c = db.get(VendorContract, contract_id)
    if c is None or not c.storage_key:
        raise not_found("Contract document")
    if not get_storage().exists(c.storage_key):
        raise AppError(404, "NOT_FOUND", "Stored file is missing; contact an administrator")
    audit_service.record(
        db,
        "VENDOR_CONTRACT_DOWNLOADED",
        "vendor",
        c.partner_id,
        actor=principal.user,
        details={"contract_id": c.id},
    )
    db.commit()
    return c
