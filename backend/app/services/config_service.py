"""Expense-specific local masters: bill months (rolling FY), approval levels and rules."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.models import (
    ApprovalLevel,
    ApprovalRule,
    BillMonth,
    Department,
    ExpenseType,
    GstRate,
    User,
)
from app.schemas.masters import ApprovalLevelIn, ApprovalRuleIn, ApprovalRuleUpdate
from app.services import audit_service

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def financial_year(d: date) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def ensure_bill_months(db: Session, today: date | None = None, actor: User | None = None) -> int:
    """Make sure every month of the previous, current and next FY exists. Idempotent."""
    today = today or date.today()
    fy_start = today.year if today.month >= 4 else today.year - 1
    existing = set(db.execute(select(BillMonth.code)).scalars())
    created = []
    for year in range(fy_start - 1, fy_start + 2):
        for offset in range(12):
            month = (3 + offset) % 12 + 1
            y = year if month >= 4 else year + 1
            code = f"{y}-{month:02d}"
            if code in existing:
                continue
            start = date(y, month, 1)
            db.add(
                BillMonth(
                    code=code,
                    label=f"{MONTHS[month - 1]} {y}",
                    month_start=start,
                    financial_year=financial_year(start),
                    is_open=True,
                    is_active=True,
                )
            )
            created.append(code)
    if created:
        audit_service.record(
            db,
            "BILL_MONTHS_GENERATED",
            "bill_months",
            None,
            actor=actor,
            details={"created": created},
        )
    return len(created)


def set_bill_month_open(db: Session, actor: Principal, month_id: int, is_open: bool) -> BillMonth:
    month = db.get(BillMonth, month_id)
    if month is None:
        raise not_found("Bill month")
    month.is_open = is_open
    audit_service.record(
        db,
        "BILL_MONTH_OPENED" if is_open else "BILL_MONTH_CLOSED",
        "bill_months",
        month.id,
        actor=actor.user,
        details={"code": month.code},
    )
    db.commit()
    return month


def create_level(db: Session, actor: Principal, data: ApprovalLevelIn) -> ApprovalLevel:
    level = ApprovalLevel(code=data.code.upper(), name=data.name, rank=data.rank)
    db.add(level)
    db.flush()
    audit_service.record(
        db,
        "APPROVAL_LEVEL_CREATED",
        "approval_levels",
        level.id,
        actor=actor.user,
        details=data.model_dump(),
    )
    db.commit()
    return level


def rules_query(department_id: int | None, include_inactive: bool) -> Select:
    stmt = select(ApprovalRule)
    if department_id:
        stmt = stmt.where(ApprovalRule.department_id == department_id)
    if not include_inactive:
        stmt = stmt.where(ApprovalRule.is_active.is_(True))
    return stmt


def _check_rule(
    db: Session,
    department_id: int,
    expense_type_id: int | None,
    level_id: int,
    approver_id: int,
    threshold,
    limit,
) -> None:
    if db.get(Department, department_id) is None:
        raise AppError(422, "VALIDATION_ERROR", "Department does not exist")
    if expense_type_id is not None and db.get(ExpenseType, expense_type_id) is None:
        raise AppError(422, "VALIDATION_ERROR", "Expense type does not exist")
    if db.get(ApprovalLevel, level_id) is None:
        raise AppError(422, "VALIDATION_ERROR", "Approval level does not exist")
    approver = db.get(User, approver_id)
    if approver is None or not approver.is_active:
        raise AppError(422, "VALIDATION_ERROR", "Approver does not exist or is inactive")
    if limit is not None and limit <= threshold:
        raise AppError(422, "VALIDATION_ERROR", "approval_limit must be greater than threshold")


def _rule_details(rule: ApprovalRule) -> dict:
    return {
        "department_id": rule.department_id,
        "expense_type_id": rule.expense_type_id,
        "level_id": rule.level_id,
        "approver_id": rule.approver_id,
        "threshold_amount": str(rule.threshold_amount),
        "approval_limit": None if rule.approval_limit is None else str(rule.approval_limit),
        "is_active": rule.is_active,
    }


def get_rule(db: Session, rule_id: int) -> ApprovalRule:
    rule = db.get(ApprovalRule, rule_id)
    if rule is None:
        raise not_found("Approval rule")
    return rule


def create_rule(db: Session, actor: Principal, data: ApprovalRuleIn) -> ApprovalRule:
    _check_rule(
        db,
        data.department_id,
        data.expense_type_id,
        data.level_id,
        data.approver_id,
        data.threshold_amount,
        data.approval_limit,
    )
    rule = ApprovalRule(**data.model_dump())
    db.add(rule)
    db.flush()
    audit_service.record(
        db,
        "APPROVAL_RULE_CREATED",
        "approval_rules",
        rule.id,
        actor=actor.user,
        details=_rule_details(rule),
    )
    db.commit()
    return get_rule(db, rule.id)


def update_rule(
    db: Session, actor: Principal, rule_id: int, data: ApprovalRuleUpdate
) -> ApprovalRule:
    rule = get_rule(db, rule_id)
    before = _rule_details(rule)
    changes = data.model_dump(exclude_unset=True)
    no_limit = changes.pop("no_limit", False)
    for key, value in changes.items():
        if value is not None:
            setattr(rule, key, value)
    if no_limit:
        rule.approval_limit = None
    _check_rule(
        db,
        rule.department_id,
        rule.expense_type_id,
        rule.level_id,
        rule.approver_id,
        rule.threshold_amount,
        rule.approval_limit,
    )
    db.flush()
    audit_service.record(
        db,
        "APPROVAL_RULE_UPDATED",
        "approval_rules",
        rule.id,
        actor=actor.user,
        details=audit_service.diff(before, _rule_details(rule)),
    )
    db.commit()
    return get_rule(db, rule.id)


def deactivate_rule(db: Session, actor: Principal, rule_id: int) -> ApprovalRule:
    rule = get_rule(db, rule_id)
    rule.is_active = False
    audit_service.record(
        db,
        "APPROVAL_RULE_DEACTIVATED",
        "approval_rules",
        rule.id,
        actor=actor.user,
        details=_rule_details(rule),
    )
    db.commit()
    return get_rule(db, rule.id)


def create_gst_rate(db: Session, actor: Principal, rate: Decimal, applies_to: str) -> GstRate:
    exists = db.execute(
        select(GstRate).where(GstRate.rate == rate, GstRate.applies_to == applies_to)
    ).scalar()
    if exists:
        raise AppError(409, "DUPLICATE", f"{rate}% for {applies_to} already exists")
    obj = GstRate(rate=rate, applies_to=applies_to, is_active=True)
    db.add(obj)
    db.flush()
    audit_service.record(
        db,
        "GST_RATE_CREATED",
        "gst_rates",
        obj.id,
        actor=actor.user,
        details={"rate": str(rate), "applies_to": applies_to},
    )
    db.commit()
    return obj


def set_gst_rate_active(db: Session, actor: Principal, rate_id: int, active: bool) -> GstRate:
    obj = db.get(GstRate, rate_id)
    if obj is None:
        raise not_found("GST rate")
    obj.is_active = active
    audit_service.record(
        db,
        "GST_RATE_ACTIVATED" if active else "GST_RATE_DEACTIVATED",
        "gst_rates",
        obj.id,
        actor=actor.user,
        details={"rate": str(obj.rate)},
    )
    db.commit()
    return obj
