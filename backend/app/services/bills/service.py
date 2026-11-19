"""Upload a bill for a new expense, read it, and return pre-filled, confidence-scored fields.

Nothing is ever applied automatically: the UI shows every field for the person to check and
correct, and the expense is only saved when they confirm. The raw extraction is kept in
bill_extractions so auditors can compare what was read with what was submitted.
"""

import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import AppError
from app.models import BillExtraction, ExpenseDocument, ExpenseType, Partner, VendorProfile
from app.services import audit_service, document_service
from app.services.bills import email_import, parser, qr, reader

log = logging.getLogger("app.bills")
ENGINE = "qr+pdfplumber+rapidocr(PP-OCRv4)"
_SUFFIXES = re.compile(r"\b(private|pvt|limited|ltd|llp|india|co|company|the|and|&)\b\.?", re.I)


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIXES.sub(" ", re.sub(r"[^\w\s&]", " ", name.lower()))).strip()


def match_vendor(db: Session, name: str | None, gstin: str | None) -> dict[str, Any] | None:
    """FAR vendor for this bill: exact GSTIN first, then a close (not merely similar) name."""
    if gstin:
        hit = (
            db.execute(
                select(Partner)
                .join(VendorProfile, VendorProfile.partner_id == Partner.id)
                .where(VendorProfile.gstin == gstin.upper(), Partner.status == "Active")
            )
            .unique()
            .scalar_one_or_none()
        )
        if hit:
            return {"id": hit.id, "name": hit.name, "match": "gstin", "confidence": 0.99}
    if not name:
        return None
    target = _norm_name(name)
    if len(target) < 3:
        return None
    best: tuple[float, Partner] | None = None
    for partner in db.execute(select(Partner).where(Partner.status == "Active")).unique().scalars():
        cand = _norm_name(partner.name)
        if not cand:
            continue
        # OCR sometimes drops spaces ("HOTELPUSHKARPALACE"): compare with and without them.
        ratio = (
            1.0
            if cand == target
            else max(
                SequenceMatcher(None, cand, target).ratio(),
                SequenceMatcher(None, cand.replace(" ", ""), target.replace(" ", "")).ratio(),
            )
        )
        if best is None or ratio > best[0]:
            best = (ratio, partner)
    if best and best[0] >= 0.86:
        return {
            "id": best[1].id,
            "name": best[1].name,
            "match": "name",
            "confidence": round(min(0.95, best[0]), 2),
        }
    return None


def _category_id(db: Session, code: str | None) -> int | None:
    if not code:
        return None
    return db.execute(
        select(ExpenseType.id).where(ExpenseType.code == code, ExpenseType.is_active.is_(True))
    ).scalar()


def _read_and_parse(data: bytes, content_type: str) -> tuple[reader.ReadResult, parser.Parsed]:
    read = reader.read(data, content_type, settings.BILL_READER_MAX_PAGES)
    decoded = qr.decode_all(read.qr_payloads)
    base = (
        0.9 if read.method and read.method[0] == "pdf-text" else 0.9 * (read.ocr_confidence or 0.8)
    )
    parsed = parser.parse(
        read.text,
        base_conf=base,
        company_gstins=settings.company_gstins,
        company_names=settings.company_names,
        qr=decoded,
    )
    if "einvoice" in decoded:
        parsed.warnings.append(
            "Read from the GST e-invoice QR (IRN). Its digital signature "
            "is not verified by the portal."
        )
    return read, parsed


def store_only(db: Session, principal: Principal, filename: str, data: bytes) -> dict:
    """Stage a file without reading it (attach later via document_ids / base_document_ids).
    An Outlook .msg / .eml is accepted too (e.g. the ad-hoc approval e-mail itself), with the
    same e-mail checks as /bills/read-email; it is kept only as a download."""
    if email_import.EMAIL_SUFFIX.search(filename or "") or data.startswith(email_import.OLE_MAGIC):
        kind = email_import.validate(filename, data)
        email_import.parse(kind, data)  # refuse damaged / non-Outlook OLE files up front
        doc = document_service.store_email(db, principal, filename, data, kind)
    else:
        doc = document_service.store_bill(db, principal, filename, data, None, None)
    audit_service.record(
        db,
        "FILE_UPLOADED",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={"sha256": doc.sha256, "file": doc.original_filename},
    )
    db.commit()
    return serialize(doc, None)


def extract_new_bill(db: Session, principal: Principal, filename: str, data: bytes) -> dict:
    if not settings.FEATURE_BILL_READER:
        raise AppError(404, "NOT_FOUND", "The bill reader is switched off")
    doc = document_service.store_bill(db, principal, filename, data, None, None)
    started = time.monotonic()
    try:
        read, parsed = _read_and_parse(data, doc.content_type)
    except Exception:  # noqa: BLE001 - a bad file must still be attachable by hand
        log.exception("Bill reading failed", extra={"document_id": doc.id})
        read = reader.ReadResult(warnings=["Couldn't read this bill; fill it in by hand"])
        parsed = parser.Parsed()
    fields = {k: v.as_dict() for k, v in parsed.fields.items()}
    vendor = match_vendor(db, parsed.get("vendor_name"), parsed.get("vendor_gstin"))
    if vendor:
        fields["vendor_id"] = {
            "value": vendor["id"],
            "confidence": vendor["confidence"],
            "source": f"vendor list ({vendor['match']})",
            "label": vendor["name"],
        }
    cat_id = _category_id(db, parsed.get("category_code"))
    if cat_id:
        f = parsed.fields["category_code"]
        fields["expense_type_id"] = {
            "value": cat_id,
            "confidence": round(f.confidence, 2),
            "source": f.source,
        }
    warnings = read.warnings + parsed.warnings
    if not fields.get("vendor_gstin") and "ocr" not in read.method and fields.get("total_amount"):
        warnings.append("No supplier GSTIN found - GST credit may not be claimable")
    buyer = parsed.get("buyer_gstin")
    if settings.company_gstins and buyer and buyer not in settings.company_gstins:
        warnings.append(f"Bill is addressed to GSTIN {buyer}, which is not one of ours")
    extraction = BillExtraction(
        document_id=doc.id,
        methods=read.method or ["none"],
        engine=ENGINE,
        fields=fields,
        warnings=warnings,
        raw_text=read.text[:20000] or None,
        ocr_confidence=read.ocr_confidence,
        pages=read.pages or 1,
        duration_ms=int((time.monotonic() - started) * 1000),
        created_by_id=principal.user.id,
    )
    db.add(extraction)
    db.flush()
    audit_service.record(
        db,
        "BILL_UPLOADED_AND_READ",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={
            "sha256": doc.sha256,
            "methods": extraction.methods,
            "fields_found": sorted(fields),
            "ms": extraction.duration_ms,
        },
    )
    db.commit()
    return serialize(doc, extraction)


def rate_check(principal: Principal) -> None:
    """Uploads run OCR (CPU heavy): cap them per person per minute."""
    from app.core.rate_limit import limiter

    if not limiter.hit(f"bill-read:{principal.user.id}", settings.BILL_READ_RATE_LIMIT_PER_MINUTE):
        raise AppError(429, "RATE_LIMITED", "Too many bill uploads - wait a minute and try again")


def import_email(db: Session, principal: Principal, filename: str, data: bytes) -> dict:
    """An Outlook .msg / .eml: every PDF / JPG / PNG attachment is read as a bill; with none,
    the e-mail's own text is (receipts like cabs and food orders). The original e-mail is kept
    as evidence and attached to the first bill line by the UI."""
    if not settings.FEATURE_BILL_READER:
        raise AppError(404, "NOT_FOUND", "The bill reader is switched off")
    kind = email_import.validate(filename, data)
    parsed = email_import.parse(kind, data)
    original = document_service.store_email(db, principal, filename, data, kind)
    # The original e-mail and its audit entry are saved together, before any bill is read.
    audit_service.record(
        db,
        "EMAIL_UPLOADED",
        "expense_document",
        original.id,
        actor=principal.user,
        details={"sha256": original.sha256, "kind": kind, "size": original.size_bytes},
    )
    db.commit()
    bills = email_import.bill_attachments(parsed)
    from_body = False
    if not bills:
        pdf = email_import.body_pdf(parsed)
        if pdf:
            stem = email_import.safe_name(parsed.subject, "e-mail")[:80]
            bills = [(f"{stem} (e-mail text).pdf", pdf)]
            from_body = True
    results: list[dict] = []
    for name, payload in bills:
        try:
            results.append(extract_new_bill(db, principal, name, payload))
        except AppError as err:  # e.g. a PDF with active content: refused before anything
            parsed.skip(name, err.detail)  # is written, so skip it and keep the rest
    audit_service.record(
        db,
        "EMAIL_IMPORTED",
        "expense_document",
        original.id,
        actor=principal.user,
        details={
            "sha256": original.sha256,
            "kind": kind,
            "bills": len(results),
            "from_email_text": from_body,
            "skipped": len([s for s in parsed.skipped if not s["quiet"]]),
        },
    )
    db.commit()
    return {
        "email": {
            "kind": kind,
            "subject": parsed.subject,
            "sender": parsed.sender,
            "sent_at": parsed.sent_at,
            "document": serialize(original, None)["document"],
        },
        "bills": results,
        "from_email_text": from_body,
        "skipped": parsed.skipped,
    }


def serialize(doc: ExpenseDocument, extraction: BillExtraction | None) -> dict:
    return {
        "document": {
            "id": doc.id,
            "original_filename": doc.original_filename,
            "content_type": doc.content_type,
            "size_bytes": doc.size_bytes,
            "sha256": doc.sha256,
            "uploaded_at": doc.uploaded_at,
        },
        "extraction": None
        if extraction is None
        else {
            "methods": extraction.methods,
            "engine": extraction.engine,
            "fields": extraction.fields,
            "warnings": extraction.warnings,
            "ocr_confidence": float(extraction.ocr_confidence)
            if extraction.ocr_confidence is not None
            else None,
            "pages": extraction.pages,
            "duration_ms": extraction.duration_ms,
        },
    }


def get_bill(db: Session, principal: Principal, document_id: int) -> dict:
    doc = document_service.visible_document(db, principal, document_id)
    extraction = db.execute(
        select(BillExtraction).where(BillExtraction.document_id == doc.id)
    ).scalar_one_or_none()
    return serialize(doc, extraction)
