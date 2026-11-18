"""Roles, permissions and the role-permission matrix (with data scope)."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin

DATA_SCOPES = ("OWN", "ASSIGNED", "DEPARTMENT", "ALL")


class Role(IdMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    # Built-in roles can't be renamed or removed; SUPER_ADMIN's rights can't be changed at all.
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    grants: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", lazy="selectin", cascade="all, delete-orphan"
    )


class Permission(IdMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)


class RolePermission(IdMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
        CheckConstraint(f"scope IN {DATA_SCOPES}", name="scope_valid"),
    )

    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("roles.id"), nullable=False)
    permission_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("permissions.id"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="ALL")

    role: Mapped[Role] = relationship(back_populates="grants")
    permission: Mapped[Permission] = relationship(lazy="joined")
