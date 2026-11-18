"""Duplicate-invoice guard.

A bill is a duplicate when the SAME COMPANY's invoice number is already claimed on another
live expense (anything not cancelled or rejected) or on another line of the same expense.
The same invoice number from a different company is fine - numbering is per supplier.

Company identity, strongest first:
* both sides have a supplier GSTIN -> the GSTINs must match (one GST registration = one
  invoice series), whatever the names say;
* otherwise the same FAR vendor, or the same supplier name ignoring case, spaces and
  punctuation ("ABC Traders Pvt. Ltd." == "abc traders pvt ltd").
Invoice numbers are compared the same way ("INV-001" == "inv 001" == "INV/001").
"""

import re
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Expense, ExpenseItem, Partner, User

DEAD = ("CANCELLED", "REJECTED")
_NON_ALNUM = re.compile(r"[^0-9A-Z]")
_SUFFIXES = re.compile(r"(PRIVATE|PVT|LIMITED|LTD|LLP|CO|COMPANY|INC|THE)+$")


def norm_invoice(value: str | None) -> str:
    return _NON_ALNUM.sub("", (value or "").upper())


def norm_company(name: str | None) -> str:
    key = _NON_ALNUM.sub("", (name or "").upper())
    stripped = _SUFFIXES.sub("", key)
    return stripped or key


@dataclass(frozen=True)
class Company:
    gstin: str | None
    vendor_id: int | None
    name: str  # normalised; "" when unknown

    @property
    def known(self) -> bool:
        return bool(self.gstin or self.vendor_id or self.name)

    @property
    def label(self) -> str:
        return self.gstin or self.name or "unknown company"

    def same_as(self, other: "Company") -> bool:
        if self.gstin and other.gstin:
            return self.gstin == other.gstin
        if self.vendor_id and other.vendor_id and self.vendor_id == other.vendor_id:
            return True
        return bool(self.name) and self.name == other.name


def company_of(
    vendor: Partner | None, supplier_gstin: str | None, supplier_name: str | None
) -> Company:
    gstin = (supplier_gstin or (vendor.gstin if vendor else None) or "").strip().upper() or None
    name = norm_company(supplier_name or (vendor.name if vendor else None))
    return Company(gstin, vendor.id if vendor else None, name)


def _item_company(item: ExpenseItem) -> Company:
    return company_of(item.vendor, item.supplier_gstin, item.supplier_name)


@dataclass(frozen=True)
class Match:
    expense_id: int
    expense_no: str
    status: str
    owner_name: str
    is_mine: bool
    line_no: int
    company: str


def find(
    db: Session,
    bill_number: str | None,
    company: Company,
    *,
    viewer_id: int,
    exclude_expense_id: int | None = None,
) -> tuple[list[Match], int]:
    """(same-company matches, count of same number from OTHER companies)."""
    key = norm_invoice(bill_number)
    if not key:
        return [], 0
    rows = db.execute(
        select(ExpenseItem, Expense.expense_no, Expense.status, Expense.owner_id, User.full_name)
        .join(Expense, ExpenseItem.expense_id == Expense.id)
        .join(User, User.id == Expense.owner_id)
        .where(
            func.upper(func.regexp_replace(ExpenseItem.bill_number, "[^0-9A-Za-z]", "", "g"))
            == key,
            Expense.status.notin_(DEAD),
            *([Expense.id != exclude_expense_id] if exclude_expense_id else []),
        )
        .order_by(Expense.id)
    ).all()
    same: list[Match] = []
    others = 0
    for item, no, status, owner_id, owner_name in rows:
        theirs = _item_company(item)
        if company.known and theirs.known and company.same_as(theirs):
            same.append(
                Match(
                    item.expense_id,
                    no,
                    status,
                    owner_name,
                    owner_id == viewer_id,
                    item.line_no,
                    theirs.label,
                )
            )
        else:
            others += 1
    return same, others


def lock(db: Session, bill_numbers: list[str | None]) -> None:
    """Serialise concurrent claims of the same invoice number until this transaction ends."""
    for key in sorted({norm_invoice(b) for b in bill_numbers if norm_invoice(b)}):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"invoice:{key}"})


def describe(m: Match) -> str:
    who = "your" if m.is_mine else f"{m.owner_name}'s"
    return f"already claimed in {who} expense {m.expense_no} (line {m.line_no})"


def assert_no_duplicates(db: Session, expense: Expense, viewer_id: int) -> None:
    """Raise 422 DUPLICATE_INVOICE if any line repeats an invoice of the same company,
    here or on another live expense. Call after the lines are applied (before commit)."""
    lock(db, [i.bill_number for i in expense.items])
    errors: list[dict] = []
    seen: list[tuple[str, Company, int]] = []
    for item in expense.items:
        key = norm_invoice(item.bill_number)
        if not key:
            continue
        company = _item_company(item)
        twin = (
            next(
                (
                    n
                    for k, c, n in seen
                    if k == key and c.known and company.known and c.same_as(company)
                ),
                None,
            )
            if company.known
            else None
        )
        if twin is not None:
            errors.append(
                {
                    "field": "bill_number",
                    "line_no": item.line_no,
                    "message": f"Line {item.line_no}: invoice {item.bill_number} from "
                    f"{company.label} is already on line {twin} of this expense",
                }
            )
        seen.append((key, company, item.line_no))
        matches, _ = find(
            db, item.bill_number, company, viewer_id=viewer_id, exclude_expense_id=expense.id
        )
        if matches:
            errors.append(
                {
                    "field": "bill_number",
                    "line_no": item.line_no,
                    "expense_no": matches[0].expense_no,
                    "message": f"Line {item.line_no}: invoice {item.bill_number} from "
                    f"{company.label} is {describe(matches[0])}",
                }
            )
    if errors:
        raise AppError(
            422,
            "DUPLICATE_INVOICE",
            errors[0]["message"] + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""),
            errors,
        )
