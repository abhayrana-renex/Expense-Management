"""Portal-only masters (schema DB_SCHEMA) and portal extensions of FAR's shared masters."""

from datetime import date, datetime
from decimal import Decimal

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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.far import Branch, Department, M, Partner


def _active() -> Mapped[bool]:
    return mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


# ---------- Extensions of FAR masters (portal-only fields, 1:1 with the FAR row) ----------


class VendorProfile(Base):
    """GST/MSME details FAR doesn't hold. Keyed by FAR's partner id."""

    __tablename__ = "vendor_profiles"

    partner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{M}.partners.id"), primary_key=True
    )
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    # MSME unless someone says otherwise (migration 0010).
    is_msme: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    udyam_number: Mapped[str | None] = mapped_column(String(30))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


Index(
    "uq_vendor_profiles_gstin",
    VendorProfile.gstin,
    unique=True,
    postgresql_where=text("gstin IS NOT NULL"),
)


class BranchProfile(Base):
    """GST state code and city for a FAR branch (needed for intra/inter-state GST checks)."""

    __tablename__ = "branch_profiles"

    branch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{M}.branches.id"), primary_key=True
    )
    state_code: Mapped[str | None] = mapped_column(String(2))
    city: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


Partner.profile = relationship(VendorProfile, lazy="joined", uselist=False, viewonly=True)
Branch.profile = relationship(BranchProfile, lazy="joined", uselist=False, viewonly=True)


# ---------- Portal-only masters ----------


class CostCentre(IdMixin, TimestampMixin, Base):
    __tablename__ = "cost_centres"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    department_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(f"{M}.departments.id"))
    is_active: Mapped[bool] = _active()


class ExpenseType(IdMixin, TimestampMixin, Base):
    __tablename__ = "expense_types"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    requires_bill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_active: Mapped[bool] = _active()


class BillMonth(IdMixin, TimestampMixin, Base):
    __tablename__ = "bill_months"

    code: Mapped[str] = mapped_column(String(7), unique=True, nullable=False)  # YYYY-MM
    label: Mapped[str] = mapped_column(String(20), nullable=False)  # Apr 2026
    month_start: Mapped[date] = mapped_column(Date, nullable=False)
    financial_year: Mapped[str] = mapped_column(String(9), nullable=False)  # 2026-27
    is_open: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_active: Mapped[bool] = _active()


class GstRate(IdMixin, TimestampMixin, Base):
    """GST rate options offered for CGST / SGST / IGST (admin-managed, never hardcoded)."""

    __tablename__ = "gst_rates"
    __table_args__ = (
        UniqueConstraint("rate", "applies_to", name="uq_gst_rates_rate_applies_to"),
        CheckConstraint("applies_to IN ('ALL','CGST','SGST','IGST')", name="applies_to_valid"),
        CheckConstraint("rate >= 0 AND rate <= 100", name="rate_range"),
    )

    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    applies_to: Mapped[str] = mapped_column(String(4), nullable=False, default="ALL")
    is_active: Mapped[bool] = _active()


class ApprovalLevel(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_levels"

    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)  # L1
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(nullable=False, unique=True)
    is_active: Mapped[bool] = _active()


class ApprovalRule(IdMixin, TimestampMixin, Base):
    """Who approves at a level, for a FAR department (optionally one expense type).

    Band routing (default): the rule covers totals in (threshold_amount, approval_limit];
    approval_limit NULL means no upper limit. Chain routing: every rule whose threshold the
    total exceeds approves in turn.
    """

    __tablename__ = "approval_rules"

    department_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{M}.departments.id"), nullable=False
    )
    expense_type_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("expense_types.id"))
    level_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("approval_levels.id"), nullable=False
    )
    approver_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    threshold_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00")
    )
    approval_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # None = no limit
    is_active: Mapped[bool] = _active()

    department: Mapped[Department] = relationship(lazy="joined")
    expense_type: Mapped[ExpenseType | None] = relationship(lazy="joined")
    level: Mapped[ApprovalLevel] = relationship(lazy="joined")
    approver: Mapped["User"] = relationship(lazy="joined")  # noqa: F821


Index(
    "uq_approval_rules_dept_level",
    ApprovalRule.department_id,
    ApprovalRule.level_id,
    unique=True,
    postgresql_where=text("expense_type_id IS NULL AND is_active"),
)
Index(
    "uq_approval_rules_dept_type_level",
    ApprovalRule.department_id,
    ApprovalRule.expense_type_id,
    ApprovalRule.level_id,
    unique=True,
    postgresql_where=text("expense_type_id IS NOT NULL AND is_active"),
)
