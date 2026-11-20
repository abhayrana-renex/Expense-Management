"""Contract register: store a contract / MSA the first time it is claimed, then know for every
later claim which periods are paid, which are due and what the next payment is.

- A contract is visible to its department, to whoever registered it, and to users who can
  read every expense. Anything else is "not found" (no existence leak).
- The schedule is worked out from the start / end dates and the payment frequency; a period
  is "claimed" when a live claim (not cancelled / rejected) linked to the contract covers it.
- Saving a CONTRACT claim links it to the register entry (by id, or by the contract number),
  creating the entry the first time. The claim keeps a snapshot of the contract details.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError, not_found
from app.models import Contract, Expense, ExpenseDocument
from app.models.contract import FREQUENCIES, MONTHS_PER
from app.services import audit_service

LIVE = ("DRAFT", "SUBMITTED", "APPROVED", "RETURNED")  # plus PENDING_Ln
DEAD = ("CANCELLED", "REJECTED")


# ---------- visibility ----------


def _visible_clause(principal: Principal):
    if principal.scope("expense.read") == "ALL":
        return Contract.id.is_not(None)
    dept = principal.user.effective_department_id
    return or_(
        Contract.created_by_id == principal.user.id,
        Contract.department_id == dept if dept is not None else Contract.id.is_(None),
    )


def get_visible(db: Session, principal: Principal, contract_id: int) -> Contract:
    c = db.execute(
        select(Contract).where(Contract.id == contract_id, _visible_clause(principal))
    ).scalar_one_or_none()
    if c is None:
        raise not_found("Contract")
    return c


def search(db: Session, principal: Principal, q: str | None, limit: int = 20) -> list[Contract]:
    stmt = select(Contract).where(Contract.is_active.is_(True), _visible_clause(principal))
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Contract.contract_no.ilike(like),
                Contract.party_name.ilike(like),
                Contract.title.ilike(like),
            )
        )
    return list(db.execute(stmt.order_by(Contract.end_date.desc()).limit(limit)).scalars())


def by_number(db: Session, number: str) -> Contract | None:
    return db.execute(
        select(Contract).where(
            func.lower(func.btrim(Contract.contract_no)) == number.strip().lower()
        )
    ).scalar_one_or_none()


# ---------- schedule ----------


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


@dataclass
class Period:
    start: date
    end: date

    def label(self, frequency: str) -> str:
        a, b = self.start.strftime("%b %Y"), self.end.strftime("%b %Y")
        return a if frequency == "MONTHLY" or a == b else f"{a} – {b}"


def periods(c: Contract) -> list[Period]:
    if c.frequency == "ONE_TIME" or c.frequency not in MONTHS_PER:
        return [Period(c.start_date, c.end_date)]
    step = MONTHS_PER[c.frequency]
    out, start, i = [], c.start_date, 0
    while start <= c.end_date and i < 240:  # 20 years of months: a hard stop
        nxt = _add_months(c.start_date, step * (i + 1))
        out.append(Period(start, min(nxt - timedelta(days=1), c.end_date)))
        start, i = nxt, i + 1
    return out


def _claims(db: Session, contract_id: int, exclude_expense_id: int | None = None) -> list[Expense]:
    stmt = select(Expense).where(Expense.contract_id == contract_id, Expense.status.notin_(DEAD))
    if exclude_expense_id:
        stmt = stmt.where(Expense.id != exclude_expense_id)
    return list(db.execute(stmt).unique().scalars())


def _claim_span(e: Expense) -> tuple[date, date] | None:
    if e.bill_period_start and e.bill_period_end:
        return e.bill_period_start, e.bill_period_end
    dates = [i.bill_date or i.expense_date for i in e.items if i.bill_date or i.expense_date]
    return (min(dates), max(dates)) if dates else None


def schedule(db: Session, c: Contract, today: date | None = None) -> dict:
    today = today or date.today()
    claims = [(e, _claim_span(e)) for e in _claims(db, c.id)]
    rows, next_due, current = [], None, None
    for p in periods(c):
        hit = next(
            (e for e, span in claims if span and span[0] <= p.end and span[1] >= p.start), None
        )
        if hit:
            status = "PAID" if hit.payment_status == "PAID" else "CLAIMED"
        elif p.start > today:
            status = "UPCOMING"
        else:
            status = "OVERDUE" if p.end < today else "DUE"
        row = {
            "start": p.start,
            "end": p.end,
            "label": p.label(c.frequency),
            "status": status,
            "expense_id": hit.id if hit else None,
            "expense_no": hit.expense_no if hit else None,
            "expense_status": hit.status if hit else None,
        }
        rows.append(row)
        if p.start <= today <= p.end:
            current = row
        if next_due is None and status in ("DUE", "OVERDUE"):
            next_due = row
    if next_due is None:
        next_due = next((r for r in rows if r["status"] == "UPCOMING"), None)
    return {
        "periods": rows,
        "current": current,
        "next_due": next_due,
        "claimed": sum(r["status"] in ("CLAIMED", "PAID") for r in rows),
        "due": sum(r["status"] in ("DUE", "OVERDUE") for r in rows),
        "ended": c.end_date < today,
    }


def serialize(db: Session, c: Contract, with_schedule: bool = True) -> dict:
    out = {
        "id": c.id,
        "contract_no": c.contract_no,
        "title": c.title,
        "party_name": c.party_name,
        "vendor": {"id": c.vendor.id, "name": c.vendor.name} if c.vendor else None,
        "start_date": c.start_date,
        "end_date": c.end_date,
        "frequency": c.frequency,
        "period_amount": c.period_amount,
        "gst_rate": c.gst_rate,
        "contract_value": c.contract_value,
        "terms": c.terms,
        "department_id": c.department_id,
        "branch_id": c.branch_id,
        "expense_type_id": c.expense_type_id,
        "has_document": c.document_id is not None,
        "is_active": c.is_active,
    }
    if with_schedule:
        out["schedule"] = schedule(db, c)
    return out


# ---------- link a CONTRACT claim to the register ----------


def link(db: Session, principal: Principal, expense: Expense, data) -> None:
    """Called on every create / update, after base documents are attached."""
    if expense.bill_type != "CONTRACT":
        expense.contract_id = None
        return
    c: Contract | None = None
    if getattr(data, "contract_id", None):
        c = get_visible(db, principal, data.contract_id)
    elif expense.contract_reference:
        found = by_number(db, expense.contract_reference)
        if found is not None:
            try:
                c = get_visible(db, principal, found.id)
            except AppError:
                raise AppError(
                    409,
                    "CONTRACT_NOT_YOURS",
                    "This contract number is already registered by another department - "
                    "ask Finance to share it with you",
                    [{"field": "contract_reference"}],
                ) from None
    if c is None:
        c = _register(db, principal, expense, data)
        if c is None:
            expense.contract_id = None
            return
    expense.contract_id = c.id
    _fill_in(db, principal, c, expense, data)
    # The claim keeps the contract's details as they were when it was made.
    expense.contract_reference = c.contract_no
    expense.contract_party = c.party_name
    expense.contract_start, expense.contract_end = c.start_date, c.end_date
    expense.contract_value = c.contract_value
    expense.contract_terms = c.terms


def _contract_doc(expense: Expense) -> int | None:
    return next((d.id for d in expense.base_documents if d.doc_kind == "CONTRACT"), None)


def _register(db: Session, principal: Principal, expense: Expense, data) -> Contract | None:
    if not (
        expense.contract_reference
        and expense.contract_start
        and expense.contract_end
        and (expense.contract_party or _first_vendor_name(expense))
    ):
        return None  # not enough yet; validation tells the person what is missing
    db.refresh(expense, ["base_documents"])
    freq = getattr(data, "contract_frequency", None) or "MONTHLY"
    c = Contract(
        contract_no=expense.contract_reference.strip(),
        title=(expense.title or "")[:200] or None,
        party_name=(expense.contract_party or _first_vendor_name(expense) or "")[:200],
        vendor_id=next((i.vendor_id for i in expense.items if i.vendor_id), None),
        start_date=expense.contract_start,
        end_date=expense.contract_end,
        frequency=freq if freq in FREQUENCIES else "MONTHLY",
        period_amount=getattr(data, "contract_period_amount", None),
        gst_rate=getattr(data, "contract_gst_rate", None),
        contract_value=expense.contract_value,
        terms=expense.contract_terms,
        department_id=expense.department_id,
        branch_id=expense.branch_id,
        expense_type_id=expense.expense_type_id,
        document_id=_contract_doc(expense),
        created_by_id=principal.user.id,
    )
    db.add(c)
    db.flush()
    audit_service.record(
        db,
        "CONTRACT_REGISTERED",
        "contract",
        c.id,
        actor=principal.user,
        details={
            "contract_no": c.contract_no,
            "party": c.party_name,
            "start": str(c.start_date),
            "end": str(c.end_date),
            "frequency": c.frequency,
            "period_amount": str(c.period_amount) if c.period_amount is not None else None,
            "from_expense": expense.expense_no,
        },
    )
    return c


def _fill_in(db: Session, principal: Principal, c: Contract, expense: Expense, data) -> None:
    """Fill gaps in a stored contract (never overwrite): its document, amount, vendor."""
    changes: dict[str, str] = {}
    if c.document_id is None and (doc := _contract_doc(expense)):
        c.document_id = doc
        changes["document_id"] = str(doc)
    amount = getattr(data, "contract_period_amount", None)
    if c.period_amount is None and amount is not None:
        c.period_amount = Decimal(amount)
        changes["period_amount"] = str(amount)
    vendor = next((i.vendor_id for i in expense.items if i.vendor_id), None)
    if c.vendor_id is None and vendor:
        c.vendor_id = vendor
        changes["vendor_id"] = str(vendor)
    if changes:
        audit_service.record(
            db, "CONTRACT_UPDATED", "contract", c.id, actor=principal.user, details=changes
        )


def _first_vendor_name(expense: Expense) -> str | None:
    for i in expense.items:
        if i.vendor is not None:
            return i.vendor.name
        if i.supplier_name:
            return i.supplier_name
    return None


def has_document(db: Session, expense: Expense) -> bool:
    """The contract file is needed once: on this claim or on the stored contract."""
    if any(d.doc_kind == "CONTRACT" for d in expense.base_documents):
        return True
    if not expense.contract_id:
        return False
    doc_id = db.execute(
        select(Contract.document_id).where(Contract.id == expense.contract_id)
    ).scalar()
    return doc_id is not None and db.get(ExpenseDocument, doc_id) is not None


def overlapping_claims(db: Session, expense: Expense) -> list[Expense]:
    """Other live claims on the same contract covering the same period."""
    span = _claim_span(expense)
    if not expense.contract_id or not span:
        return []
    return [
        e
        for e in _claims(db, expense.contract_id, exclude_expense_id=expense.id)
        if (s := _claim_span(e)) and s[0] <= span[1] and s[1] >= span[0]
    ]
