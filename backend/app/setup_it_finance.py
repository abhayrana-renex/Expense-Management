"""Company approval chain and payment roles with real FAR employees.

    python -m app.setup_it_finance          (idempotent; every change is audited)

Who is who lives in backend/org_setup.local.json (gitignored - real names never go into the
repository). Copy backend/org_setup.example.json to start; ORG_SETUP_FILE overrides the path.

Approval (AP process flow, APPROVAL_ROUTING=chain), the same for every active department:
  Initiator -> L1 Process owner -> L2 HOD -> L3 F&A ratification (corrects the bill)
  -> MD final approval only above md_above.
Payment: Finance schedules -> payment approver (MD) approves -> bank file -> UTR ->
  general ledger. Every stage can push back one step; nothing is cancelled.

Logins are linked to FAR employees (core.assignees) by employee code, so name, e-mail and
department always come from FAR. Claims already in flight keep their route, except that
steps still waiting for a replaced approver ("replaced": {old: new}) move to the new one.
"""

import json
import os
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models import (
    ApprovalLevel,
    ApprovalRule,
    ApprovalStep,
    Assignee,
    Branch,
    Department,
    Expense,
    User,
)
from app.seed import LEVELS
from app.services import audit_service

BACKEND = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("ORG_SETUP_FILE") or BACKEND / "org_setup.local.json")


def _load_config(path: Path = CONFIG) -> tuple[list, tuple, dict]:
    """(PEOPLE [(username, employee code, role)], CHAIN ((level, username, above)), REPLACED)."""
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Copy backend/org_setup.example.json to "
            "backend/org_setup.local.json and fill in your FAR employee codes."
        )
    cfg = json.loads(path.read_text(encoding="utf-8"))
    people = [(p["username"], str(p["employee_code"]), p["role"]) for p in cfg["people"]]
    chain = tuple((c["level"], c["username"], str(Decimal(c["above"]))) for c in cfg["chain"])
    names = {p[0] for p in people}
    missing = [c[1] for c in chain if c[1] not in names]
    if missing:
        raise SystemExit(f"Chain names people not in 'people': {missing}")
    return people, chain, dict(cfg.get("replaced") or {})


PEOPLE: list = []
CHAIN: tuple = ()
REPLACED: dict = {}


def _audit(db: Session, actor: User | None, action: str, entity: str, eid, details: dict):
    audit_service.record(db, action, entity, eid, actor=actor, details=details)


def _levels(db: Session, actor: User | None) -> dict[str, ApprovalLevel]:
    levels = {lv.code: lv for lv in db.execute(select(ApprovalLevel)).scalars()}
    for code, name, rank in LEVELS:
        lv = levels.get(code)
        if lv is None:
            lv = ApprovalLevel(code=code, name=name, rank=rank)
            db.add(lv)
            db.flush()
            levels[code] = lv
            _audit(db, actor, "APPROVAL_LEVEL_CREATED", "approval_level", lv.id, {"code": code})
        elif lv.name != name or not lv.is_active:
            _audit(
                db,
                actor,
                "APPROVAL_LEVEL_UPDATED",
                "approval_level",
                lv.id,
                {"name": [lv.name, name]},
            )
            lv.name, lv.is_active = name, True
    return levels


def _users(db: Session, actor: User | None, branch: Branch) -> dict[str, User]:
    from app.models import Role

    roles = {r.code: r for r in db.execute(select(Role)).scalars()}
    out: dict[str, User] = {}
    for username, emp_code, role_code in PEOPLE:
        emp = db.execute(
            select(Assignee).where(Assignee.employee_code == emp_code)
        ).scalar_one_or_none()
        if emp is None or not emp.is_active:
            raise SystemExit(f"FAR employee {emp_code} ({username}) not found or inactive")
        user = (
            db.execute(select(User).where(User.username == username)).unique().scalar_one_or_none()
        )
        created = user is None
        if created:
            user = User(
                username=username,
                password_hash=hash_password(settings.SEED_DEMO_PASSWORD),
                must_change_password=False,
                branch_id=branch.id,
            )
            db.add(user)
        before = None if created else user.role_id
        user.employee_code = emp.employee_code
        user.full_name = emp.name
        user.email = emp.email or f"{username}@example.invalid"
        user.assignee_id = emp.id
        user.department_id = emp.department_id
        user.role_id = roles[role_code].id
        user.is_active = True
        db.flush()
        if created or before != user.role_id:
            _audit(
                db,
                actor,
                "USER_CREATED" if created else "USER_ROLE_CHANGED",
                "user",
                user.id,
                {"username": username, "far_employee": emp_code, "role": role_code},
            )
        out[username] = user
    return out


def _rules(db: Session, actor: User | None, users: dict[str, User], levels) -> int:
    changed = 0
    for dept in db.execute(select(Department).where(Department.is_active)).scalars():
        want = {code: (users[who], Decimal(above)) for code, who, above in CHAIN}
        current = list(
            db.execute(
                select(ApprovalRule).where(
                    ApprovalRule.department_id == dept.id,
                    ApprovalRule.expense_type_id.is_(None),
                    ApprovalRule.is_active.is_(True),
                )
            )
            .unique()
            .scalars()
        )
        for rule in current:
            target = want.get(rule.level.code)
            if (
                target
                and rule.approver_id == target[0].id
                and rule.threshold_amount == target[1]
                and rule.approval_limit is None
            ):
                want.pop(rule.level.code)
                continue
            rule.is_active = False
            changed += 1
            _audit(
                db,
                actor,
                "APPROVAL_RULE_DEACTIVATED",
                "approval_rule",
                rule.id,
                {"department": dept.name, "level": rule.level.code, "reason": "company chain"},
            )
        db.flush()
        for code, (approver, threshold) in want.items():
            rule = ApprovalRule(
                department_id=dept.id,
                level_id=levels[code].id,
                approver_id=approver.id,
                threshold_amount=threshold,
                approval_limit=None,
            )
            db.add(rule)
            db.flush()
            changed += 1
            _audit(
                db,
                actor,
                "APPROVAL_RULE_CREATED",
                "approval_rule",
                rule.id,
                {
                    "department": dept.name,
                    "level": code,
                    "approver": approver.username,
                    "above": str(threshold),
                },
            )
    return changed


def _reassign_in_flight(db: Session, actor: User | None, users: dict[str, User]) -> int:
    moved = 0
    for old, new in REPLACED.items():
        old_u = db.execute(select(User).where(User.username == old)).unique().scalar_one_or_none()
        if old_u is None:
            continue
        steps = (
            db.execute(
                select(ApprovalStep, Expense)
                .join(
                    Expense,
                    (Expense.id == ApprovalStep.expense_id) & (Expense.cycle == ApprovalStep.cycle),
                )
                .where(
                    ApprovalStep.approver_id == old_u.id,
                    ApprovalStep.status.in_(("WAITING", "PENDING")),
                )
            )
            .unique()
            .all()
        )
        for step, exp in steps:
            if exp.owner_id == users[new].id:
                continue  # never route a claim to its own maker
            step.approver_id = users[new].id
            if step.status == "PENDING":
                exp.current_approver_id = users[new].id
            moved += 1
            _audit(
                db,
                actor,
                "APPROVAL_STEP_REASSIGNED",
                "expense",
                exp.id,
                {
                    "expense_no": exp.expense_no,
                    "level": step.level.code,
                    "from": old,
                    "to": new,
                    "reason": "approver replaced",
                },
            )
    return moved


def main() -> None:
    global PEOPLE, CHAIN, REPLACED
    PEOPLE, CHAIN, REPLACED = _load_config()
    if settings.is_production:
        raise SystemExit("Refusing to run the set-up when APP_ENV=production")
    if not settings.SEED_DEMO_PASSWORD:
        raise SystemExit("Set SEED_DEMO_PASSWORD (first password for new logins)")
    with SessionLocal() as db:
        actor = (
            db.execute(select(User).where(User.username == settings.SEED_ADMIN_USERNAME.lower()))
            .unique()
            .scalar_one_or_none()
        )
        branch = (
            actor.branch
            if actor and actor.branch
            else db.execute(select(Branch).where(Branch.is_active).order_by(Branch.id))
            .unique()
            .scalars()
            .first()
        )
        levels = _levels(db, actor)
        users = _users(db, actor, branch)
        rules = _rules(db, actor, users, levels)
        moved = _reassign_in_flight(db, actor, users)
        db.commit()
    print(
        f"Chain set for every department ({rules} rule changes); {moved} in-flight step(s) "
        "moved to their replacement."
    )
    if settings.APPROVAL_ROUTING != "chain":
        print("NOTE: set APPROVAL_ROUTING=chain in backend/.env and restart the API.")


if __name__ == "__main__":
    main()
