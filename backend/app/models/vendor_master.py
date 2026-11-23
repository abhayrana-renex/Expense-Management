"""Vendor master details the portal keeps for FAR's vendors (migration 0014): escalation
contacts, bank accounts (maker-checker), GST registrations and contracts. Keyed by FAR's
partner id; FAR's own tables are never changed."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.far import M

# Who to call, in escalation order.
CONTACT_KINDS = (
    "L1_SUPPORT",
    "L2_SUPPORT",
    "L3_SUPPORT",
    "BILLING",
    "ACCOUNT_MANAGER",
    "ESCALATION",
)
ACCOUNT_TYPES = ("SAVINGS", "CURRENT", "CC", "OD")
# PENDING (entered, waiting for a second person) -> ACTIVE (used for payments) ->
# SUPERSEDED (a newer account was approved). REJECTED requests are kept too.
BANK_STATUSES = ("PENDING", "ACTIVE", "REJECTED", "SUPERSEDED")
CONTRACT_TYPES = ("NDA", "MSA", "DO")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _vendor() -> Mapped[int]:
    return mapped_column(Integer, ForeignKey(f"{M}.partners.id"), nullable=False, index=True)


def _user(nullable: bool = True) -> Mapped:
    return mapped_column(BigInteger, ForeignKey("users.id"), nullable=nullable)


class VendorContact(IdMixin, TimestampMixin, Base):
    __tablename__ = "vendor_contacts"
    __table_args__ = (
        UniqueConstraint("partner_id", "kind", name="uq_vendor_contacts_partner_kind"),
        CheckConstraint(_in("kind", CONTACT_KINDS), name="kind_valid"),
    )

    partner_id: Mapped[int] = _vendor()
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    mobile: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(254))
    updated_by_id: Mapped[int | None] = _user()


class VendorBankAccount(IdMixin, TimestampMixin, Base):
    """A vendor's bank account. Never edited or deleted: a change is a new row, used for
    payments only after someone other than the person who entered it approves it."""

    __tablename__ = "vendor_bank_accounts"
    __table_args__ = (
        CheckConstraint(_in("account_type", ACCOUNT_TYPES), name="account_type_valid"),
        CheckConstraint(_in("status", BANK_STATUSES), name="status_valid"),
        CheckConstraint(
            "decided_by_id IS NULL OR decided_by_id <> requested_by_id", name="maker_is_not_checker"
        ),
    )

    partner_id: Mapped[int] = _vendor()
    account_type: Mapped[str] = mapped_column(String(10), nullable=False)
    account_no: Mapped[str] = mapped_column(String(20), nullable=False)
    ifsc: Mapped[str] = mapped_column(String(11), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    bank_branch: Mapped[str | None] = mapped_column(String(120))
    signing_authority: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(60))
    wef: Mapped[date] = mapped_column(Date, nullable=False)  # with effect from
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="PENDING")
    requested_by_id: Mapped[int] = _user(nullable=False)
    decided_by_id: Mapped[int | None] = _user()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))


# One account in use and one change waiting, per vendor, at most.
Index(
    "uq_vendor_bank_active",
    VendorBankAccount.partner_id,
    unique=True,
    postgresql_where=text("status = 'ACTIVE'"),
)
Index(
    "uq_vendor_bank_pending",
    VendorBankAccount.partner_id,
    unique=True,
    postgresql_where=text("status = 'PENDING'"),
)


class VendorGstRegistration(IdMixin, TimestampMixin, Base):
    """One GSTIN per state/branch the vendor bills from. A GSTIN belongs to one vendor."""

    __tablename__ = "vendor_gst_registrations"
    __table_args__ = (UniqueConstraint("gstin", name="uq_vendor_gst_registrations_gstin"),)

    partner_id: Mapped[int] = _vendor()
    gstin: Mapped[str] = mapped_column(String(15), nullable=False)
    state: Mapped[str | None] = mapped_column(String(60))
    branch_name: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    updated_by_id: Mapped[int | None] = _user()


class VendorContract(IdMixin, TimestampMixin, Base):
    """NDA / MSA / DO with the vendor. The signed copy, once stored, is never replaced: a new
    version is a new row and the old one is set inactive."""

    __tablename__ = "vendor_contracts"
    __table_args__ = (
        CheckConstraint(_in("contract_type", CONTRACT_TYPES), name="contract_type_valid"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="period_valid"),
    )

    partner_id: Mapped[int] = _vendor()
    contract_type: Mapped[str] = mapped_column(String(10), nullable=False)
    contract_no: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    original_filename: Mapped[str | None] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_by_id: Mapped[int] = _user(nullable=False)
