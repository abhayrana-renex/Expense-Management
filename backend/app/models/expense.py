"""Expenses (header), line items, bill documents, approval route steps and history."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.far import Branch, Department, M, Partner
from app.models.masters import ApprovalLevel, BillMonth, CostCentre, ExpenseType
from app.models.user import User

MONEY = Numeric(14, 2)
ZERO = Decimal("0.00")

STATUS_CHECK = (
    "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','RETURNED','CANCELLED') "
    "OR status LIKE 'PENDING\\_%'"
)


class Expense(IdMixin, TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint(STATUS_CHECK, name="status_valid"),
        CheckConstraint("total_amount >= 0", name="total_non_negative"),
        CheckConstraint(
            "payment_status IN ('NOT_DUE','PENDING','SCHEDULED','PAY_APPROVED','PROCESSING',"
            "'ON_HOLD','PAID')",
            name="payment_status_valid",
        ),
        CheckConstraint("bill_type IN ('PO','ADHOC','CONTRACT')", name="bill_type_valid"),
        CheckConstraint(
            "contract_start IS NULL OR contract_end IS NULL OR contract_end >= contract_start",
            name="contract_period_valid",
        ),
        CheckConstraint(
            "bill_period_start IS NULL OR bill_period_end IS NULL "
            "OR bill_period_end >= bill_period_start",
            name="bill_period_valid",
        ),
        Index("ix_expenses_bill_type", "bill_type"),
        Index("ix_expenses_owner_status", "owner_id", "status"),
        Index("ix_expenses_payment_status", "payment_status"),
        Index("ix_expenses_status_submitted", "status", "submitted_at"),
    )

    expense_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    # FAR shared masters (core.departments / core.branches).
    department_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{M}.departments.id"), nullable=False, index=True
    )
    branch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{M}.branches.id"), nullable=False, index=True
    )
    cost_centre_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cost_centres.id"))
    expense_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expense_types.id"), nullable=False, index=True
    )
    bill_month_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("bill_months.id"))
    # Bill duration (billing / service period), e.g. 01 Oct - 31 Oct, read from the bill.
    bill_period_start: Mapped[date | None] = mapped_column(Date)
    bill_period_end: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    total_taxable: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=ZERO)
    total_tax: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=ZERO)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=ZERO)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="DRAFT")
    cycle: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_level_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("approval_levels.id")
    )
    current_approver_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), index=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the expense reached its current stage (pending level / payment): drives "waiting for".
    current_stage_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Payment (after final approval). NOT_DUE until approved.
    payment_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NOT_DUE", server_default="NOT_DUE"
    )
    payment_mode: Mapped[str | None] = mapped_column(String(20))  # NEFT/RTGS/IMPS/UPI/...
    payment_reference: Mapped[str | None] = mapped_column(String(60))  # UTR / cheque no.
    paid_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    paid_on: Mapped[date | None] = mapped_column(Date)
    payment_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_updated_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    # Payment schedule (finance) -> payment approval (MD) -> bank file -> UTR.
    payment_scheduled_for: Mapped[date | None] = mapped_column(Date, index=True)
    payment_scheduled_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    payment_approved_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    payment_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_batch_ref: Mapped[str | None] = mapped_column(String(40), index=True)

    # Type of bill (AP process: "Option for type of bill") and its base document details.
    bill_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="PO", server_default="PO"
    )
    po_number: Mapped[str | None] = mapped_column(String(60))  # PO based
    approval_by_name: Mapped[str | None] = mapped_column(String(150))  # Ad-hoc: who approved
    approval_date: Mapped[date | None] = mapped_column(Date)
    approval_reference: Mapped[str | None] = mapped_column(String(200))  # e-mail subject / ref
    approval_amount: Mapped[Decimal | None] = mapped_column(MONEY)  # amount approved, if stated
    contract_reference: Mapped[str | None] = mapped_column(String(100))  # Contract / MSA
    contract_party: Mapped[str | None] = mapped_column(String(200))
    contract_start: Mapped[date | None] = mapped_column(Date)
    contract_end: Mapped[date | None] = mapped_column(Date)
    contract_value: Mapped[Decimal | None] = mapped_column(MONEY)
    contract_terms: Mapped[str | None] = mapped_column(String(500))  # billing cycle, notice...

    # Snapshot at submission so later master changes never rewrite what auditors see.
    snapshot_department_name: Mapped[str | None] = mapped_column(String(150))
    snapshot_branch_name: Mapped[str | None] = mapped_column(String(150))
    snapshot_cost_centre_code: Mapped[str | None] = mapped_column(String(30))
    snapshot_expense_type_name: Mapped[str | None] = mapped_column(String(150))

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}

    owner: Mapped[User] = relationship(foreign_keys=[owner_id], lazy="joined")
    current_approver: Mapped[User | None] = relationship(
        foreign_keys=[current_approver_id], lazy="joined"
    )
    department: Mapped[Department] = relationship(lazy="joined")
    branch: Mapped[Branch] = relationship(lazy="joined")
    cost_centre: Mapped[CostCentre | None] = relationship(lazy="joined")
    expense_type: Mapped[ExpenseType] = relationship(lazy="joined")
    bill_month: Mapped[BillMonth | None] = relationship(lazy="joined")
    current_level: Mapped[ApprovalLevel | None] = relationship(lazy="joined")
    payment_scheduled_by: Mapped[User | None] = relationship(
        foreign_keys=[payment_scheduled_by_id], lazy="selectin"
    )
    payment_approved_by: Mapped[User | None] = relationship(
        foreign_keys=[payment_approved_by_id], lazy="selectin"
    )
    # Ad-hoc approval e-mails and contracts (not bills): the base documents of the claim.
    base_documents: Mapped[list["ExpenseDocument"]] = relationship(
        lazy="selectin",
        order_by="ExpenseDocument.id",
        primaryjoin="and_(ExpenseDocument.expense_id == Expense.id, "
        "ExpenseDocument.doc_kind != 'BILL', ExpenseDocument.superseded_at.is_(None))",
        viewonly=True,
    )
    items: Mapped[list["ExpenseItem"]] = relationship(
        back_populates="expense",
        order_by="ExpenseItem.line_no",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ExpenseItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "expense_items"
    __table_args__ = (
        CheckConstraint("taxable_amount > 0", name="taxable_positive"),
        CheckConstraint(
            "cgst_amount >= 0 AND sgst_amount >= 0 AND igst_amount >= 0", name="tax_non_negative"
        ),
    )

    expense_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    # FAR shared master core.partners (vendors / contractors).
    vendor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{M}.partners.id"), index=True
    )
    bill_number: Mapped[str | None] = mapped_column(String(60))
    bill_date: Mapped[date | None] = mapped_column(Date)
    taxable_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    cgst_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=ZERO)
    sgst_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=ZERO)
    igst_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=ZERO)
    # GST rate (%) chosen from the gst_rates master, or as printed on the bill.
    cgst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    sgst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    igst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)

    snapshot_vendor_name: Mapped[str | None] = mapped_column(String(200))
    snapshot_vendor_gstin: Mapped[str | None] = mapped_column(String(15))
    # As printed on the bill (vendors such as Uber or a hotel may not be in FAR's vendor list).
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    supplier_gstin: Mapped[str | None] = mapped_column(String(15))
    # MSME (Udyam registered) supplier - Yes / No; null when nobody said.
    supplier_msme: Mapped[bool | None] = mapped_column(Boolean)
    # A person confirmed the (bill-reader pre-filled) details against the bill.
    details_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    expense: Mapped[Expense] = relationship(back_populates="items")
    vendor: Mapped[Partner | None] = relationship(lazy="joined")
    documents: Mapped[list["ExpenseDocument"]] = relationship(
        back_populates="item",
        lazy="selectin",
        order_by="ExpenseDocument.id",
        primaryjoin="and_(ExpenseDocument.item_id == ExpenseItem.id, "
        "ExpenseDocument.superseded_at.is_(None))",
        viewonly=True,
    )


class ExpenseDocument(IdMixin, Base):
    """Stored bill. Never overwritten or deleted; replaced documents are marked superseded."""

    __tablename__ = "expense_documents"
    __table_args__ = (
        CheckConstraint("doc_kind IN ('BILL','APPROVAL','CONTRACT')", name="doc_kind_valid"),
    )

    # NULL while "staged": uploaded on the new-expense screen, read by the bill reader, and
    # not yet attached. Only the uploader can see or attach a staged bill.
    expense_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("expenses.id"), nullable=True, index=True
    )
    item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("expense_items.id", ondelete="SET NULL"), index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    uploaded_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    # BILL (invoice, on a line) | APPROVAL (ad-hoc approval e-mail) | CONTRACT (contract / MSA)
    doc_kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="BILL", server_default="BILL"
    )

    item: Mapped[ExpenseItem | None] = relationship(back_populates="documents")


class BillExtraction(IdMixin, Base):
    """What the bill reader extracted from a document (kept for audit, never edited)."""

    __tablename__ = "bill_extractions"

    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expense_documents.id"), nullable=False, unique=True
    )
    methods: Mapped[list[str]] = mapped_column(JSONB, nullable=False)  # qr, pdf-text, ocr
    engine: Mapped[str] = mapped_column(String(60), nullable=False)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApprovalStep(IdMixin, Base):
    """The planned route for one submission cycle, one row per level."""

    __tablename__ = "approval_steps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('WAITING','PENDING','APPROVED','REJECTED','RETURNED',"
            "'CANCELLED','SKIPPED')",
            name="status_valid",
        ),
        Index("ix_approval_steps_approver_status", "approver_id", "status"),
    )

    expense_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expenses.id"), nullable=False, index=True
    )
    cycle: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    level_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_levels.id"), nullable=False
    )
    approver_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    rule_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("approval_rules.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255))
    acted_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # became PENDING
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str | None] = mapped_column(Text)

    level: Mapped[ApprovalLevel] = relationship(lazy="joined")
    approver: Mapped[User] = relationship(foreign_keys=[approver_id], lazy="joined")
    acted_by: Mapped[User | None] = relationship(foreign_keys=[acted_by_id], lazy="joined")


class ExpenseHistory(IdMixin, Base):
    """Business history shown to users. Append-only; never deleted."""

    __tablename__ = "expense_history"

    expense_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expenses.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    level_code: Mapped[str | None] = mapped_column(String(10))
    actor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor: Mapped[User] = relationship(lazy="joined")
