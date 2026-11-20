"""Contract register: stored contracts / MSAs and their payment schedule (paid / due)."""

from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, require
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.services import audit_service, contract_service, document_service
from app.services.bills import contract_reader, reader, service
from app.services.bills.vendor_match import match_vendor

router = APIRouter(prefix="/contracts", tags=["Contracts"], responses=ERROR_RESPONSES)
can_read = require("expense.read")
can_create = require("expense.create")


class ContractPeriod(BaseModel):
    start: date
    end: date
    label: str
    status: str  # PAID | CLAIMED | DUE | OVERDUE | UPCOMING
    expense_id: int | None
    expense_no: str | None
    expense_status: str | None


class ContractSchedule(BaseModel):
    periods: list[ContractPeriod]
    current: ContractPeriod | None
    next_due: ContractPeriod | None
    claimed: int
    due: int
    ended: bool


class ContractOut(BaseModel):
    id: int
    contract_no: str
    title: str | None
    party_name: str
    vendor: dict[str, Any] | None
    start_date: date
    end_date: date
    frequency: str
    period_amount: Decimal | None
    gst_rate: Decimal | None
    contract_value: Decimal | None
    terms: str | None
    department_id: int | None
    branch_id: int | None
    expense_type_id: int | None
    has_document: bool
    is_active: bool
    schedule: ContractSchedule | None = None


class ContractRead(BaseModel):
    document: dict[str, Any]
    fields: dict[str, dict[str, Any]]
    existing: ContractOut | None


@router.get(
    "",
    response_model=list[ContractOut],
    summary="Find stored contracts",
    description="Your department's contracts (all contracts with company-wide expense read "
    "access), searched by number, party or title, with the payment schedule of each.",
)
def list_contracts(
    q: str | None = Query(None, max_length=100),
    principal: Principal = Depends(can_read),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [contract_service.serialize(db, c) for c in contract_service.search(db, principal, q)]


@router.get("/{contract_id}", response_model=ContractOut, summary="One contract and its schedule")
def get_contract(
    contract_id: int, principal: Principal = Depends(can_read), db: Session = Depends(get_db)
) -> dict:
    return contract_service.serialize(db, contract_service.get_visible(db, principal, contract_id))


@router.post(
    "/read",
    response_model=ContractRead,
    status_code=201,
    summary="Upload a contract / MSA and read it",
    description=f"PDF, JPG or PNG, max {settings.MAX_UPLOAD_SIZE_MB} MB. The file is stored "
    "(staged, visible only to you: pass its id in `base_document_ids`) and read for the "
    "contract number, party, start / end, payment frequency, amount per period, value, GST "
    "and terms. When the number is already registered and visible to you, `existing` is "
    "that contract (use its id as `contract_id`). Nothing is saved to the register until the "
    "claim is saved.",
    responses={413: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
def read_contract(
    file: UploadFile = File(...),
    principal: Principal = Depends(can_create),
    db: Session = Depends(get_db),
) -> dict:
    data = file.file.read(settings.max_upload_bytes + 1)
    service.rate_check(principal)
    doc = document_service.store_bill(db, principal, file.filename or "", data, None, None)
    try:
        text = reader.read(data, doc.content_type, max(settings.BILL_READER_MAX_PAGES, 8)).text
    except Exception:  # noqa: BLE001 - an unreadable file can still be attached and typed in
        text = ""
    fields = contract_reader.read(text, settings.company_names)
    party = fields.get("party_name", {}).get("value")
    if party and (v := match_vendor(db, party, None, text=text)) and v["confidence"] >= 0.6:
        fields["vendor_id"] = {
            "value": v["id"],
            "confidence": v["confidence"],
            "source": f"vendor list ({v['match']})",
            "label": v["name"],
        }
    existing = None
    number = fields.get("contract_no", {}).get("value")
    if number and (found := contract_service.by_number(db, number)):
        try:
            existing = contract_service.serialize(
                db, contract_service.get_visible(db, principal, found.id)
            )
        except Exception:  # noqa: BLE001 - registered elsewhere: say nothing about it here
            existing = None
    audit_service.record(
        db,
        "CONTRACT_UPLOADED_AND_READ",
        "expense_document",
        doc.id,
        actor=principal.user,
        details={"sha256": doc.sha256, "fields_found": sorted(fields)},
    )
    db.commit()
    return {
        "document": service.serialize(doc, None)["document"],
        "fields": {
            k: {
                **v,
                "value": str(v["value"]) if isinstance(v["value"], date | Decimal) else v["value"],
            }
            for k, v in fields.items()
        },
        "existing": existing,
    }
