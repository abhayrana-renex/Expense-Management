"""Role-aware dashboard KPIs and series, aggregated in SQL within the user's data scope."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.models import Branch, Department, Expense, ExpenseType
from app.services.scope import expense_scope_clause

ZERO = Decimal("0.00")


def _money(v: Any) -> str:
    return f"{Decimal(v or 0):.2f}"


def summary(db: Session, principal: Principal, months: int = 6) -> dict[str, Any]:
    scope = expense_scope_clause(
        principal, "report.read" if principal.can("report.read") else "expense.read"
    )
    uid = principal.user.id
    pending = Expense.status.like("PENDING\\_%")

    mine = db.execute(
        select(
            func.count().filter(Expense.status == "DRAFT"),
            func.count().filter(pending),
            func.count().filter(Expense.status == "RETURNED"),
            func.coalesce(func.sum(Expense.total_amount).filter(pending), 0),
            func.coalesce(func.sum(Expense.total_amount).filter(Expense.status == "APPROVED"), 0),
        ).where(Expense.owner_id == uid)
    ).one()

    queue = db.execute(
        select(func.count(), func.coalesce(func.sum(Expense.total_amount), 0)).where(
            Expense.current_approver_id == uid, pending
        )
    ).one()

    month_start = func.date_trunc("month", func.coalesce(Expense.submitted_at, Expense.created_at))
    today = date.today()
    first = date(today.year, today.month, 1)
    y, m = first.year, first.month - (months - 1)
    while m <= 0:
        m += 12
        y -= 1
    since = date(y, m, 1)
    by_month = db.execute(
        select(
            month_start.label("m"),
            func.count(),
            func.coalesce(func.sum(Expense.total_amount), 0),
            func.coalesce(
                func.sum(case((Expense.status == "APPROVED", Expense.total_amount), else_=ZERO)), 0
            ),
        )
        .where(scope, Expense.status.notin_(("DRAFT", "CANCELLED")), month_start >= since)
        .group_by("m")
        .order_by("m")
    ).all()

    by_status = db.execute(
        select(
            case((pending, "PENDING"), else_=Expense.status).label("s"),
            func.count(),
            func.coalesce(func.sum(Expense.total_amount), 0),
        )
        .where(scope)
        .group_by("s")
    ).all()

    def top(dim_col: Any, join_model: Any, join_on: Any) -> list[dict[str, Any]]:
        rows = db.execute(
            select(dim_col, func.count(), func.coalesce(func.sum(Expense.total_amount), 0))
            .join(join_model, join_on)
            .where(scope, Expense.status.notin_(("DRAFT", "CANCELLED")))
            .group_by(dim_col)
            .order_by(func.sum(Expense.total_amount).desc())
            .limit(8)
        ).all()
        return [{"label": r[0], "count": r[1], "amount": _money(r[2])} for r in rows]

    approved = case((Expense.status == "APPROVED", Expense.total_amount), else_=ZERO)
    by_department = db.execute(
        select(
            Department.name,
            func.count(),
            func.coalesce(func.sum(Expense.total_amount), 0),
            func.coalesce(func.sum(approved), 0),
        )
        .join(Department, Department.id == Expense.department_id)
        .where(scope, Expense.status.notin_(("DRAFT", "CANCELLED", "REJECTED")))
        .group_by(Department.name)
        .order_by(func.sum(Expense.total_amount).desc())
    ).all()

    return {
        "role": principal.role,
        "kpis": {
            "my_drafts": mine[0],
            "my_pending_count": mine[1],
            "my_returned": mine[2],
            "my_pending_amount": _money(mine[3]),
            "my_approved_amount": _money(mine[4]),
            "awaiting_my_approval_count": queue[0],
            "awaiting_my_approval_amount": _money(queue[1]),
        },
        "series": {
            "by_month": [
                {
                    "month": r[0].date().isoformat(),
                    "count": r[1],
                    "amount": _money(r[2]),
                    "approved_amount": _money(r[3]),
                }
                for r in by_month
            ],
            "by_status": [
                {"status": r[0], "count": r[1], "amount": _money(r[2])} for r in by_status
            ],
            "by_category": top(
                ExpenseType.name, ExpenseType, ExpenseType.id == Expense.expense_type_id
            ),
            "by_branch": top(Branch.name, Branch, Branch.id == Expense.branch_id),
            # Department-wise spend (submitted and approved), within the viewer's scope.
            "by_department": [
                {
                    "label": r[0],
                    "count": r[1],
                    "amount": _money(r[2]),
                    "approved_amount": _money(r[3]),
                }
                for r in by_department
            ],
        },
        "scope": principal.scope("report.read") or principal.scope("expense.read"),
    }
