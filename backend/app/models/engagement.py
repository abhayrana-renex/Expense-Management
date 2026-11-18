"""In-app notifications and out-of-office approval delegation."""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin
from app.models.user import User


class Notification(IdMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_unread", "user_id", "read_at"),)

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    expense_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("expenses.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delegation(IdMixin, Base):
    """While active, `delegate` may act on approvals assigned to `approver` (audited)."""

    __tablename__ = "delegations"
    __table_args__ = (
        Index("ix_delegations_delegate_dates", "delegate_id", "starts_on", "ends_on"),
    )

    approver_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    delegate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    approver: Mapped[User] = relationship(foreign_keys=[approver_id], lazy="joined")
    delegate: Mapped[User] = relationship(foreign_keys=[delegate_id], lazy="joined")
