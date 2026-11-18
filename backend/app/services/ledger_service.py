"""General ledger for paid claims (double entry), built from recorded facts only.

Per paid claim, two vouchers:
  JV-<claim>  on approval:  Dr <category> expense (taxable), Dr Input CGST / SGST / IGST
                            Cr Sundry creditors - <payee>               (bill total)
  PV-<claim>  on payment:   Dr Sundry creditors - <payee>
                            Cr Bank - <mode>  (narration carries the UTR)
Every voucher balances; the sheet ends with total debit = total credit.
"""

import io
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import Principal
from app.core.errors import AppError
from app.models import Expense
from app.services import audit_service
from app.services.scope import expense_scope_clause

ZERO = Decimal("0.00")
COLUMNS = [
    "Date",
    "Voucher no.",
    "Voucher type",
    "Ledger",
    "Debit",
    "Credit",
    "Department",
    "Cost centre",
    "Claim no.",
    "Bill no.",
    "UTR",
    "Narration",
]


def _payee(item: Any, owner_name: str) -> str:
    name = (item.vendor.name if item.vendor else None) or item.supplier_name
    return f"Sundry creditors - {name}" if name else f"Employee payable - {owner_name}"


def entries(
    db: Session, principal: Principal, date_from: date | None, date_to: date | None
) -> list[list[Any]]:
    if not principal.can("ledger.export"):
        raise AppError(403, "FORBIDDEN", "You are not allowed to download the general ledger")
    stmt = select(Expense).where(
        Expense.payment_status == "PAID", expense_scope_clause(principal, "ledger.export")
    )
    if date_from:
        stmt = stmt.where(Expense.paid_on >= date_from)
    if date_to:
        stmt = stmt.where(Expense.paid_on <= date_to)
    rows: list[list[Any]] = []
    for e in db.execute(stmt.order_by(Expense.paid_on, Expense.id)).unique().scalars():
        dept = e.snapshot_department_name or e.department.name
        cc = e.snapshot_cost_centre_code or (e.cost_centre.code if e.cost_centre else "")
        head = f"{e.snapshot_expense_type_name or e.expense_type.name} expense"
        bills = ", ".join(i.bill_number for i in e.items if i.bill_number)
        booked_on = (e.approved_at.date() if e.approved_at else e.paid_on).isoformat()
        jv, pv = f"JV-{e.expense_no}", f"PV-{e.expense_no}"
        taxes = {
            "Input CGST": sum((i.cgst_amount for i in e.items), ZERO),
            "Input SGST": sum((i.sgst_amount for i in e.items), ZERO),
            "Input IGST": sum((i.igst_amount for i in e.items), ZERO),
        }
        payable: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for i in e.items:
            payable[_payee(i, e.owner.full_name)] += i.total_amount

        ctx = (dept, cc, e.expense_no, bills)

        def line(
            d: str,
            v: str,
            vt: str,
            ledger: str,
            dr: Decimal,
            cr: Decimal,
            utr: str,
            nar: str,
            ctx: tuple = ctx,
        ) -> None:
            rows.append([d, v, vt, ledger, dr, cr, *ctx, utr, nar])

        nar_jv = f"Being {e.title} booked ({e.expense_no})"
        line(booked_on, jv, "Journal", head, e.total_taxable, ZERO, "", nar_jv)
        for ledger, amt in taxes.items():
            if amt:
                line(booked_on, jv, "Journal", ledger, amt, ZERO, "", nar_jv)
        for ledger, amt in payable.items():
            line(booked_on, jv, "Journal", ledger, ZERO, amt, "", nar_jv)

        utr = e.payment_reference or ""
        paid_on = e.paid_on.isoformat() if e.paid_on else ""
        nar_pv = f"Paid {e.expense_no} by {e.payment_mode}" + (f", UTR {utr}" if utr else "")
        for ledger, amt in payable.items():
            line(paid_on, pv, "Payment", ledger, amt, ZERO, utr, nar_pv)
        line(
            paid_on,
            pv,
            "Payment",
            f"Bank - {e.payment_mode}",
            ZERO,
            e.paid_amount or e.total_amount,
            utr,
            nar_pv,
        )
    return rows


def xlsx(db: Session, principal: Principal, date_from: date | None, date_to: date | None) -> bytes:
    rows = entries(db, principal, date_from, date_to)
    wb = Workbook()
    ws = wb.active
    ws.title = "General ledger"
    ws.append(COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    dr = sum((r[4] for r in rows), ZERO)
    cr = sum((r[5] for r in rows), ZERO)
    ws.append([])
    ws.append(["", "", "", "TOTAL", dr, cr])
    ws.cell(ws.max_row, 4).font = Font(bold=True)
    for col, width in zip(
        "ABCDEFGHIJKL", (11, 22, 10, 40, 14, 14, 30, 12, 18, 22, 22, 50), strict=True
    ):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2, min_col=5, max_col=6):
        for c in row:
            c.number_format = "#,##0.00"
    audit_service.record(
        db,
        "LEDGER_EXPORTED",
        "report",
        "general_ledger",
        actor=principal.user,
        details={
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
            "lines": len(rows),
            "debit": f"{dr:.2f}",
            "credit": f"{cr:.2f}",
        },
    )
    db.commit()
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
