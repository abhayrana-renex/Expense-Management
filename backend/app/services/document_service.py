"""Bill upload, supersede, authenticated download and short-lived signed URLs."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import sha256_hex, sign_document_url, utcnow, verify_document_signature
from app.models import ExpenseDocument, ExpenseItem, User
from app.services import audit_service, expense_rules
from app.services.bills import reader
from app.services.expense_service import get_visible
from app.services.storage import get_storage, scan_for_malware

# Bills may be PDFs (online bills) or photos/scans. The real type is taken from the file
# content (magic bytes), never from the name or the browser-declared content type.
ALLOWED = {
    "application/pdf": (".pdf",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
}


# PDF features that run code or carry other files. Bills never need them; a PDF with them is
# refused (re-print it to PDF). Names may be hex-escaped (/J#61vaScript), so decode first.
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia)(?![A-Za-z0-9])")
_PDF_NAME_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")


def pdf_active_content(data: bytes) -> str | None:
    """The first active-content marker found in a PDF, or None."""
    names = _PDF_NAME_HEX.sub(lambda m: bytes([int(m.group(1), 16)]), data)
    hit = _PDF_ACTIVE.search(names)
    if hit:
        return hit.group(1).decode()
    # Markers can also hide inside compressed object streams: check the parsed catalog.
    try:
        import pdfplumber

        with pdfplumber.open(__import__("io").BytesIO(data)) as pdf:
            catalog = pdf.doc.catalog
            names_tree = catalog.get("Names")
            names_tree = names_tree.resolve() if hasattr(names_tree, "resolve") else names_tree
            for key in ("JavaScript", "EmbeddedFiles"):
                if isinstance(names_tree, dict) and key in names_tree:
                    return key
            action = catalog.get("OpenAction")
            action = action.resolve() if hasattr(action, "resolve") else action
            if isinstance(action, dict) and str(action.get("S", "")).strip("/'") in (
                "JavaScript",
                "Launch",
            ):
                return "OpenAction"
    except Exception:  # noqa: BLE001 - unreadable PDFs are handled by the reader
        return None
    return None


def validate_bill(filename: str, data: bytes) -> str:
    """Return the detected content type, or raise INVALID_FILE / FILE_TOO_LARGE."""
    if len(data) == 0:
        raise AppError(422, "INVALID_FILE", "File is empty")
    if len(data) > settings.max_upload_bytes:
        raise AppError(413, "FILE_TOO_LARGE", f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB")
    detected = reader.sniff(data)
    if detected is None:
        raise AppError(422, "INVALID_FILE", "Only PDF, JPG or PNG bills are accepted")
    if PurePath(filename).suffix.lower() not in ALLOWED[detected]:
        raise AppError(422, "INVALID_FILE", "File name extension does not match its content")
    if detected == "application/pdf" and (marker := pdf_active_content(data)):
        raise AppError(
            422,
            "INVALID_FILE",
            f"This PDF contains active content ({marker}). Print it to PDF (Microsoft Print "
            "to PDF) and upload that copy.",
        )
    if not scan_for_malware(data):
        raise AppError(422, "INVALID_FILE", "File failed the malware scan")
    return detected


def store_bill(
    db: Session,
    principal: Principal,
    filename: str,
    data: bytes,
    expense_id: int | None,
    item_id: int | None,
) -> ExpenseDocument:
    """Validate + store a bill (never overwrites). expense_id=None = staged for a new expense."""
    content_type = validate_bill(filename, data)
    key = get_storage().save(data, ALLOWED[content_type][0])
    doc = ExpenseDocument(
        expense_id=expense_id,
        item_id=item_id,
        original_filename=PurePath(filename).name[:255],
        storage_key=key,
        content_type=content_type,
        size_bytes=len(data),
        sha256=sha256_hex(data),
        uploaded_by_id=principal.user.id,
    )
    db.add(doc)
    db.flush()
    return doc


def store_email(
    db: Session, principal: Principal, filename: str, data: bytes, kind: str
) -> ExpenseDocument:
    """Keep the original e-mail (.msg / .eml) as evidence for the bills read from it.
    Validated by email_import.validate before this is called."""
    from app.services.bills.email_import import EMAIL_TYPES, safe_name

    if not scan_for_malware(data):
        raise AppError(422, "INVALID_FILE", "File failed the malware scan")
    content_type, ext = EMAIL_TYPES[kind]
    key = get_storage().save(data, ext)
    doc = ExpenseDocument(
        expense_id=None,
        item_id=None,
        original_filename=safe_name(filename, f"e-mail{ext}")[:255],
        storage_key=key,
        content_type=content_type,
        size_bytes=len(data),
        sha256=sha256_hex(data),
        uploaded_by_id=principal.user.id,
    )
    db.add(doc)
    db.flush()
    return doc


PREVIEWABLE = set(ALLOWED)


def upload(
    db: Session,
    principal: Principal,
    expense_id: int,
    item_id: int,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> ExpenseDocument:
    expense = get_visible(db, principal, expense_id, for_update=True)
    expense_rules.assert_allowed("upload_bill", expense, principal)
    item = db.get(ExpenseItem, item_id)
    if item is None or item.expense_id != expense.id:
        raise not_found("Line item")
    doc = store_bill(db, principal, filename, data, expense.id, item.id)
    audit_service.record(
        db,
        "DOCUMENT_UPLOADED",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={
            "expense_id": expense.id,
            "item_id": item.id,
            "sha256": doc.sha256,
            "size": doc.size_bytes,
        },
    )
    db.commit()
    return doc


def supersede(db: Session, principal: Principal, expense_id: int, document_id: int) -> None:
    expense = get_visible(db, principal, expense_id, for_update=True)
    expense_rules.assert_allowed("upload_bill", expense, principal)
    doc = db.get(ExpenseDocument, document_id)
    if doc is None or doc.expense_id != expense.id or doc.superseded_at is not None:
        raise not_found("Document")
    doc.superseded_at = utcnow()
    doc.superseded_by_id = principal.user.id
    audit_service.record(
        db,
        "DOCUMENT_SUPERSEDED",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={"expense_id": expense.id},
    )
    db.commit()


def visible_document(db: Session, principal: Principal, document_id: int) -> ExpenseDocument:
    doc = db.get(ExpenseDocument, document_id)
    if doc is None:
        raise not_found("Document")
    if doc.expense_id is None:  # staged bill: only its uploader may see it
        if doc.uploaded_by_id != principal.user.id:
            raise not_found("Document")
        return doc
    get_visible(db, principal, doc.expense_id)  # raises 404 when out of scope
    return doc


def stream(doc: ExpenseDocument) -> Iterator[bytes]:
    return get_storage().open_chunks(doc.storage_key)


def download(db: Session, principal: Principal, document_id: int, via: str) -> ExpenseDocument:
    doc = visible_document(db, principal, document_id)
    if not get_storage().exists(doc.storage_key):
        raise AppError(404, "NOT_FOUND", "Stored file is missing; contact an administrator")
    audit_service.record(
        db,
        "DOCUMENT_DOWNLOADED",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={"via": via},
    )
    db.commit()
    return doc


def signed_url(db: Session, principal: Principal, document_id: int) -> tuple[str, datetime]:
    doc = visible_document(db, principal, document_id)
    expires = int(utcnow().timestamp()) + settings.SIGNED_URL_TTL_SECONDS
    sig = sign_document_url(doc.id, principal.user.id, expires)
    url = (
        f"{settings.API_V1_PREFIX}/expenses/documents/{doc.id}/signed"
        f"?u={principal.user.id}&e={expires}&s={sig}"
    )
    audit_service.record(
        db,
        "DOCUMENT_URL_ISSUED",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={"expires": expires},
    )
    db.commit()
    return url, datetime.fromtimestamp(expires, tz=UTC)


def resolve_signed(
    db: Session, document_id: int, user_id: int, expires: int, sig: str
) -> tuple[ExpenseDocument, User]:
    """Check the HMAC, then re-check that the user may still see the document right now."""
    if not verify_document_signature(document_id, user_id, expires, sig):
        raise AppError(403, "INVALID_SIGNATURE", "Link is invalid or has expired")
    user = db.execute(select(User).where(User.id == user_id)).unique().scalar_one_or_none()
    if user is None or not user.is_active:
        raise AppError(403, "INVALID_SIGNATURE", "Link is invalid or has expired")
    principal = Principal(
        user=user, session_id=0, permissions={g.permission.code: g.scope for g in user.role.grants}
    )
    doc = download(db, principal, document_id, via="signed_url")
    return doc, user
