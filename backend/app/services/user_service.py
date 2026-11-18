"""User management. Users are never hard-deleted; they are deactivated."""

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, conflict, not_found
from app.core.security import hash_password, utcnow
from app.models import Assignee, Branch, Department, Role, User
from app.schemas.auth import UserCreate, UserUpdate
from app.services import audit_service, auth_service

PROTECTED_ROLE = "SUPER_ADMIN"


def _role(db: Session, code: str) -> Role:
    role = db.execute(select(Role).where(Role.code == code)).scalar_one_or_none()
    if role is None:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown role '{code}'")
    return role


def _check_refs(db: Session, department_id: int | None, branch_id: int | None) -> None:
    if department_id is not None and db.get(Department, department_id) is None:
        raise AppError(422, "VALIDATION_ERROR", "Department does not exist")
    if branch_id is not None and db.get(Branch, branch_id) is None:
        raise AppError(422, "VALIDATION_ERROR", "Branch does not exist")


def _far_employee(db: Session, assignee_id: int, user_id: int | None = None) -> Assignee:
    """The FAR employee (core.assignees) a login is linked to: must exist, be an active
    employee, and not already be linked to another login."""
    emp = db.get(Assignee, assignee_id)
    if emp is None or emp.type != "employee":
        raise AppError(
            422, "VALIDATION_ERROR", "FAR employee does not exist", [{"field": "assignee_id"}]
        )
    if emp.status != "Active":
        raise AppError(
            422, "VALIDATION_ERROR", "FAR employee is Inactive", [{"field": "assignee_id"}]
        )
    taken = db.execute(select(User.id).where(User.assignee_id == assignee_id)).scalar()
    if taken is not None and taken != user_id:
        raise AppError(
            409,
            "DUPLICATE",
            "This FAR employee already has a portal login",
            [{"field": "assignee_id"}],
        )
    return emp


def _guard_role_change(actor: Principal, target_role: str) -> None:
    if target_role == PROTECTED_ROLE and actor.role != PROTECTED_ROLE:
        raise AppError(403, "FORBIDDEN", "Only a SUPER_ADMIN can grant or manage SUPER_ADMIN")


def get(db: Session, user_id: int) -> User:
    user = db.execute(select(User).where(User.id == user_id)).unique().scalar_one_or_none()
    if user is None:
        raise not_found("User")
    return user


def list_query(
    q: str | None, role: str | None, department_id: int | None, is_active: bool | None
) -> Select:
    stmt = select(User)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                User.full_name.ilike(like),
                User.username.ilike(like),
                User.employee_code.ilike(like),
                User.email.ilike(like),
            )
        )
    if role:
        stmt = stmt.join(Role, Role.id == User.role_id).where(Role.code == role)
    if department_id:
        stmt = stmt.where(User.department_id == department_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    return stmt


def create(db: Session, actor: Principal, data: UserCreate) -> User:
    _guard_role_change(actor, data.role_code)
    role = _role(db, data.role_code)
    _check_refs(db, data.department_id, data.branch_id)
    emp = _far_employee(db, data.assignee_id) if data.assignee_id else None
    employee_code = data.employee_code or (emp.employee_code if emp else None)
    full_name = data.full_name or (emp.name if emp else None)
    email = data.email or (emp.email if emp else None)
    if not (employee_code and full_name and email):
        raise AppError(
            422, "VALIDATION_ERROR", "The FAR employee has no code or email; enter them here"
        )
    user = User(
        employee_code=employee_code.strip().upper(),
        username=data.username,
        full_name=full_name.strip(),
        email=str(email).lower(),
        phone=data.phone,
        designation=data.designation,
        role_id=role.id,
        department_id=data.department_id or (emp.department_id if emp else None),
        branch_id=data.branch_id,
        assignee_id=emp.id if emp else None,
        password_hash="!",
        must_change_password=True,
    )
    errors = auth_service.password_policy_errors(data.temporary_password, user)
    if errors:
        raise AppError(
            422,
            "PASSWORD_POLICY",
            "Temporary password does not meet the policy",
            [{"msg": e} for e in errors],
        )
    user.password_hash = hash_password(data.temporary_password)
    db.add(user)
    db.flush()
    audit_service.record(
        db,
        "USER_CREATED",
        "user",
        user.id,
        actor=actor.user,
        details={"username": user.username, "role": role.code, "far_employee_id": user.assignee_id},
    )
    db.commit()
    return get(db, user.id)


def update(db: Session, actor: Principal, user_id: int, data: UserUpdate) -> User:
    user = get(db, user_id)
    if user.role.code == PROTECTED_ROLE:
        _guard_role_change(actor, PROTECTED_ROLE)
    changes = data.model_dump(exclude_unset=True)
    before = {k: getattr(user, k, None) for k in changes if k != "role_code"}
    before["role_code"] = user.role.code
    if "role_code" in changes:
        _guard_role_change(actor, changes["role_code"])
        if user.id == actor.user.id and changes["role_code"] != user.role.code:
            raise AppError(403, "FORBIDDEN", "You cannot change your own role")
        user.role_id = _role(db, changes.pop("role_code")).id
    _check_refs(db, changes.get("department_id"), changes.get("branch_id"))
    if changes.get("assignee_id") is not None:
        _far_employee(db, changes["assignee_id"], user.id)
    if "email" in changes and changes["email"]:
        changes["email"] = str(changes["email"]).lower()
    for field, value in changes.items():
        setattr(user, field, value)
    db.flush()
    after = {k: getattr(user, k, None) for k in before if k != "role_code"}
    after["role_code"] = data.role_code or before["role_code"]
    audit_service.record(
        db,
        "USER_UPDATED",
        "user",
        user.id,
        actor=actor.user,
        details=audit_service.diff(before, after),
    )
    db.commit()
    return get(db, user.id)


def set_active(db: Session, actor: Principal, user_id: int, active: bool) -> User:
    user = get(db, user_id)
    if user.id == actor.user.id:
        raise conflict("SELF_ACTION", "You cannot deactivate your own account")
    if user.role.code == PROTECTED_ROLE:
        _guard_role_change(actor, PROTECTED_ROLE)
    if not active:
        others = db.execute(
            select(func.count())
            .select_from(User)
            .join(Role, Role.id == User.role_id)
            .where(Role.code == PROTECTED_ROLE, User.is_active.is_(True), User.id != user.id)
        ).scalar_one()
        if user.role.code == PROTECTED_ROLE and others == 0:
            raise conflict("LAST_SUPER_ADMIN", "Cannot deactivate the last active SUPER_ADMIN")
        auth_service.revoke_all_sessions(db, user.id, "DEACTIVATED")
    user.is_active = active
    user.deactivated_at = None if active else utcnow()
    audit_service.record(
        db, "USER_ACTIVATED" if active else "USER_DEACTIVATED", "user", user.id, actor=actor.user
    )
    db.commit()
    return get(db, user.id)


def force_password_change(db: Session, actor: Principal, user_id: int) -> User:
    user = get(db, user_id)
    user.must_change_password = True
    audit_service.record(db, "USER_FORCE_PASSWORD_CHANGE", "user", user.id, actor=actor.user)
    db.commit()
    return get(db, user.id)


def reset_password(db: Session, actor: Principal, user_id: int, temporary_password: str) -> User:
    user = get(db, user_id)
    if user.role.code == PROTECTED_ROLE:
        _guard_role_change(actor, PROTECTED_ROLE)
    auth_service.set_password(db, user, temporary_password, must_change=True)
    user.failed_login_count = 0
    user.locked_until = None
    auth_service.revoke_all_sessions(db, user.id, "PASSWORD_RESET")
    audit_service.record(db, "USER_PASSWORD_RESET", "user", user.id, actor=actor.user)
    db.commit()
    return get(db, user.id)


def unlock(db: Session, actor: Principal, user_id: int) -> User:
    user = get(db, user_id)
    user.failed_login_count = 0
    user.locked_until = None
    audit_service.record(db, "USER_UNLOCKED", "user", user.id, actor=actor.user)
    db.commit()
    return get(db, user.id)
