"""Contract register (migration 0011): a contract / MSA stored the first time it is claimed;
later claims link to it (expenses.contract_id) so paid and due periods are known."""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.far import M, Partner

FREQUENCIES = ("MONTHLY", "QUARTERLY", "HALF_YEARLY", "YEARLY", "ONE_TIME")
MONTHS_PER = {"MONTHLY": 1, "QUARTERLY": 3, "HALF_YEARLY": 6, "YEARLY": 12}


class Contract(IdMixin, TimestampMixin, Base):
    __tablename__ = "contracts"

    contract_no: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    party_name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(f"{M}.partners.id"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    frequency: Mapped[str] = mapped_column(
        String(12), nullable=False, default="MONTHLY", server_default="MONTHLY"
    )
    period_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))  # pre-tax, each period
    gst_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    contract_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    terms: Mapped[str | None] = mapped_column(String(500))
    department_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(f"{M}.departments.id"))
    branch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(f"{M}.branches.id"))
    expense_type_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("expense_types.id"))
    document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("expense_documents.id"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)

    vendor: Mapped[Partner | None] = relationship(lazy="joined")
