"""Idempotent seeds. Reference data (roles, permissions, levels, bill months, super admin)
is separate from demo data (categories, demo logins, approval rules).

Departments, branches, vendors and employees are FAR's shared masters: seeds NEVER insert
into them. Demo data only *reads* FAR's existing rows to attach logins and rules."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.permissions import PERMISSIONS
from app.core.security import hash_password
from app.models import (
    ApprovalLevel,
    ApprovalRule,
    Branch,
    Department,
    ExpenseType,
    GstRate,
    Permission,
    Role,
    RolePermission,
    User,
)
from app.services import audit_service, config_service

ROLES: dict[str, tuple[str, dict[str, str]]] = {
    "USER": (
        "Employee",
        {
            "expense.create": "OWN",
            "expense.read": "OWN",
            "report.read": "OWN",
            "master.read": "ALL",
        },
    ),
    "APPROVER": (
        "Approver",
        {
            "expense.create": "OWN",
            "expense.read": "ASSIGNED",
            "expense.approve": "ASSIGNED",
            "report.read": "DEPARTMENT",
            "report.export": "DEPARTMENT",
            "master.read": "ALL",
        },
    ),
    "ADMIN": (
        "Administrator",
        {
            "expense.create": "OWN",
            "expense.read": "ALL",
            "expense.approve": "ASSIGNED",
            "expense.reopen": "ALL",
            "report.read": "ALL",
            "report.export": "ALL",
            "report.dump": "ALL",
            "master.read": "ALL",
            "master.manage": "ALL",
            "approval_rule.manage": "ALL",
            "user.manage": "ALL",
            "audit.read": "ALL",
            "payment.schedule": "ALL",
            "payment.record": "ALL",
            "ledger.export": "ALL",
        },
    ),
    # HOD (L1) and CFO (L2): approve bills of every department, so they see the whole
    # company dashboard (spend charts and spend by department), but only open the claims
    # routed to them.
    "HOD_CFO": (
        "HOD / CFO approver",
        {
            "expense.create": "OWN",
            "expense.read": "ASSIGNED",
            "expense.approve": "ASSIGNED",
            "report.read": "ALL",
            "report.export": "ALL",
            "report.department_spend": "ALL",
            "master.read": "ALL",
        },
    ),
    "FA_CHECK": (
        "F&A ratification",
        {
            "expense.create": "OWN",
            "expense.read": "ASSIGNED",
            "expense.approve": "ASSIGNED",
            "report.read": "DEPARTMENT",
            "report.department_spend": "ALL",
            "report.export": "DEPARTMENT",
            "master.read": "ALL",
        },
    ),
    "MD": (
        "MD & CEO - final and payment approver",
        {
            "expense.create": "OWN",
            "expense.read": "ALL",
            "expense.approve": "ASSIGNED",
            "payment.approve": "ALL",
            "report.read": "ALL",
            "report.department_spend": "ALL",
            "report.export": "ALL",
            "master.read": "ALL",
        },
    ),
    "FINANCE": (
        "Finance - Payments",
        {
            "expense.create": "OWN",
            "expense.read": "ALL",
            "expense.approve": "ASSIGNED",
            "payment.schedule": "ALL",
            "payment.record": "ALL",
            "ledger.export": "ALL",
            "vendor_bank.approve": "ALL",
            "report.read": "ALL",
            "report.department_spend": "ALL",
            "report.export": "ALL",
            "master.read": "ALL",
        },
    ),
}
ROLES["SUPER_ADMIN"] = (
    "Super Administrator",
    {
        **ROLES["ADMIN"][1],
        "role.manage": "ALL",
        "report.department_spend": "ALL",
        "vendor_bank.approve": "ALL",
    },
)
SYSTEM_ROLES = {
    "USER",
    "APPROVER",
    "HOD_CFO",
    "ADMIN",
    "FINANCE",
    "SUPER_ADMIN",
    "FA_CHECK",
    "MD",
}
# GST rate options for CGST / SGST / IGST (owner's list). Admins can add/deactivate rates.
GST_RATES = ["3", "5", "7", "9", "12", "15", "18"]

# Approval matrix: Process owner -> L1 HOD -> L2 CFO (mid amounts) or L3 MD (large amounts)
# -> Finance check, then payment. Which levels a bill needs, and the amounts, come from each
# department's approval rules (Approval policies), never from code.
LEVELS = [
    ("PO", "Process owner", 1),
    ("L1", "Head of department (HOD)", 2),
    ("L2", "CFO", 3),
    ("L3", "MD & CEO", 4),
    ("FIN", "Finance check", 5),
]


def _get_or_create(db: Session, model: type, lookup: dict, **values):
    obj = db.execute(select(model).filter_by(**lookup)).unique().scalar_one_or_none()
    if obj is None:
        obj = model(**lookup, **values)
        db.add(obj)
        db.flush()
    return obj


def seed_reference(db: Session) -> None:
    existing_perms = set(db.execute(select(Permission.code)).scalars())
    perms = {}
    for code, (desc, _module, _scopes) in PERMISSIONS.items():
        perm = _get_or_create(db, Permission, {"code": code}, description=desc)
        perm.description = desc
        perms[code] = perm
    new_perms = set(perms) - existing_perms
    for code, (name, grants) in ROLES.items():
        is_new_role = db.execute(select(Role.id).where(Role.code == code)).first() is None
        role = _get_or_create(db, Role, {"code": code}, name=name, is_system=True)
        role.is_system = code in SYSTEM_ROLES
        have = {g.permission.code for g in role.grants}
        for perm_code, scope in grants.items():
            # Never overwrite rights an admin changed on the Roles screen: only grant
            # permissions that are new (or everything for a brand-new role).
            if perm_code not in have and (is_new_role or perm_code in new_perms):
                db.add(
                    RolePermission(role_id=role.id, permission_id=perms[perm_code].id, scope=scope)
                )
    for rate in GST_RATES:
        _get_or_create(db, GstRate, {"rate": Decimal(rate), "applies_to": "ALL"})
    for code, name, rank in LEVELS:
        _get_or_create(db, ApprovalLevel, {"code": code}, name=name, rank=rank)
    config_service.ensure_bill_months(db)
    db.flush()

    if settings.SEED_ADMIN_PASSWORD and settings.SEED_ADMIN_EMAIL:
        username = settings.SEED_ADMIN_USERNAME.lower()
        if db.execute(select(User).where(User.username == username)).first() is None:
            role = db.execute(select(Role).where(Role.code == "SUPER_ADMIN")).scalar_one()
            admin = User(
                employee_code="EMP000",
                username=username,
                full_name="System Administrator",
                email=settings.SEED_ADMIN_EMAIL,
                designation="System Administrator",
                role_id=role.id,
                password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                must_change_password=False,
            )
            db.add(admin)
            db.flush()
            audit_service.record(
                db,
                "USER_SEEDED",
                "user",
                admin.id,
                details={"username": username, "role": "SUPER_ADMIN"},
            )
    db.commit()


EXPENSE_TYPES = [
    ("TRAVEL", "Travel", True),
    ("LODGING", "Lodging", True),
    ("MEALS", "Meals & Entertainment", True),
    ("OFFICE", "Office Supplies", True),
    ("TELECOM", "Telephone & Internet", True),
    ("REPAIRS", "Repairs & Maintenance", True),
    ("PROF", "Professional Fees", True),
    ("RENT", "Rent", True),
    ("MISC", "Miscellaneous", True),
]
# username, name, role, index into FAR's active departments / branches (ordered by id)
DEMO_USERS = [
    ("finadmin", "Demo Finance Admin", "ADMIN", 2, 0),
    ("approver1", "Demo Approver L1", "APPROVER", 0, 0),
    ("approver2", "Demo Approver L2", "APPROVER", 0, 0),
    ("approver3", "Demo Approver L3", "APPROVER", 2, 0),
    ("user1", "Demo Employee One", "USER", 0, 1),
    ("user2", "Demo Employee Two", "USER", 1, 2),
    ("mdpay", "Demo MD", "MD", 2, 0),
]
# Amount bands (band routing): one approver per expense.
RULES = [
    ("L1", "approver1", "0.00", "50000.00"),
    ("L2", "approver2", "50000.00", "100000.00"),
    ("L3", "approver3", "100000.00", None),  # no upper limit
]


def seed_demo(db: Session) -> None:
    """Portal-side demo data on top of FAR's real masters (read-only use of FAR rows)."""
    if not settings.SEED_DEMO_PASSWORD:
        raise SystemExit("Set SEED_DEMO_PASSWORD in backend/.env to seed demo users")
    if settings.is_production:
        raise SystemExit("Refusing to seed demo data when APP_ENV=production")
    depts = list(
        db.execute(select(Department).where(Department.is_active).order_by(Department.id)).scalars()
    )
    branches = list(
        db.execute(select(Branch).where(Branch.is_active).order_by(Branch.id)).unique().scalars()
    )
    if len(depts) < 3 or len(branches) < 3:
        raise SystemExit("FAR needs at least 3 active departments and branches for demo data")

    for code, name, bill in EXPENSE_TYPES:
        _get_or_create(db, ExpenseType, {"code": code}, name=name, requires_bill=bill)

    roles = {r.code: r for r in db.execute(select(Role)).scalars()}
    pw_hash = hash_password(settings.SEED_DEMO_PASSWORD)
    users = {}
    for n, (username, name, role, d_idx, b_idx) in enumerate(DEMO_USERS, start=1):
        users[username] = _get_or_create(
            db,
            User,
            {"username": username},
            employee_code=f"DEMO{n:03d}",
            full_name=name,
            email=f"{username}@example.com",
            role_id=roles[role].id,
            department_id=depts[d_idx].id,
            branch_id=branches[b_idx].id,
            password_hash=pw_hash,
            must_change_password=False,
        )
    admin = (
        db.execute(select(User).where(User.username == settings.SEED_ADMIN_USERNAME.lower()))
        .unique()
        .scalar_one_or_none()
    )
    if admin and admin.department_id is None:
        admin.department_id = depts[0].id
        admin.branch_id = branches[0].id

    levels = {lv.code: lv for lv in db.execute(select(ApprovalLevel)).scalars()}
    for dept in depts:
        for level, approver, threshold, limit in RULES:
            exists = db.execute(
                select(ApprovalRule).where(
                    ApprovalRule.department_id == dept.id,
                    ApprovalRule.level_id == levels[level].id,
                    ApprovalRule.expense_type_id.is_(None),
                    ApprovalRule.is_active.is_(True),
                )
            ).first()
            if not exists:
                db.add(
                    ApprovalRule(
                        department_id=dept.id,
                        level_id=levels[level].id,
                        approver_id=users[approver].id,
                        threshold_amount=Decimal(threshold),
                        approval_limit=Decimal(limit) if limit else None,
                    )
                )
    audit_service.record(
        db, "DEMO_DATA_SEEDED", "system", None, details={"far_departments_used": len(depts)}
    )
    db.commit()
