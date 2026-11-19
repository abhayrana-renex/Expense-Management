"""Bill reader: upload a bill first, get pre-filled fields to check, then create the expense."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, require
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.services.bills import service

router = APIRouter(prefix="/bills", tags=["Bill reader"], responses=ERROR_RESPONSES)
can_create = require("expense.create")


class ExtractedField(BaseModel):
    value: Any
    confidence: float
    source: str
    label: str | None = None


class BillDocument(BaseModel):
    id: int
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: Any


class Extraction(BaseModel):
    methods: list[str]
    engine: str
    fields: dict[str, ExtractedField]
    warnings: list[str]
    ocr_confidence: float | None
    pages: int
    duration_ms: int


class BillReadResult(BaseModel):
    document: BillDocument
    extraction: Extraction | None


class EmailInfo(BaseModel):
    kind: str
    subject: str
    sender: str
    sent_at: datetime | None
    document: BillDocument


class SkippedFile(BaseModel):
    name: str
    reason: str
    quiet: bool = False


class EmailReadResult(BaseModel):
    email: EmailInfo
    bills: list[BillReadResult]
    from_email_text: bool
    skipped: list[SkippedFile]


FIELD_DOC = (
    "Fields (each {value, confidence 0-1, source}): vendor_name, vendor_gstin, vendor_pan, "
    "vendor_state, vendor_id (FAR vendor match), buyer_gstin, invoice_number, invoice_date, "
    "taxable_amount, cgst_amount, sgst_amount, igst_amount, total_amount, cgst_rate, sgst_rate, "
    "igst_rate, round_off, hsn_sac, place_of_supply, irn, description, category_code, "
    "expense_type_id. Sources: e-invoice QR, UPI QR, PDF text, OCR, table, computed. "
    "Nothing is applied automatically: show every field for the person to confirm."
)


@router.post(
    "/read",
    response_model=BillReadResult,
    status_code=201,
    summary="Upload a bill and read it (QR + PDF text + OCR)",
    description=f"PDF, JPG or PNG, max {settings.MAX_UPLOAD_SIZE_MB} MB. The bill is stored "
    "(staged, visible only to you) and returned with pre-filled fields. Pass its "
    "`document.id` in `items[].document_ids` when creating the expense. " + FIELD_DOC,
    responses={413: {"model": ErrorResponse}},
)
def read_bill(
    file: UploadFile = File(...),
    read: bool = Query(
        True,
        description="false = just store the file (approval e-mail, contract, or a bill the "
        "person types in by hand); `extraction` is then null. With read=false an Outlook "
        ".msg / .eml (e.g. the approval e-mail itself) is accepted as well",
    ),
    principal: Principal = Depends(can_create),
    db: Session = Depends(get_db),
) -> dict:
    # Sync on purpose: OCR is CPU-bound, so FastAPI runs this in a worker thread instead of
    # blocking the event loop for every other user.
    data = file.file.read(settings.max_upload_bytes + 1)
    service.rate_check(principal)
    if not read:
        return service.store_only(db, principal, file.filename or "", data)
    return service.extract_new_bill(db, principal, file.filename or "", data)


@router.post(
    "/read-email",
    response_model=EmailReadResult,
    status_code=201,
    summary="Upload an Outlook e-mail (.msg) or saved e-mail (.eml) and read its bills",
    description=f"Max {settings.MAX_EMAIL_SIZE_MB} MB. Every PDF / JPG / PNG attachment "
    f"(by content, up to {settings.EMAIL_MAX_BILLS}) is stored and read like `/bills/read`; "
    "logos and signature pictures are ignored; other files are listed in `skipped` with the "
    "reason. With no bill attached, the e-mail's own text is turned into a PDF and read "
    "(`from_email_text`). Nothing in the e-mail is executed or fetched (HTML is reduced to "
    "text). The original e-mail is kept (`email.document`) and is only ever downloadable.",
    responses={413: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
def read_email(
    file: UploadFile = File(...),
    principal: Principal = Depends(can_create),
    db: Session = Depends(get_db),
) -> dict:
    data = file.file.read(settings.max_email_bytes + 1)
    service.rate_check(principal)
    return service.import_email(db, principal, file.filename or "", data)


@router.get(
    "/{document_id}",
    response_model=BillReadResult,
    summary="A bill and what the reader extracted from it",
)
def get_bill(
    document_id: int,
    principal: Principal = Depends(require("expense.read")),
    db: Session = Depends(get_db),
) -> dict:
    return service.get_bill(db, principal, document_id)
