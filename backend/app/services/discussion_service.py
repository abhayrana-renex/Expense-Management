"""Discussion on an expense: questions and replies between the people in its process.

Who may read and post: the initiator, every approver on the expense's route (any round;
skipped levels are not part of it) and anyone acting for one of them under a delegation.
The audit team (audit.read) may read, never post. Everyone else gets 404, even admins who can
see the expense itself. Messages are append-only (DB trigger); mentions may only name people
in the process, and each mention is notified."""

from pathlib import PurePath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.core.security import sha256_hex
from app.models import ApprovalStep, Expense, User
from app.models.discussion import ExpenseMessage, ExpenseMessageFile
from app.services import audit_service, engagement_service
from app.services.document_service import ALLOWED, validate_bill
from app.services.storage import get_storage

MAX_FILES = 5
NOT_IN_PROCESS = ("SKIPPED",)


def participants(db: Session, expense: Expense) -> list[User]:
    """The initiator first, then the approvers in route order (each person once)."""
    steps = db.execute(
        select(ApprovalStep)
        .where(ApprovalStep.expense_id == expense.id, ApprovalStep.status.notin_(NOT_IN_PROCESS))
        .order_by(ApprovalStep.cycle, ApprovalStep.sequence)
    ).unique().scalars()
    people: dict[int, User] = {expense.owner_id: expense.owner}
    for s in steps:
        people.setdefault(s.approver_id, s.approver)
    return list(people.values())


def _access(db: Session, principal: Principal, expense_id: int) -> tuple[Expense, list[User], bool]:
    """(expense, participants, may_post). 404 for anyone outside the process."""
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise not_found("Expense")
    people = participants(db, expense)
    ids = {u.id for u in people}
    uid = principal.user.id
    if uid in ids or ids & principal.acting_for:
        return expense, people, True
    if principal.can("audit.read"):
        return expense, people, False
    raise not_found("Expense")


def _role(expense: Expense, user: User, steps: dict[int, str]) -> str:
    return "Initiator" if user.id == expense.owner_id else steps.get(user.id, "Approver")


def _levels(db: Session, expense: Expense) -> dict[int, str]:
    rows = db.execute(
        select(ApprovalStep)
        .where(ApprovalStep.expense_id == expense.id, ApprovalStep.status.notin_(NOT_IN_PROCESS))
        .order_by(ApprovalStep.cycle, ApprovalStep.sequence)
    ).unique().scalars()
    out: dict[int, str] = {}
    for s in rows:
        out.setdefault(s.approver_id, f"{s.level.code} · {s.level.name}")
    return out


def _person(u: User, role: str) -> dict:
    return {"id": u.id, "full_name": u.full_name, "role": role}


def serialize(expense: Expense, m: ExpenseMessage, levels: dict[int, str]) -> dict:
    return {
        "id": m.id,
        "author": _person(m.author, _role(expense, m.author, levels)),
        "body": m.body,
        "created_at": m.created_at,
        "mentions": [_person(u, _role(expense, u, levels)) for u in m.mentions],
        "files": [
            {
                "id": f.id,
                "name": f.original_filename,
                "content_type": f.content_type,
                "size_bytes": f.size_bytes,
            }
            for f in m.files
        ],
    }


def thread(db: Session, principal: Principal, expense_id: int) -> dict:
    expense, people, may_post = _access(db, principal, expense_id)
    levels = _levels(db, expense)
    messages = db.execute(
        select(ExpenseMessage)
        .where(ExpenseMessage.expense_id == expense.id)
        .order_by(ExpenseMessage.id)
    ).unique().scalars()
    return {
        "can_post": may_post,
        "participants": [_person(u, _role(expense, u, levels)) for u in people],
        "messages": [serialize(expense, m, levels) for m in messages],
    }


def post(
    db: Session,
    principal: Principal,
    expense_id: int,
    body: str,
    mention_ids: list[int],
    files: list[tuple[str, bytes]],
) -> dict:
    expense, people, may_post = _access(db, principal, expense_id)
    if not may_post:
        raise AppError(403, "FORBIDDEN", "Only people in this expense's process can post")
    text = body.strip()
    if not text and not files:
        raise AppError(422, "VALIDATION_ERROR", "Write a message or attach a file")
    if len(text) > 4000:
        raise AppError(422, "VALIDATION_ERROR", "Keep the message under 4000 characters")
    if len(files) > MAX_FILES:
        raise AppError(422, "VALIDATION_ERROR", f"Attach at most {MAX_FILES} files at a time")
    by_id = {u.id: u for u in people}
    unknown = [i for i in dict.fromkeys(mention_ids) if i not in by_id]
    if unknown:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            "You can only mention people in this expense's process",
            [{"field": "mentions", "ids": unknown}],
        )
    checked = [(name, data, validate_bill(name, data)) for name, data in files]

    msg = ExpenseMessage(expense_id=expense.id, author_id=principal.user.id, body=text)
    msg.mentions = [by_id[i] for i in dict.fromkeys(mention_ids)]
    db.add(msg)
    db.flush()
    storage = get_storage()
    for name, data, content_type in checked:
        db.add(
            ExpenseMessageFile(
                message_id=msg.id,
                original_filename=PurePath(name).name[:255],
                storage_key=storage.save(data, ALLOWED[content_type][0]),
                content_type=content_type,
                size_bytes=len(data),
                sha256=sha256_hex(data),
            )
        )
    db.flush()
    db.refresh(msg)
    audit_service.record(
        db,
        "EXPENSE_MESSAGE_POSTED",
        "expense",
        expense.id,
        actor=principal.user,
        details={
            "message_id": msg.id,
            "mentions": [u.id for u in msg.mentions],
            "files": [{"name": f.original_filename, "sha256": f.sha256} for f in msg.files],
        },
    )
    _notify(db, principal, expense, msg)
    levels = _levels(db, expense)
    out = serialize(expense, msg, levels)
    db.commit()
    return out


def _notify(db: Session, principal: Principal, expense: Expense, msg: ExpenseMessage) -> None:
    me = principal.user.id
    preview = (msg.body or "(attachment)")[:300]
    mentioned = [u.id for u in msg.mentions if u.id != me]
    engagement_service.notify(
        db,
        mentioned,
        "MENTION",
        f"{principal.user.full_name} asked you about {expense.expense_no}",
        preview,
        expense.id,
    )
    # No one named: the initiator and whoever holds it now still hear about it.
    if not msg.mentions:
        engagement_service.notify(
            db,
            [u for u in (expense.owner_id, expense.current_approver_id) if u != me],
            "MESSAGE",
            f"New message on {expense.expense_no} from {principal.user.full_name}",
            preview,
            expense.id,
        )


def file_for_download(
    db: Session, principal: Principal, expense_id: int, file_id: int
) -> ExpenseMessageFile:
    _access(db, principal, expense_id)
    f = db.get(ExpenseMessageFile, file_id)
    msg = db.get(ExpenseMessage, f.message_id) if f else None
    if f is None or msg is None or msg.expense_id != expense_id:
        raise not_found("File")
    if not get_storage().exists(f.storage_key):
        raise AppError(404, "NOT_FOUND", "Stored file is missing; contact an administrator")
    audit_service.record(
        db,
        "MESSAGE_FILE_DOWNLOADED",
        "expense",
        expense_id,
        actor=principal.user,
        details={"file_id": f.id},
    )
    db.commit()
    return f
