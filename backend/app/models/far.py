"""FAR's shared master tables (schema MASTER_SCHEMA, normally `core`).

These tables are OWNED BY FAR. The portal reads and writes them in place so both
applications always see the same data, but its migrations never create or alter them
(see alembic/env.py include_object). Columns mirror FAR exactly:
- integer serial ids, `status` 'Active'|'Inactive' (soft retire, never hard-delete here)
- audit columns created_at/created_by/updated_at/updated_by (by = core.users id or NULL)
- department and branch names are stored in CAPITALS (FAR convention)
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.models.base import Base

M = settings.MASTER_SCHEMA
FAR_TABLE = {"schema": M, "info": {"owned_by": "FAR"}}


class FarMaster:
    """Columns and helpers common to every FAR master."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="Active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(Integer)

    @hybrid_property
    def is_active(self) -> bool:
        return self.status == "Active"

    @is_active.inplace.expression
    @classmethod
    def _is_active_expression(cls):  # noqa: ANN206
        return cls.status == "Active"


class Department(FarMaster, Base):
    __tablename__ = "departments"
    __table_args__ = FAR_TABLE

    name: Mapped[str] = mapped_column(Text, nullable=False)


class Branch(FarMaster, Base):
    __tablename__ = "branches"
    __table_args__ = FAR_TABLE

    name: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[int] = mapped_column(Integer, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)

    # `profile` (BranchProfile) is attached in app/models/masters.py.

    @property
    def state_code(self) -> str | None:
        return self.profile.state_code if self.profile else None

    @property
    def city(self) -> str | None:
        return self.profile.city if self.profile else None


class Partner(FarMaster, Base):
    """FAR 'partners' = vendors and contractors. The portal calls them vendors."""

    __tablename__ = "partners"
    __table_args__ = FAR_TABLE

    type: Mapped[str] = mapped_column(Text, nullable=False, default="Vendor")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    department_id: Mapped[int | None] = mapped_column(ForeignKey(f"{M}.departments.id"))
    contact_info: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    # `profile` (VendorProfile) is attached in app/models/masters.py.

    @property
    def gstin(self) -> str | None:
        return self.profile.gstin if self.profile else None

    @property
    def pan(self) -> str | None:
        return self.profile.pan if self.profile else None

    @property
    def is_msme(self) -> bool:
        return bool(self.profile and self.profile.is_msme)

    @property
    def udyam_number(self) -> str | None:
        return self.profile.udyam_number if self.profile else None

    @property
    def state_code(self) -> str | None:
        return self.gstin[:2] if self.gstin else None


class Assignee(FarMaster, Base):
    """FAR 'assignees': employees (type='employee') and partner staff (type='partner')."""

    __tablename__ = "assignees"
    __table_args__ = FAR_TABLE

    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    employee_code: Mapped[str | None] = mapped_column(Text)
    company_name: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[int | None] = mapped_column(ForeignKey(f"{M}.departments.id"))
    email: Mapped[str | None] = mapped_column(String)

    department: Mapped[Department | None] = relationship(lazy="joined")
