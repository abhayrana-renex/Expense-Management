"""In-app notifications and out-of-office delegation. Notifications are written in the same
transaction as the event that causes them, so they can never describe something that didn't
happen."""

from collections.abc import Iterable
from datetime import date, timedelta

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import utcnow
from app.models import Delegation, Expense, Notification, Permission, Role, RolePermission, User
from app.schemas.common import money_str
from app.services import audit_service

MAX_DELEGATION_DAYS = 90


# ---------- notifications ----------


def notify(
    db: Session,
    user_ids: Iterable[int | None],
    kind: str,
    title: str,
    body: str | None = None,
    expense_id: int | None = None,
) -> None:
    for uid in {u for u in user_ids if u}:
        db.add(
            Notification(
                user_id=uid, kind=kind, title=title[:200], body=body, expense_id=expense_id
            )
        )


def holders_of(db: Session, permission: str) -> list[int]:
    """Active users whose role grants `permission` (e.g. finance for payment.record)."""
    return list(
        db.execute(
            select(User.id)
            .join(Role, Role.id == User.role_id)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                Permission.code == permission, User.is_active.is_(True), Role.is_active.is_(True)
            )
        ).scalars()
    )


def tag(e: Expense) -> str:
    return f"{e.expense_no} · ₹{money_str(e.total_amount)}"


def notify_pending(db: Session, e: Expense) -> None:
    """Tell the current approver (and anyone covering for them) that it's their turn."""
    if not e.current_approver_id:
        return
    ids = [e.current_approver_id, *delegates_of(db, e.current_approver_id)]
    notify(
        db,
        ids,
        "APPROVAL_NEEDED",
        f"Approval needed: {e.expense_no}",
        f"{e.owner.full_name} · {tag(e)}",
        e.id,
    )


def list_query(user_id: int, unread_only: bool) -> Select:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return stmt


def unread_count(db: Session, user_id: int) -> int:
    return db.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    ).scalar_one()


def mark_read(db: Session, user_id: int, notification_id: int | None) -> int:
    stmt = update(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    n = db.execute(stmt.values(read_at=utcnow())).rowcount
    db.commit()
    return n


# ---------- delegation ----------


def _active(today: date | None = None):
    today = today or date.today()
    return (
        Delegation.revoked_at.is_(None),
        Delegation.starts_on <= today,
        Delegation.ends_on >= today,
    )


def delegators_of(db: Session, delegate_id: int) -> set[int]:
    """Approvers this user is covering for today."""
    return set(
        db.execute(
            select(Delegation.approver_id).where(Delegation.delegate_id == delegate_id, *_active())
        ).scalars()
    )


def delegates_of(db: Session, approver_id: int) -> set[int]:
    return set(
        db.execute(
            select(Delegation.delegate_id).where(Delegation.approver_id == approver_id, *_active())
        ).scalars()
    )


def list_delegations(db: Session, principal: Principal) -> list[Delegation]:
    stmt = select(Delegation).where(
        Delegation.revoked_at.is_(None), Delegation.ends_on >= date.today()
    )
    if not principal.can("user.manage"):
        stmt = stmt.where(
            (Delegation.approver_id == principal.user.id)
            | (Delegation.delegate_id == principal.user.id)
        )
    return list(db.execute(stmt.order_by(Delegation.starts_on)).unique().scalars())


def create_delegation(
    db: Session,
    principal: Principal,
    delegate_id: int,
    starts_on: date,
    ends_on: date,
    reason: str | None,
    approver_id: int | None,
) -> Delegation:
    approver_id = approver_id or principal.user.id
    if approver_id != principal.user.id and not principal.can("user.manage"):
        raise AppError(403, "FORBIDDEN", "You can only delegate your own approvals")
    if ends_on < starts_on or ends_on < date.today():
        raise AppError(422, "VALIDATION_ERROR", "End date must be today or later, after the start")
    if (ends_on - starts_on) > timedelta(days=MAX_DELEGATION_DAYS):
        raise AppError(
            422, "VALIDATION_ERROR", f"A delegation can last at most {MAX_DELEGATION_DAYS} days"
        )
    if delegate_id == approver_id:
        raise AppError(422, "VALIDATION_ERROR", "Choose someone other than the approver")
    delegate = db.get(User, delegate_id)
    perms = {g.permission.code for g in delegate.role.grants} if delegate else set()
    if delegate is None or not delegate.is_active or "expense.approve" not in perms:
        raise AppError(422, "VALIDATION_ERROR", "The delegate must be an active approver")
    overlap = db.execute(
        select(Delegation.id).where(
            Delegation.approver_id == approver_id,
            Delegation.revoked_at.is_(None),
            Delegation.starts_on <= ends_on,
            Delegation.ends_on >= starts_on,
        )
    ).first()
    if overlap:
        raise AppError(409, "DUPLICATE", "There is already a delegation for those dates")
    d = Delegation(
        approver_id=approver_id,
        delegate_id=delegate_id,
        starts_on=starts_on,
        ends_on=ends_on,
        reason=reason,
        created_by_id=principal.user.id,
    )
    db.add(d)
    db.flush()
    approver = db.get(User, approver_id)
    notify(
        db,
        [delegate_id],
        "DELEGATION",
        f"You'll approve for {approver.full_name}",
        f"{starts_on:%d %b} - {ends_on:%d %b %Y}" + (f" · {reason}" if reason else ""),
    )
    audit_service.record(
        db,
        "DELEGATION_CREATED",
        "delegation",
        d.id,
        actor=principal.user,
        details={
            "approver_id": approver_id,
            "delegate_id": delegate_id,
            "from": starts_on.isoformat(),
            "to": ends_on.isoformat(),
        },
    )
    db.commit()
    return db.get(Delegation, d.id)


def revoke_delegation(db: Session, principal: Principal, delegation_id: int) -> None:
    d = db.get(Delegation, delegation_id)
    if d is None or d.revoked_at is not None:
        raise not_found("Delegation")
    if principal.user.id not in (d.approver_id, d.created_by_id) and not principal.can(
        "user.manage"
    ):
        raise AppError(403, "FORBIDDEN", "Only the approver or an admin can end a delegation")
    d.revoked_at = utcnow()
    audit_service.record(db, "DELEGATION_REVOKED", "delegation", d.id, actor=principal.user)
    db.commit()
