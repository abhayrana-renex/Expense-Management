"""Vendor master details: contacts, bank accounts (maker-checker), GST registrations and
contracts for FAR's vendors. Reading needs master.read; changes need master.manage; a bank
account is used only after someone else with vendor_bank.approve approves it."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import Principal, require
from app.schemas.common import ERROR_RESPONSES
from app.services import vendor_bank_service, vendor_master_service
from app.services.storage import get_storage

router = APIRouter(prefix="/masters", tags=["Masters: Vendor details"], responses=ERROR_RESPONSES)
read = require("master.read")
manage = require("master.manage")
checker = require("vendor_bank.approve")

ContactKind = Literal[
    "L1_SUPPORT", "L2_SUPPORT", "L3_SUPPORT", "BILLING", "ACCOUNT_MANAGER", "ESCALATION"
]


class ContactIn(BaseModel):
    kind: ContactKind
    name: str | None = Field(None, max_length=120)
    mobile: str | None = Field(None, max_length=20)
    email: str | None = Field(None, max_length=254)


class BankIn(BaseModel):
    account_type: Literal["SAVINGS", "CURRENT", "CC", "OD"]
    account_no: str = Field(max_length=30)
    ifsc: str = Field(max_length=11)
    bank_name: str = Field(max_length=120)
    bank_branch: str | None = Field(None, max_length=120)
    signing_authority: str | None = Field(None, max_length=120)
    state: str | None = Field(None, max_length=60)
    wef: date = Field(description="With effect from")


class DecisionIn(BaseModel):
    note: str = Field("", max_length=500, description="Required when rejecting")


class GstIn(BaseModel):
    gstin: str = Field(max_length=15)
    state: str | None = Field(None, max_length=60, description="Defaults to the GSTIN's state")
    branch_name: str | None = Field(None, max_length=120)
    is_active: bool = True


class GstUpdate(BaseModel):
    state: str | None = Field(None, max_length=60)
    branch_name: str | None = Field(None, max_length=120)
    is_active: bool | None = None


@router.get("/vendors/{partner_id}/details", summary="Everything the portal keeps on a vendor")
def vendor_details(
    partner_id: int, principal: Principal = Depends(read), db: Session = Depends(get_db)
) -> dict:
    return vendor_master_service.detail(db, principal, partner_id)


@router.put("/vendors/{partner_id}/contacts", summary="Save the escalation contacts")
def save_contacts(
    partner_id: int,
    body: list[ContactIn],
    principal: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> dict:
    rows = [c.model_dump() for c in body]
    return vendor_master_service.save_contacts(db, principal, partner_id, rows)


@router.post(
    "/vendors/{partner_id}/bank-accounts",
    status_code=201,
    summary="Enter a bank account or a change (maker)",
    description="Saved as PENDING; used for payments only after someone else with "
    "vendor_bank.approve approves it. One change may wait at a time (409 BANK_CHANGE_PENDING).",
)
def request_bank(
    partner_id: int,
    body: BankIn,
    principal: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> dict:
    return vendor_bank_service.request(db, principal, partner_id, body.model_dump())


@router.get("/vendor-bank-accounts/pending", summary="Bank changes waiting for a checker")
def pending_bank(principal: Principal = Depends(checker), db: Session = Depends(get_db)) -> list:
    return vendor_bank_service.pending(db, principal)


@router.post(
    "/vendor-bank-accounts/{account_id}/{decision}",
    summary="Approve or reject a bank change (checker, never the maker)",
)
def decide_bank(
    account_id: int,
    decision: Literal["approve", "reject"],
    body: DecisionIn,
    principal: Principal = Depends(checker),
    db: Session = Depends(get_db),
) -> dict:
    return vendor_bank_service.decide(db, principal, account_id, decision == "approve", body.note)


@router.post("/vendors/{partner_id}/gst", status_code=201, summary="Add a GST registration")
def add_gst(
    partner_id: int,
    body: GstIn,
    principal: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> dict:
    return vendor_master_service.add_gst(db, principal, partner_id, body.model_dump())


@router.patch("/vendor-gst/{reg_id}", summary="Change a GST registration's state, branch or status")
def update_gst(
    reg_id: int,
    body: GstUpdate,
    principal: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> dict:
    return vendor_master_service.update_gst(db, principal, reg_id, body.model_dump())


@router.post(
    "/vendors/{partner_id}/contracts",
    status_code=201,
    summary="Add a contract (NDA / MSA / DO) with its signed copy",
    description=f"Multipart. Document: PDF, JPG or PNG, max {settings.MAX_UPLOAD_SIZE_MB} MB.",
)
async def add_contract(
    partner_id: int,
    contract_type: Literal["NDA", "MSA", "DO"] = Form(...),
    contract_no: str = Form(..., max_length=100),
    start_date: date = Form(...),
    end_date: date | None = Form(None),
    is_active: bool = Form(True),
    file: UploadFile | None = File(None),
    principal: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> dict:
    doc = None
    if file is not None and file.filename:
        doc = (file.filename, await file.read(settings.max_upload_bytes + 1))
    data = {
        "contract_type": contract_type,
        "contract_no": contract_no,
        "start_date": start_date,
        "end_date": end_date,
        "is_active": is_active,
    }
    return vendor_master_service.add_contract(db, principal, partner_id, data, doc)


@router.post("/vendor-contracts/{contract_id}/{state}", summary="Set a contract active or inactive")
def contract_state(
    contract_id: int,
    state: Literal["activate", "deactivate"],
    principal: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> dict:
    return vendor_master_service.set_contract_active(
        db, principal, contract_id, state == "activate"
    )


@router.get("/vendor-contracts/{contract_id}/document", summary="Download the signed contract")
def contract_document(
    contract_id: int, principal: Principal = Depends(read), db: Session = Depends(get_db)
) -> StreamingResponse:
    c = vendor_master_service.contract_document(db, principal, contract_id)
    safe = "".join(ch for ch in c.original_filename or "" if ch.isalnum() or ch in "._- ")
    return StreamingResponse(
        get_storage().open_chunks(c.storage_key),
        media_type=c.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe or "contract.pdf"}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
