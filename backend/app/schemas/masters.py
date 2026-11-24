"""Master data schemas. Shared (FAR) masters use FAR's columns; extras live in portal profiles."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import Money, ORMModel, Ref, UserRef

GSTIN_RE = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
PAN_RE = r"^[A-Z]{5}[0-9]{4}[A-Z]$"
CODE_RE = r"^[A-Za-z0-9._/-]+$"


class _Trim(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class SharedOut(ORMModel):
    """Every FAR master row: shared live with FAR (edits here show in FAR and vice versa)."""

    id: int
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None
    shared_with: str = "FAR"


# ---- Department (FAR core.departments; key = name in CAPITALS) ----
class DepartmentIn(_Trim):
    name: str = Field(min_length=1, max_length=150, examples=["RELATIONSHIP MANAGEMENT"])


class DepartmentUpdate(_Trim):
    name: str | None = Field(None, min_length=1, max_length=150)


class DepartmentOut(SharedOut):
    name: str


# ---- Branch (FAR core.branches; key = code) + portal GST profile ----
class BranchIn(_Trim):
    code: int = Field(ge=0, description="FAR branch code (whole number)")
    name: str = Field(min_length=1, max_length=150)
    address: str | None = Field(None, max_length=1000)
    state_code: str | None = Field(None, pattern=r"^[0-9]{2}$", description="GST state code")
    city: str | None = Field(None, max_length=100)


class BranchUpdate(_Trim):
    code: int | None = Field(None, ge=0)
    name: str | None = Field(None, min_length=1, max_length=150)
    address: str | None = Field(None, max_length=1000)
    state_code: str | None = Field(None, pattern=r"^[0-9]{2}$")
    city: str | None = Field(None, max_length=100)


class BranchOut(SharedOut):
    code: int
    name: str
    address: str | None
    state_code: str | None
    city: str | None


# ---- Vendor (FAR core.partners; key = name, case-insensitive) + portal GST profile ----
class VendorIn(_Trim):
    type: Literal["Vendor", "Contractor"] = "Vendor"
    name: str = Field(min_length=1, max_length=200)
    department_id: int | None = None
    contact_info: str | None = Field(None, max_length=1000)
    description: str | None = Field(None, max_length=1000)
    gstin: str | None = Field(None, pattern=GSTIN_RE)
    pan: str | None = Field(None, pattern=PAN_RE)
    is_msme: bool = Field(True, description="MSME (Udyam registered); Yes unless set to No")
    udyam_number: str | None = Field(None, max_length=30)

    @field_validator("gstin", "pan", mode="before")
    @classmethod
    def upper(cls, v: object) -> object:
        return v.strip().upper() or None if isinstance(v, str) else v


class VendorUpdate(_Trim):
    type: Literal["Vendor", "Contractor"] | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    department_id: int | None = None
    contact_info: str | None = Field(None, max_length=1000)
    description: str | None = Field(None, max_length=1000)
    gstin: str | None = Field(None, pattern=GSTIN_RE)
    pan: str | None = Field(None, pattern=PAN_RE)
    is_msme: bool | None = None
    udyam_number: str | None = Field(None, max_length=30)

    @field_validator("gstin", "pan", mode="before")
    @classmethod
    def upper(cls, v: object) -> object:
        return v.strip().upper() or None if isinstance(v, str) else v


class VendorOut(SharedOut):
    type: str
    name: str
    department_id: int | None
    contact_info: str | None
    description: str | None
    gstin: str | None
    pan: str | None
    is_msme: bool
    udyam_number: str | None
    state_code: str | None


# ---- Employee (FAR core.assignees, type='employee'; key = employee_code) ----
class EmployeeIn(_Trim):
    employee_code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=150)
    department_id: int | None = None
    email: EmailStr | None = None


class EmployeeUpdate(_Trim):
    employee_code: str | None = Field(None, min_length=1, max_length=30)
    name: str | None = Field(None, min_length=1, max_length=150)
    department_id: int | None = None
    email: EmailStr | None = None


class EmployeeOut(SharedOut):
    employee_code: str | None
    name: str
    department_id: int | None
    department: Ref | None
    email: str | None


# ---- Portal-only masters ----
class _Code(_Trim):
    code: str = Field(min_length=1, max_length=30, pattern=CODE_RE)
    name: str = Field(min_length=1, max_length=150)

    @field_validator("code")
    @classmethod
    def upper_code(cls, v: str) -> str:
        return v.upper()


class LocalOut(ORMModel):
    id: int
    code: str
    name: str
    is_active: bool
    shared_with: None = None


class ExpenseTypeIn(_Code):
    description: str | None = Field(None, max_length=255)
    requires_bill: bool = True


class ExpenseTypeUpdate(_Trim):
    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, max_length=255)
    requires_bill: bool | None = None


class ExpenseTypeOut(LocalOut):
    description: str | None
    requires_bill: bool


class BillMonthOut(ORMModel):
    id: int
    code: str
    label: str
    month_start: date
    financial_year: str
    is_open: bool
    is_active: bool


class BillMonthUpdate(BaseModel):
    is_open: bool


class ApprovalLevelOut(ORMModel):
    id: int
    code: str
    name: str
    rank: int
    is_active: bool


class ApprovalLevelIn(BaseModel):
    code: str = Field(min_length=1, max_length=10, pattern=r"^L[0-9]{1,2}$")
    name: str = Field(min_length=1, max_length=100)
    rank: int = Field(ge=1, le=99)


class ApprovalRuleIn(BaseModel):
    department_id: int
    expense_type_id: int | None = None
    level_id: int
    approver_id: int
    threshold_amount: Money = Field(description="Band starts above this amount")
    approval_limit: Money | None = Field(
        None, description="Band ends at this amount (inclusive); null = no upper limit"
    )


class ApprovalRuleUpdate(BaseModel):
    approver_id: int | None = None
    threshold_amount: Money | None = None
    approval_limit: Money | None = None
    no_limit: bool = Field(False, description="Remove the upper limit (approval_limit = null)")


class ApprovalRuleOut(ORMModel):
    id: int
    department_id: int
    expense_type_id: int | None
    level_id: int
    approver_id: int
    threshold_amount: Money
    approval_limit: Money | None
    is_active: bool
    department: Ref | None = None
    expense_type: Ref | None = None
    level: Ref | None = None
    approver: UserRef | None = None
