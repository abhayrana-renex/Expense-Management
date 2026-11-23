"""Discussion on an expense (migration 0012): questions and replies between the people in its
process (initiator and approvers), with @mentions and attachments.

Append-only, like the approval history: a DB trigger blocks UPDATE / DELETE on all three
tables. A mistake is fixed by posting a follow-up."""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Table, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin
from app.models.user import User

message_mentions = Table(
    "expense_message_mentions",
    Base.metadata,
    Column("message_id", BigInteger, ForeignKey("expense_messages.id"), primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id"), primary_key=True),
)


class ExpenseMessage(IdMixin, Base):
    __tablename__ = "expense_messages"

    expense_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expenses.id"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    author: Mapped[User] = relationship(lazy="joined")
    mentions: Mapped[list[User]] = relationship(secondary=message_mentions, lazy="selectin")
    files: Mapped[list["ExpenseMessageFile"]] = relationship(
        lazy="selectin", order_by="ExpenseMessageFile.id"
    )


class ExpenseMessageFile(IdMixin, Base):
    """An attachment on a message (PDF / JPG / PNG, checked like a bill). Never a bill: it is
    not part of the claim and can't be attached to a line."""

    __tablename__ = "expense_message_files"

    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("expense_messages.id"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
