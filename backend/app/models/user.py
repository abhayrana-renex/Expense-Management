"""Users, password history and login sessions (refresh tokens)."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.far import Assignee, Branch, Department, M
from app.models.rbac import Role


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    employee_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    designation: Mapped[str | None] = mapped_column(String(100))
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), nullable=False)
    # Department / home branch are FAR's shared masters.
    department_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(f"{M}.departments.id"))
    branch_id: Mapped[int | None] = mapped_column(Integer, ForeignKey(f"{M}.branches.id"))
    # The FAR employee record (core.assignees, type 'employee') this login belongs to.
    assignee_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{M}.assignees.id"), unique=True
    )

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role] = relationship(lazy="joined")
    department: Mapped[Department | None] = relationship(lazy="joined")
    branch: Mapped[Branch | None] = relationship(lazy="joined")
    assignee: Mapped[Assignee | None] = relationship(lazy="joined")

    @property
    def effective_department_id(self) -> int | None:
        """FAR's employee record wins: a department change in FAR applies here at once."""
        if self.assignee is not None and self.assignee.department_id is not None:
            return self.assignee.department_id
        return self.department_id

    @property
    def employee_active(self) -> bool:
        """False when FAR has marked the linked employee Inactive."""
        return self.assignee is None or self.assignee.status == "Active"


class PasswordHistory(IdMixin, Base):
    __tablename__ = "password_history"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(IdMixin, Base):
    """One login. Access tokens carry its id (sid); revoking it kills all its tokens."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(50))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
