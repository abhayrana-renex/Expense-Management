"""Expense schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import Money, ORMModel, Ref, UserRef


class ExpenseItemIn(BaseModel):
    id: int | None = Field(None, description="Send to update an existing line; omit to add one")
    expense_date: date
    description: str = Field(min_length=1, max_length=500)
    vendor_id: int | None = None
    bill_number: str | None = Field(None, max_length=60)
    bill_date: date | None = None
    taxable_amount: Money
    cgst_amount: Money = Decimal("0.00")
    sgst_amount: Money = Decimal("0.00")
    igst_amount: Money = Decimal("0.00")
    cgst_rate: Decimal | None = Field(
        None, ge=0, le=100, decimal_places=2, description="GST rate %"
    )
    sgst_rate: Decimal | None = Field(
        None, ge=0, le=100, decimal_places=2, description="GST rate %"
    )
    igst_rate: Decimal | None = Field(
        None, ge=0, le=100, decimal_places=2, description="GST rate %"
    )
    supplier_name: str | None = Field(None, max_length=200, description="As printed on the bill")
    supplier_gstin: str | None = Field(
        None,
        pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$",
        description="Supplier GSTIN as printed on the bill",
    )
    supplier_msme: bool | None = Field(
        None, description="Supplier is an MSME (Udyam registered); null = not stated"
    )
    document_ids: list[int] = Field(
        default_factory=list,
        max_length=10,
        description="Bills uploaded via POST /bills/read to attach to this line",
    )
    details_confirmed: bool = Field(
        True,
        description="The person checked the (bill-reader pre-filled) values against the "
        "bill. Required to submit when the line's bill was read automatically.",
    )

    @field_validator("supplier_gstin", mode="before")
    @classmethod
    def upper_gstin(cls, v: object) -> object:
        return v.strip().upper() or None if isinstance(v, str) else v

    @model_validator(mode="after")
    def check(self) -> "ExpenseItemIn":
        if self.taxable_amount <= 0:
            raise ValueError("taxable_amount must be greater than 0")
        if min(self.cgst_amount, self.sgst_amount, self.igst_amount) < 0:
            raise ValueError("tax amounts cannot be negative")
        return self


class ExpenseIn(BaseModel):
    title: str = Field(min_length=1, max_length=200, examples=["Client visit - Pune"])
    purpose: str | None = Field(None, max_length=2000)
    expense_type_id: int
    branch_id: int | None = Field(None, description="Defaults to the user's home branch")
    department_id: int | None = Field(
        None,
        description="Department the expense is booked to (FAR master); defaults to the "
        "user's department. Approval routing follows this department's rules.",
    )
    cost_centre_id: int | None = None
    bill_month_id: int | None = None
    bill_period_start: date | None = Field(None, description="Bill duration: from (inclusive)")
    bill_period_end: date | None = Field(None, description="Bill duration: to (inclusive)")
    bill_type: Literal["PO", "ADHOC", "CONTRACT"] = Field(
        "PO",
        description="PO = purchase-order based; ADHOC = no PO, approved by e-mail (upload it); "
        "CONTRACT = contract / MSA based (upload the contract)",
    )
    po_number: str | None = Field(None, max_length=60)
    approval_by_name: str | None = Field(None, max_length=150, description="ADHOC: who approved")
    approval_date: date | None = Field(None, description="ADHOC: date of the approval e-mail")
    approval_reference: str | None = Field(
        None, max_length=200, description="ADHOC: e-mail subject or reference"
    )
    approval_amount: Money | None = Field(None, description="ADHOC: amount approved, if stated")
    contract_reference: str | None = Field(None, max_length=100, description="Contract / MSA no.")
    contract_party: str | None = Field(None, max_length=200, description="Other party")
    contract_start: date | None = None
    contract_end: date | None = None
    contract_value: Money | None = None
    contract_terms: str | None = Field(
        None, max_length=500, description="Billing cycle, notice period, etc."
    )
    contract_id: int | None = Field(
        None,
        description="CONTRACT: the stored contract (GET /contracts) this claim pays a "
        "period of. Omit the first time: the contract is registered from the details above.",
    )
    contract_frequency: (
        Literal["MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "ONE_TIME"] | None
    ) = Field(None, description="CONTRACT, first claim: how often it is paid")
    contract_period_amount: Money | None = Field(
        None, description="CONTRACT, first claim: amount (pre-tax) due each period"
    )
    contract_gst_rate: Decimal | None = Field(
        None, ge=0, le=100, decimal_places=2, description="CONTRACT, first claim: GST %"
    )
    base_document_ids: list[int] = Field(
        default_factory=list,
        max_length=10,
        description="Approval e-mail (ADHOC) or contract (CONTRACT) files uploaded via "
        "POST /bills/read?read=false; previously attached ones not listed are superseded",
    )
    items: list[ExpenseItemIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_period(self) -> "ExpenseIn":
        if self.contract_start and self.contract_end and self.contract_end < self.contract_start:
            raise ValueError("contract_end cannot be before contract_start")
        if (
            self.bill_period_start
            and self.bill_period_end
            and self.bill_period_end < self.bill_period_start
        ):
            raise ValueError("bill_period_end cannot be before bill_period_start")
        return self


class ExpenseCreate(ExpenseIn):
    submit: bool = Field(False, description="Create and submit atomically")


class ExpenseUpdate(ExpenseIn):
    version: int = Field(description="Version you loaded; mismatch returns 409")


class DocumentOut(ORMModel):
    id: int
    item_id: int | None
    doc_kind: str = "BILL"
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    uploaded_by_id: int


class ExpenseItemOut(ORMModel):
    id: int
    line_no: int
    expense_date: date
    description: str
    vendor: Ref | None
    bill_number: str | None
    bill_date: date | None
    taxable_amount: Money
    cgst_amount: Money
    sgst_amount: Money
    igst_amount: Money
    total_amount: Money
    cgst_rate: Decimal | None
    sgst_rate: Decimal | None
    igst_rate: Decimal | None
    snapshot_vendor_name: str | None
    snapshot_vendor_gstin: str | None
    supplier_name: str | None
    supplier_gstin: str | None
    supplier_msme: bool | None
    details_confirmed: bool
    documents: list[DocumentOut]


class BillMonthRef(ORMModel):
    id: int
    code: str
    label: str


class ExpenseSummaryOut(ORMModel):
    id: int
    expense_no: str
    title: str
    status: str
    owner: UserRef
    department: Ref
    branch: Ref
    expense_type: Ref
    cost_centre: Ref | None
    bill_month: BillMonthRef | None
    bill_period_start: date | None = None
    bill_period_end: date | None = None
    current_level: Ref | None
    current_approver: UserRef | None
    total_taxable: Money
    total_tax: Money
    total_amount: Money
    submitted_at: datetime | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    payment_status: str = Field(
        description="NOT_DUE, PENDING (to schedule), SCHEDULED, PAY_APPROVED, PROCESSING "
        "(sent to bank), PAID, ON_HOLD"
    )
    bill_type: str = Field("PO", description="PO, ADHOC or CONTRACT")
    payment_scheduled_for: date | None = Field(None, description="Payment date set by finance")
    current_stage_since: datetime | None = Field(
        description="When the expense reached its current approval level / payment stage"
    )


class ExpenseOut(ExpenseSummaryOut):
    purpose: str | None
    currency: str
    cycle: int
    closed_at: datetime | None
    snapshot_department_name: str | None
    snapshot_branch_name: str | None
    snapshot_cost_centre_code: str | None
    snapshot_expense_type_name: str | None
    po_number: str | None
    approval_by_name: str | None
    approval_date: date | None
    approval_reference: str | None
    approval_amount: Money | None
    contract_reference: str | None
    contract_party: str | None
    contract_start: date | None
    contract_end: date | None
    contract_value: Money | None
    contract_terms: str | None
    contract_id: int | None = None
    base_documents: list[DocumentOut]
    payment_mode: str | None
    payment_reference: str | None
    paid_amount: Money | None
    paid_on: date | None
    payment_scheduled_by: UserRef | None = None
    payment_approved_by: UserRef | None = None
    payment_approved_at: datetime | None = None
    payment_batch_ref: str | None = None
    items: list[ExpenseItemOut]


class ActionRequest(BaseModel):
    comment: str = Field(min_length=3, max_length=2000)
    version: int | None = Field(None, description="Optional optimistic-lock check")


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: Literal["BLOCK", "WARN"]
    field: str | None = None
    item_id: int | None = None
    line_no: int | None = None


class RouteStepPreview(BaseModel):
    level: str
    approver: str
    status: str = Field(
        description="PENDING (first), WAITING, or SKIPPED (you can't approve " "your own expense)"
    )
    note: str | None = None
    approval_limit: Money | None = Field(description="null = no upper limit")


class ValidationResult(BaseModel):
    can_submit: bool
    issues: list[ValidationIssue]
    route: list[RouteStepPreview] = Field(
        default_factory=list, description="Who will approve, in order, if submitted now"
    )


class PaymentRequest(BaseModel):
    action: Literal[
        "schedule",
        "unschedule",
        "approve",
        "send_back",
        "reopen",
        "sent_to_bank",
        "bank_returned",
        "paid",
        "hold",
        "release",
    ] = Field(
        description="schedule (finance) -> approve (payment approver / MD) -> sent_to_bank "
        "(or via the bank file) -> paid (UTR). send_back / reopen / bank_returned push back "
        "one step; hold / release park a payment."
    )
    scheduled_for: date | None = Field(None, description="schedule: payment date")
    mode: Literal["NEFT", "RTGS", "IMPS", "UPI", "CHEQUE", "CASH", "OTHER"] | None = None
    reference: str | None = Field(None, max_length=60, description="paid: UTR / cheque number")
    paid_on: date | None = None
    note: str | None = Field(None, max_length=2000, description="Required for push-back / hold")
    version: int | None = None


class PaymentBulkRequest(BaseModel):
    expense_ids: list[int] = Field(min_length=1, max_length=500)
    action: Literal["schedule", "unschedule", "approve", "send_back", "reopen", "hold", "release"]
    scheduled_for: date | None = None
    mode: Literal["NEFT", "RTGS", "IMPS", "UPI", "CHEQUE", "CASH", "OTHER"] | None = None
    note: str | None = Field(None, max_length=2000)


class BankFileRequest(BaseModel):
    expense_ids: list[int] = Field(
        default_factory=list, description="Empty = every payment approved for payment"
    )


class TrackerStage(BaseModel):
    key: str
    label: str
    state: Literal["done", "current", "upcoming", "skipped", "failed"]
    at: datetime | None
    actor: str | None
    note: str | None
    comment: str | None


class TrackerOut(BaseModel):
    expense_id: int
    expense_no: str
    status: str
    payment_status: str
    headline: str
    stages: list[TrackerStage]


class AllowedActions(BaseModel):
    edit: bool
    submit: bool
    cancel: bool
    upload_bill: bool
    approve: bool
    reject: bool
    return_: bool = Field(alias="return", serialization_alias="return")
    reopen: bool
    send_back: bool = False
    correct: bool = Field(False, description="Finance check may correct bill details now")
    send_back_to: str | None = Field(
        None, description="Who a send-back would go to (previous approver, or the employee)"
    )
    record_payment: bool = Field(False, description="Any payment step is available to you")
    payment_actions: list[str] = Field(
        default_factory=list, description="Payment steps you may take now (see /payment)"
    )

    model_config = {"populate_by_name": True}


class HistoryOut(ORMModel):
    id: int
    action: str
    from_status: str | None
    to_status: str
    level_code: str | None
    actor: UserRef
    comment: str | None
    ip_address: str | None
    created_at: datetime


class StepOut(ORMModel):
    id: int
    cycle: int
    sequence: int
    level: Ref
    approver: UserRef
    status: str
    note: str | None
    acted_at: datetime | None
    comment: str | None


class SignedUrlOut(BaseModel):
    url: str
    expires_at: datetime


class CorrectionItemIn(BaseModel):
    id: int = Field(description="Existing line to correct")
    description: str = Field(min_length=1, max_length=500)
    vendor_id: int | None = None
    bill_number: str | None = Field(None, max_length=60)
    bill_date: date | None = None
    taxable_amount: Money
    cgst_amount: Money = Decimal("0.00")
    sgst_amount: Money = Decimal("0.00")
    igst_amount: Money = Decimal("0.00")
    cgst_rate: Decimal | None = Field(None, ge=0, le=100, decimal_places=2)
    sgst_rate: Decimal | None = Field(None, ge=0, le=100, decimal_places=2)
    igst_rate: Decimal | None = Field(None, ge=0, le=100, decimal_places=2)
    supplier_name: str | None = Field(None, max_length=200)
    supplier_gstin: str | None = Field(
        None, pattern=r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
    )
    supplier_msme: bool | None = None

    @field_validator("supplier_gstin", mode="before")
    @classmethod
    def upper_gstin(cls, v: object) -> object:
        return v.strip().upper() or None if isinstance(v, str) else v

    @model_validator(mode="after")
    def check(self) -> "CorrectionItemIn":
        if self.taxable_amount <= 0:
            raise ValueError("taxable_amount must be greater than 0")
        if min(self.cgst_amount, self.sgst_amount, self.igst_amount) < 0:
            raise ValueError("tax amounts cannot be negative")
        return self


class CorrectionRequest(BaseModel):
    items: list[CorrectionItemIn] = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=3, max_length=2000, description="What was corrected and why")
    version: int


class InvoiceMatch(BaseModel):
    expense_id: int
    expense_no: str
    status: str
    owner_name: str
    is_mine: bool
    line_no: int
    company: str


class InvoiceCheckOut(BaseModel):
    duplicate: bool = Field(description="Same company's invoice is already claimed")
    matches: list[InvoiceMatch]
    same_number_other_companies: int = Field(
        description="Same invoice number from other companies (allowed, for information)"
    )
