"""Auth and user schemas."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.schemas.common import ORMModel, Ref


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=60, examples=["admin"])
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class RoleOut(ORMModel):
    id: int
    code: str
    name: str


class UserOut(ORMModel):
    id: int
    employee_code: str
    username: str
    full_name: str
    email: str
    phone: str | None
    designation: str | None
    role: RoleOut
    department: Ref | None
    branch: Ref | None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None
    locked_until: datetime | None
    assignee_id: int | None = Field(description="Linked FAR employee (core.assignees.id)")
    employee_active: bool = Field(description="False when FAR marked the employee Inactive")
    effective_department_id: int | None
    created_at: datetime


class MeOut(BaseModel):
    user: UserOut
    role: str
    permissions: dict[str, str] = Field(description="permission code -> data scope")
    must_change_password: bool
    password_expired: bool
    feature_flags: dict[str, bool | str]  # flags, plus approval_routing (band/chain/matrix)
    password_policy: dict[str, int]
    expense_defaults: dict[str, int | None] = Field(
        description="Pre-fill for a new expense: department_id, branch_id, cost_centre_id "
        "(DEFAULT_DEPARTMENT / DEFAULT_BRANCH, else the user's own; the cost centre linked "
        "to that department). The user can change each one."
    )


class UserCreate(BaseModel):
    assignee_id: int | None = Field(
        None,
        description="FAR employee to link. Code, name, email and department default "
        "from FAR; FAR stays the source for department and active status.",
    )
    employee_code: str | None = Field(None, min_length=1, max_length=30)
    username: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9._-]+$")
    full_name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=20)
    designation: str | None = Field(None, max_length=100)
    role_code: str
    department_id: int | None = None
    branch_id: int | None = None
    temporary_password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def lower(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def identity(self) -> "UserCreate":
        if self.assignee_id is None and not (self.employee_code and self.full_name and self.email):
            raise ValueError("Pick a FAR employee, or give employee_code, full_name and email")
        return self


class UserUpdate(BaseModel):
    assignee_id: int | None = None
    full_name: str | None = Field(None, min_length=1, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=20)
    designation: str | None = Field(None, max_length=100)
    role_code: str | None = None
    department_id: int | None = None
    branch_id: int | None = None


class ResetPasswordRequest(BaseModel):
    temporary_password: str = Field(min_length=1, max_length=128)
