"""Upload a bill for a new expense, read it, and return pre-filled, confidence-scored fields.

Nothing is ever applied automatically: the UI shows every field for the person to check and
correct, and the expense is only saved when they confirm. The raw extraction is kept in
bill_extractions so auditors can compare what was read with what was submitted.
"""

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import AppError
from app.models import BillExtraction, ExpenseDocument
from app.services import audit_service, document_service
from app.services.bills import category, email_import, parser, qr, reader
from app.services.bills.vendor_match import match_vendor

log = logging.getLogger("app.bills")
SURE_VENDOR = 0.6  # below this a vendor is suggested, not filled in
ENGINE = "qr+pdfplumber+rapidocr(PP-OCRv4)"


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
        company_pans=settings.company_pans,
        qr=decoded,
        headings=read.headings,
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
    # Reading switched off (FEATURE_BILL_READER=false): the bill is still stored and attached,
    # just not read - `extraction` is null and the person types every field.
    if not settings.FEATURE_BILL_READER:
        return store_only(db, principal, filename, data)
    doc = document_service.store_bill(db, principal, filename, data, None, None)
    started = time.monotonic()
    try:
        read, parsed = _read_and_parse(data, doc.content_type)
    except Exception:  # noqa: BLE001 - a bad file must still be attachable by hand
        log.exception("Bill reading failed", extra={"document_id": doc.id})
        read = reader.ReadResult(warnings=["Couldn't read this bill; fill it in by hand"])
        parsed = parser.Parsed()
    fields = {k: v.as_dict() for k, v in parsed.fields.items()}
    vendor = match_vendor(
        db,
        parsed.get("vendor_name"),
        parsed.get("vendor_gstin"),
        candidates=parsed.vendor_candidates,
        text=read.text,
        relationship_number=parsed.get("relationship_number"),
    )
    # Pre-fill the vendor only when the match is sure. First-name, brand and same-name
    # matches are listed first in the vendor dropdown for the person to pick.
    if vendor:
        sure = vendor["confidence"] >= SURE_VENDOR and not vendor.get("same_name")
        if sure:
            fields["vendor_id"] = {
                "value": vendor["id"],
                "confidence": vendor["confidence"],
                "source": f"vendor list ({vendor['match']})",
                "label": vendor["name"],
            }
        elif not vendor.get("same_name"):
            parsed.warnings.append(
                f"Possible vendor: {vendor['name']} ({vendor['match']}) - pick it from the top "
                "of the vendor list if it is right"
            )
        if vendor.get("same_name"):
            names = ", ".join(vendor["same_name"])
            if vendor["match"].startswith("same first name"):
                note = f"Vendors with the same first name as the bill - pick the right one: {names}"
            elif vendor["match"].startswith("brand"):
                note = f"Several vendors carry this name - pick the right one: {names}"
            else:
                note = (
                    f"{len(vendor['same_name'])} vendors have this name and no relationship "
                    f"number on the bill matched one - pick the right vendor: {names}"
                )
            parsed.warnings.append(note)
        picked = fields.get("vendor_name", {}).get("value")
        if sure and vendor.get("via_rank") and vendor.get("via") and vendor["via"] != picked:
            # The most prominent header name isn't in the vendor list, but another name in the
            # header is: show that one as the supplier, and say so.
            fields["vendor_name"] = {
                "value": vendor["via"],
                "confidence": vendor["confidence"],
                "source": "bill header (found in the vendor list)",
            }
            parsed.warnings.append(
                f"Vendor found from another name in the bill header: “{vendor['via']}” "
                f"(“{picked}” is not in the vendor list) - check it is the seller"
            )
        if vendor.get("suggestions"):
            # Shown at the top of the vendor list on the bill line ("From the bill").
            fields["vendor_suggestions"] = {
                "value": vendor["suggestions"],
                "confidence": vendor["confidence"],
                "source": "vendor list (name / first name / relationship no.)",
            }
    if parsed.vendor_candidates:
        fields["vendor_candidates"] = {
            "value": parsed.vendor_candidates,
            "confidence": fields.get("vendor_name", {}).get("confidence", 0.5),
            "source": "bill header (largest / coloured text first)",
        }
    # Category from everything on the bill: past claims for this vendor, the HSN/SAC code,
    # keywords, then the most related category by the bill's words (bills/category.py).
    rule = parsed.fields.get("category_code")
    cat = category.guess(
        db,
        text=read.text,
        vendor_name=parsed.get("vendor_name"),
        hsn=parsed.get("hsn_sac"),
        vendor_id=fields.get("vendor_id", {}).get("value"),
        gstin=parsed.get("vendor_gstin"),
        rule_code=parsed.get("category_code"),
        rule_confidence=rule.confidence if rule else 0.0,
    )
    if cat:
        fields["expense_type_id"] = {
            "value": cat.type_id,
            "confidence": cat.confidence,
            "source": cat.source,
        }
    warnings = read.warnings + parsed.warnings
    if not fields.get("vendor_gstin") and "ocr" not in read.method and fields.get("total_amount"):
        warnings.append("No supplier GSTIN found - GST credit may not be claimable")
    buyer = parsed.get("buyer_gstin")
    ours = settings.company_gstins
    pans = settings.company_pans
    if (ours or pans) and buyer and buyer not in ours and buyer[2:12] not in pans:
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
    as evidence and attached to the first bill line by the UI. With reading switched off the
    bills are attached unread (extract_new_bill)."""
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
