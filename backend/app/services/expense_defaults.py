"""What a new expense starts with: department, branch and cost centre.

DEFAULT_DEPARTMENT / DEFAULT_BRANCH (FAR names, any case) apply to everyone; when unset, or
when FAR no longer has that name Active, the person's own department / branch is used. The
cost centre is the Active one linked to that department. All of it is only pre-filled: the
person can change every value, and the server validates what is submitted as usual.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Branch, CostCentre, Department, User


def _active_by_name(db: Session, model: type, name: str) -> int | None:
    if not name.strip():
        return None
    return db.execute(
        select(model.id)
        .where(func.upper(func.btrim(model.name)) == name.strip().upper(), model.is_active)
        .order_by(model.id)
        .limit(1)
    ).scalar()


def cost_centre_for(db: Session, department_id: int | None) -> int | None:
    if department_id is None:
        return None
    return db.execute(
        select(CostCentre.id)
        .where(CostCentre.department_id == department_id, CostCentre.is_active.is_(True))
        .order_by(CostCentre.code)
        .limit(1)
    ).scalar()


def for_user(db: Session, user: User) -> dict[str, int | None]:
    department_id = (
        _active_by_name(db, Department, settings.DEFAULT_DEPARTMENT) or user.effective_department_id
    )
    branch_id = _active_by_name(db, Branch, settings.DEFAULT_BRANCH) or user.branch_id
    return {
        "department_id": department_id,
        "branch_id": branch_id,
        "cost_centre_id": cost_centre_for(db, department_id),
    }
