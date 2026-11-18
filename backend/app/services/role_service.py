"""Roles & rights: create roles and change which permissions (with data scope) each has."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, conflict, not_found
from app.core.permissions import PERMISSIONS
from app.models import Permission, Role, RolePermission, User
from app.services import audit_service

LOCKED = "SUPER_ADMIN"
KEEP_ALIVE = ("role.manage", "user.manage")  # someone must always hold these


def catalogue() -> list[dict]:
    return [
        {"code": code, "description": d, "module": m, "scopes": list(s)}
        for code, (d, m, s) in PERMISSIONS.items()
    ]


def _grants(role: Role) -> dict[str, str]:
    return {g.permission.code: g.scope for g in role.grants}


def list_roles(db: Session) -> list[dict]:
    counts = dict(
        db.execute(
            select(User.role_id, func.count())
            .where(User.is_active.is_(True))
            .group_by(User.role_id)
        ).all()
    )
    roles = db.execute(select(Role).order_by(Role.is_system.desc(), Role.id)).scalars()
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "description": r.description,
            "is_system": r.is_system,
            "is_active": r.is_active,
            "locked": r.code == LOCKED,
            "active_users": counts.get(r.id, 0),
            "grants": _grants(r),
        }
        for r in roles
    ]


def _validate(grants: dict[str, str]) -> None:
    errors = []
    for code, scope in grants.items():
        if code not in PERMISSIONS:
            errors.append({"permission": code, "msg": "Unknown permission"})
        elif scope not in PERMISSIONS[code][2]:
            errors.append(
                {
                    "permission": code,
                    "msg": f"Scope must be one of {', '.join(PERMISSIONS[code][2])}",
                }
            )
    if errors:
        raise AppError(422, "VALIDATION_ERROR", "Invalid rights", errors)


def _apply(db: Session, role: Role, grants: dict[str, str]) -> None:
    perms = {p.code: p for p in db.execute(select(Permission)).scalars()}
    current = {g.permission.code: g for g in role.grants}
    for code, grant in current.items():
        if code not in grants:
            role.grants.remove(grant)
    for code, scope in grants.items():
        if code in current:
            current[code].scope = scope
        else:
            role.grants.append(RolePermission(permission_id=perms[code].id, scope=scope))


def _ensure_keep_alive(db: Session) -> None:
    db.flush()
    for code in KEEP_ALIVE:
        holders = db.execute(
            select(func.count())
            .select_from(RolePermission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .join(Role, Role.id == RolePermission.role_id)
            .where(Permission.code == code, Role.is_active.is_(True))
        ).scalar_one()
        if not holders:
            raise conflict("LAST_ADMIN_RIGHT", f"At least one active role must keep '{code}'")


def create(
    db: Session,
    actor: Principal,
    code: str,
    name: str,
    description: str | None,
    grants: dict[str, str],
) -> dict:
    code = code.strip().upper()
    if db.execute(select(Role.id).where(Role.code == code)).first():
        raise conflict("DUPLICATE", f"Role {code} already exists")
    _validate(grants)
    role = Role(code=code, name=name.strip(), description=description, is_system=False)
    db.add(role)
    db.flush()
    _apply(db, role, grants)
    audit_service.record(
        db,
        "ROLE_CREATED",
        "role",
        role.id,
        actor=actor.user,
        details={"code": code, "grants": grants},
    )
    db.commit()
    return next(r for r in list_roles(db) if r["id"] == role.id)


def update(
    db: Session,
    actor: Principal,
    role_id: int,
    name: str | None,
    description: str | None,
    grants: dict[str, str] | None,
    is_active: bool | None,
) -> dict:
    role = db.get(Role, role_id)
    if role is None:
        raise not_found("Role")
    if role.code == LOCKED and (grants is not None or is_active is False):
        raise AppError(403, "FORBIDDEN", "SUPER_ADMIN rights are fixed")
    if role.is_system and is_active is False:
        raise AppError(403, "FORBIDDEN", "Built-in roles can't be switched off")
    before = {"name": role.name, "grants": _grants(role), "is_active": role.is_active}
    if name is not None and not role.is_system:
        role.name = name.strip()
    if description is not None:
        role.description = description
    if is_active is not None:
        role.is_active = is_active
    if grants is not None:
        _validate(grants)
        _apply(db, role, grants)
    _ensure_keep_alive(db)
    after = {"name": role.name, "grants": _grants(role), "is_active": role.is_active}
    added = {k: v for k, v in after["grants"].items() if before["grants"].get(k) != v}
    removed = sorted(set(before["grants"]) - set(after["grants"]))
    audit_service.record(
        db,
        "ROLE_UPDATED",
        "role",
        role.id,
        actor=actor.user,
        details={
            "code": role.code,
            "granted_or_changed": added,
            "removed": removed,
            **audit_service.diff(
                {k: before[k] for k in ("name", "is_active")},
                {k: after[k] for k in ("name", "is_active")},
            ),
        },
    )
    db.commit()
    return next(r for r in list_roles(db) if r["id"] == role.id)
