"""Data-scope filters. Every expense query goes through these."""

from sqlalchemy import ColumnElement, and_, exists, false, or_, true

from app.core.deps import Principal
from app.models import ApprovalStep, Expense

# Steps that mean "this approver has been asked or has acted".
ENGAGED_STEP_STATUSES = ("PENDING", "APPROVED", "REJECTED", "RETURNED")


def assigned_clause(user_id: int) -> ColumnElement[bool]:
    return exists().where(
        and_(
            ApprovalStep.expense_id == Expense.id,
            ApprovalStep.approver_id == user_id,
            ApprovalStep.status.in_(ENGAGED_STEP_STATUSES),
        )
    )


def expense_scope_clause(principal: Principal, permission: str) -> ColumnElement[bool]:
    scope = principal.scope(permission)
    uid = principal.user.id
    own = Expense.owner_id == uid
    if scope == "ALL":
        return true()
    if scope == "OWN":
        return own
    covering = (
        Expense.current_approver_id.in_(principal.acting_for) if principal.acting_for else false()
    )
    if scope == "ASSIGNED":
        return or_(own, assigned_clause(uid), covering)
    if scope == "DEPARTMENT":
        dept = principal.user.effective_department_id
        in_dept = Expense.department_id == dept if dept is not None else false()
        return or_(own, assigned_clause(uid), in_dept, covering)
    return false()
