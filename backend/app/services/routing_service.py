"""Approval routing. Levels, bands, limits and approvers come only from approval_rules.

Two policies (setting APPROVAL_ROUTING):
* "band" (default): ONE approver, chosen by amount. A rule covers totals in
  (threshold_amount, approval_limit]; approval_limit NULL = no upper limit.
  e.g. L1 0-50,000 / L2 50,000-1,00,000 / L3 above 1,00,000.
* "chain": every level whose threshold the total exceeds approves in turn (L1 -> L2 -> L3).
* "matrix": every level whose range (threshold_amount, approval_limit] covers the total
  approves in turn. The company matrix is set up this way, e.g.
  PO 0-any / L1 HOD 0-any / L2 CFO 5,000-50,000 / L3 MD 50,000-any / FIN Finance check 0-any:
  4,500 -> PO, HOD, Finance; 45,000 -> PO, HOD, CFO, Finance; 4,50,000 -> PO, HOD, MD, Finance.

Maker is never the checker: if the chosen approver is the submitter, that level is
SKIPPED. In band and chain the expense then goes to the next higher level's approver; in
matrix the next level on the route simply takes it (nothing is added), unless none is left.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.models import ApprovalRule, Expense
from app.schemas.common import money_str


@dataclass
class PlannedStep:
    rule: ApprovalRule
    status: str  # PENDING for the first, WAITING for the rest, SKIPPED for self-approval
    note: str | None = None


def _rules_for(db: Session, expense: Expense) -> list[ApprovalRule]:
    rules = (
        db.execute(
            select(ApprovalRule).where(
                ApprovalRule.department_id == expense.department_id,
                ApprovalRule.is_active.is_(True),
            )
        )
        .unique()
        .scalars()
        .all()
    )
    rules = [r for r in rules if r.level.is_active]
    # An expense-type-specific rule set wins over the department-wide one.
    specific = [r for r in rules if r.expense_type_id == expense.expense_type_id]
    chosen = specific or [r for r in rules if r.expense_type_id is None]
    return sorted(chosen, key=lambda r: r.level.rank)


def _covers(rule: ApprovalRule, total: Decimal) -> bool:
    return total > rule.threshold_amount and (
        rule.approval_limit is None or total <= rule.approval_limit
    )


def _limit_text(rule: ApprovalRule) -> str:
    return "no limit" if rule.approval_limit is None else money_str(rule.approval_limit)


def plan_route(db: Session, expense: Expense) -> list[PlannedStep]:
    """Return the ordered steps, or raise a routing error the submitter can act on."""
    total: Decimal = expense.total_amount
    chosen = _rules_for(db, expense)
    dept = expense.department.name if expense.department else expense.department_id
    if not chosen:
        raise AppError(
            422,
            "NO_APPROVAL_MAPPING",
            f"No approval rule is set up for department '{dept}'. An administrator has been "
            "notified; please try again once it is configured.",
        )
    if settings.APPROVAL_ROUTING == "matrix":
        required = [r for r in chosen if _covers(r, total)]
    elif settings.APPROVAL_ROUTING == "chain":
        required = [r for r in chosen if total > r.threshold_amount]
        top = required[-1] if required else None
        if top is not None and top.approval_limit is not None and total > top.approval_limit:
            raise AppError(
                422,
                "AMOUNT_EXCEEDS_APPROVAL_LIMIT",
                f"Total {money_str(total)} exceeds the highest approval limit "
                f"{_limit_text(top)} for '{dept}'",
            )
    else:
        band = next((r for r in chosen if _covers(r, total)), None)
        if band is None and any(total > r.threshold_amount for r in chosen):
            highest = max(chosen, key=lambda r: r.level.rank)
            raise AppError(
                422,
                "AMOUNT_EXCEEDS_APPROVAL_LIMIT",
                f"Total {money_str(total)} is above every approval band for '{dept}' "
                f"(highest: {highest.level.code} up to {_limit_text(highest)})",
            )
        required = [band] if band else []
    if not required:
        raise AppError(
            422, "NO_APPROVAL_MAPPING", f"No approval level covers an amount of {money_str(total)}"
        )
    return _with_maker_checker(
        chosen, required, expense.owner_id, dept, settings.APPROVAL_ROUTING == "matrix"
    )


def _with_maker_checker(
    chosen: list[ApprovalRule],
    required: list[ApprovalRule],
    owner_id: int,
    dept: object,
    matrix: bool = False,
) -> list[PlannedStep]:
    used: set[int] = {r.id for r in required}
    steps: list[PlannedStep] = []
    for rule in required:
        if not rule.approver.is_active:
            raise AppError(
                422, "APPROVER_INACTIVE", f"The {rule.level.code} approver for '{dept}' is inactive"
            )
        if rule.approver_id != owner_id:
            steps.append(PlannedStep(rule, "WAITING"))
            continue
        # Maker is never the checker: skip this level and escalate to the next higher one.
        steps.append(PlannedStep(rule, "SKIPPED", "Approver is the submitter"))
        later = any(
            r.level.rank > rule.level.rank and r.approver_id != owner_id for r in required
        )
        if matrix and later:
            continue  # the next level on the route takes it; nobody is added
        substitute = next(
            (
                r
                for r in chosen
                if r.level.rank > rule.level.rank
                and r.id not in used
                and r.approver_id != owner_id
                and r.approver.is_active
            ),
            None,
        )
        if substitute is None and not any(
            r.level.rank > rule.level.rank and r.approver_id != owner_id for r in required
        ):
            raise AppError(
                422,
                "NO_INDEPENDENT_APPROVER",
                "No approver other than you is configured at or above "
                f"{rule.level.code} for '{dept}'",
            )
        if substitute is not None:
            used.add(substitute.id)
            steps.append(PlannedStep(substitute, "WAITING", f"Escalated from {rule.level.code}"))

    steps.sort(key=lambda s: s.rule.level.rank)
    active = [s for s in steps if s.status == "WAITING"]
    if not active:
        raise AppError(422, "NO_INDEPENDENT_APPROVER", "No independent approver available")
    active[0].status = "PENDING"
    return steps
