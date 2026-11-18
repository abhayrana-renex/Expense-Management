"""Master CRUD. Shared masters are FAR's own rows (written in place, FAR's rules applied);
portal-only masters live in the portal schema. Nothing is ever hard-deleted."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import utcnow
from app.models import (
    Assignee,
    Branch,
    BranchProfile,
    CostCentre,
    Department,
    ExpenseType,
    Partner,
    VendorProfile,
)
from app.schemas import masters as s
from app.services import audit_service


@dataclass(frozen=True)
class Unique:
    field: str
    case_insensitive: bool = True


@dataclass(frozen=True)
class MasterSpec:
    key: str  # URL segment
    model: type
    label: str
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    out_schema: type[BaseModel]
    shared: bool  # True = FAR's table, edited in place
    search_fields: tuple[str, ...]
    sort_fields: tuple[str, ...]
    uniques: tuple[Unique, ...] = ()
    upper_fields: tuple[str, ...] = ()  # FAR stores these in CAPITALS
    fixed: dict[str, Any] = field(default_factory=dict)  # values forced on create
    base_filter: Callable[[], ColumnElement[bool]] | None = None
    profile_model: type | None = None
    profile_key: str = ""
    profile_fields: tuple[str, ...] = ()


MASTERS: dict[str, MasterSpec] = {
    spec.key: spec
    for spec in (
        MasterSpec(
            "departments",
            Department,
            "Department",
            s.DepartmentIn,
            s.DepartmentUpdate,
            s.DepartmentOut,
            shared=True,
            search_fields=("name",),
            sort_fields=("name", "created_at"),
            uniques=(Unique("name"),),
            upper_fields=("name",),
        ),
        MasterSpec(
            "branches",
            Branch,
            "Branch",
            s.BranchIn,
            s.BranchUpdate,
            s.BranchOut,
            shared=True,
            search_fields=("name", "address"),
            sort_fields=("code", "name", "created_at"),
            uniques=(Unique("code", case_insensitive=False), Unique("name")),
            upper_fields=("name",),
            profile_model=BranchProfile,
            profile_key="branch_id",
            profile_fields=("state_code", "city"),
        ),
        MasterSpec(
            "vendors",
            Partner,
            "Vendor",
            s.VendorIn,
            s.VendorUpdate,
            s.VendorOut,
            shared=True,
            search_fields=("name", "contact_info"),
            sort_fields=("name", "created_at"),
            uniques=(Unique("name"),),
            profile_model=VendorProfile,
            profile_key="partner_id",
            profile_fields=("gstin", "pan", "is_msme", "udyam_number"),
        ),
        MasterSpec(
            "employees",
            Assignee,
            "Employee",
            s.EmployeeIn,
            s.EmployeeUpdate,
            s.EmployeeOut,
            shared=True,
            search_fields=("name", "employee_code", "email"),
            sort_fields=("name", "employee_code", "created_at"),
            uniques=(Unique("employee_code"),),
            fixed={"type": "employee"},
            base_filter=lambda: Assignee.type == "employee",
        ),
        MasterSpec(
            "cost-centres",
            CostCentre,
            "Cost centre",
            s.CostCentreIn,
            s.CostCentreUpdate,
            s.CostCentreOut,
            shared=False,
            search_fields=("code", "name"),
            sort_fields=("code", "name", "created_at"),
            uniques=(Unique("code"),),
        ),
        MasterSpec(
            "expense-types",
            ExpenseType,
            "Expense type",
            s.ExpenseTypeIn,
            s.ExpenseTypeUpdate,
            s.ExpenseTypeOut,
            shared=False,
            search_fields=("code", "name"),
            sort_fields=("name", "code", "created_at"),
            uniques=(Unique("code"),),
        ),
    )
}


def _scoped(spec: MasterSpec, stmt: Select) -> Select:
    return stmt.where(spec.base_filter()) if spec.base_filter else stmt


def list_query(spec: MasterSpec, q: str | None, is_active: bool | None) -> Select:
    stmt = _scoped(spec, select(spec.model))
    if q:
        like = f"%{q.strip()}%"
        cols = [getattr(spec.model, f) for f in spec.search_fields]
        if spec.key == "branches" and q.strip().isdigit():
            stmt = stmt.where(or_(Branch.code == int(q.strip()), *[c.ilike(like) for c in cols]))
        else:
            stmt = stmt.where(or_(*[c.ilike(like) for c in cols]))
    if is_active is not None:
        stmt = stmt.where(spec.model.is_active.is_(is_active))
    return stmt


def get(db: Session, spec: MasterSpec, obj_id: int) -> Any:
    obj = (
        db.execute(_scoped(spec, select(spec.model).where(spec.model.id == obj_id)))
        .unique()
        .scalar_one_or_none()
    )
    if obj is None:
        raise not_found(spec.label)
    return obj


def _check_unique(
    db: Session, spec: MasterSpec, values: dict[str, Any], exclude_id: int | None
) -> None:
    # Serialise portal writers per table; FAR's own writes are covered by its unique keys.
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:t))"), {"t": spec.model.__table__.fullname}
    )
    for rule in spec.uniques:
        value = values.get(rule.field)
        if value is None:
            continue
        col = getattr(spec.model, rule.field)
        cond = (
            func.lower(func.btrim(col)) == str(value).strip().lower()
            if rule.case_insensitive
            else col == value
        )
        stmt = _scoped(spec, select(spec.model.id).where(cond))
        if exclude_id is not None:
            stmt = stmt.where(spec.model.id != exclude_id)
        if db.execute(stmt.limit(1)).first():
            raise AppError(
                409,
                "DUPLICATE",
                f"A {spec.label.lower()} with this {rule.field.replace('_', ' ')} "
                "already exists (in the portal or FAR)",
                [{"field": rule.field}],
            )


def _normalise(spec: MasterSpec, data: dict[str, Any]) -> dict[str, Any]:
    for f in spec.upper_fields:
        if isinstance(data.get(f), str):
            data[f] = data[f].strip().upper()
    if isinstance(data.get("email"), str):
        data["email"] = data["email"].lower()
    return data


def _check_refs(db: Session, data: dict[str, Any]) -> None:
    dept_id = data.get("department_id")
    if dept_id is not None and db.get(Department, dept_id) is None:
        raise AppError(
            422, "VALIDATION_ERROR", "Department does not exist", [{"field": "department_id"}]
        )


def _save_profile(db: Session, spec: MasterSpec, obj_id: int, values: dict[str, Any]) -> None:
    if not spec.profile_model or not values:
        return
    profile = db.get(spec.profile_model, obj_id)
    if profile is None:
        profile = spec.profile_model(**{spec.profile_key: obj_id})
        db.add(profile)
    for key, value in values.items():
        setattr(profile, key, value)


def _split(spec: MasterSpec, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    profile = {k: data.pop(k) for k in list(data) if k in spec.profile_fields}
    return data, profile


def _snapshot(obj: Any, keys: Any) -> dict[str, Any]:
    return {k: getattr(obj, k, None) for k in keys}


def create(db: Session, actor: Principal, spec: MasterSpec, data: BaseModel) -> Any:
    values, profile = _split(spec, _normalise(spec, data.model_dump()))
    _check_refs(db, values)
    _check_unique(db, spec, values, None)
    obj = spec.model(**values, **spec.fixed)
    if spec.shared:
        obj.status = "Active"  # created_by stays NULL: portal users aren't FAR users
    db.add(obj)
    db.flush()
    _save_profile(db, spec, obj.id, profile)
    audit_service.record(
        db,
        "MASTER_CREATED",
        spec.key,
        obj.id,
        actor=actor.user,
        details={"shared_with_far": spec.shared, **values, **profile},
    )
    db.commit()
    db.expire_all()
    return get(db, spec, obj.id)


def update(db: Session, actor: Principal, spec: MasterSpec, obj_id: int, data: BaseModel) -> Any:
    obj = get(db, spec, obj_id)
    values, profile = _split(spec, _normalise(spec, data.model_dump(exclude_unset=True)))
    _check_refs(db, values)
    _check_unique(db, spec, values, obj.id)
    before = _snapshot(obj, list(values) + list(profile))
    for key, value in values.items():
        setattr(obj, key, value)
    if spec.shared:
        obj.updated_at = utcnow()
        obj.updated_by = None
    _save_profile(db, spec, obj.id, profile)
    db.flush()
    audit_service.record(
        db,
        "MASTER_UPDATED",
        spec.key,
        obj.id,
        actor=actor.user,
        details={
            "shared_with_far": spec.shared,
            **audit_service.diff(before, {**values, **profile}),
        },
    )
    db.commit()
    db.expire_all()
    return get(db, spec, obj.id)


def set_active(db: Session, actor: Principal, spec: MasterSpec, obj_id: int, active: bool) -> Any:
    """Deactivate instead of delete: expenses and FAR assets keep their reference."""
    obj = get(db, spec, obj_id)
    if spec.shared:
        obj.status = "Active" if active else "Inactive"
        obj.updated_at = utcnow()
        obj.updated_by = None
    else:
        obj.is_active = active
    audit_service.record(
        db,
        "MASTER_ACTIVATED" if active else "MASTER_DEACTIVATED",
        spec.key,
        obj.id,
        actor=actor.user,
        details={"shared_with_far": spec.shared},
    )
    db.commit()
    db.expire_all()
    return get(db, spec, obj.id)
