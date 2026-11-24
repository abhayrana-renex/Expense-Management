"""Expense report and line-level admin dump, with CSV/Excel export (streamed)."""

import csv
import io
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, aliased

from app.core.deps import Principal
from app.models import (
    BillMonth,
    Branch,
    Department,
    Expense,
    ExpenseItem,
    ExpenseType,
    User,
)
from app.services import audit_service
from app.services.expense_service import list_query

Approver = aliased(User)

REPORT_COLUMNS: list[tuple[str, str]] = [
    ("expense_no", "Expense No"),
    ("title", "Title"),
    ("employee_code", "Employee Code"),
    ("employee_name", "Employee"),
    ("department", "Department"),
    ("branch", "Branch"),
    ("expense_type", "Expense Type"),
    ("bill_month", "Bill Month"),
    ("status", "Status"),
    ("current_approver", "Pending With"),
    ("total_taxable", "Taxable"),
    ("total_tax", "GST"),
    ("total_amount", "Total"),
    ("submitted_at", "Submitted At"),
    ("approved_at", "Approved At"),
]
MONEY_KEYS = {
    "total_taxable",
    "total_tax",
    "total_amount",
    "taxable_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "item_total",
}

DUMP_COLUMNS: list[tuple[str, str]] = [
    ("expense_no", "Expense No"),
    ("employee_code", "Employee Code"),
    ("employee_name", "Employee"),
    ("department", "Department"),
    ("branch", "Branch"),
    ("expense_type", "Expense Type"),
    ("bill_month", "Bill Month"),
    ("line_no", "Line"),
    ("expense_date", "Expense Date"),
    ("description", "Description"),
    ("vendor_name", "Vendor"),
    ("vendor_gstin", "Vendor GSTIN"),
    ("bill_number", "Bill No"),
    ("bill_date", "Bill Date"),
    ("taxable_amount", "Taxable"),
    ("cgst_amount", "CGST"),
    ("sgst_amount", "SGST"),
    ("igst_amount", "IGST"),
    ("item_total", "Line Total"),
    ("status", "Status"),
    ("submitted_at", "Submitted At"),
    ("approved_at", "Approved Date"),
]


def report_rows_query(principal: Principal, **filters: Any) -> Select:
    base = list_query(principal, permission="report.read", **filters).subquery()
    return (
        select(
            Expense.expense_no,
            Expense.title,
            User.employee_code,
            User.full_name.label("employee_name"),
            func.coalesce(Expense.snapshot_department_name, Department.name).label("department"),
            func.coalesce(Expense.snapshot_branch_name, Branch.name).label("branch"),
            func.coalesce(Expense.snapshot_expense_type_name, ExpenseType.name).label(
                "expense_type"
            ),
            BillMonth.label.label("bill_month"),
            Expense.status,
            Approver.full_name.label("current_approver"),
            Expense.total_taxable,
            Expense.total_tax,
            Expense.total_amount,
            Expense.submitted_at,
            Expense.approved_at,
        )
        .join(base, base.c.id == Expense.id)
        .join(User, User.id == Expense.owner_id)
        .join(Department, Department.id == Expense.department_id)
        .join(Branch, Branch.id == Expense.branch_id)
        .join(ExpenseType, ExpenseType.id == Expense.expense_type_id)
        .outerjoin(BillMonth, BillMonth.id == Expense.bill_month_id)
        .outerjoin(Approver, Approver.id == Expense.current_approver_id)
    )


def totals(db: Session, principal: Principal, **filters: Any) -> dict[str, Any]:
    base = list_query(principal, permission="report.read", **filters).subquery()
    row = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(base.c.total_taxable), 0),
            func.coalesce(func.sum(base.c.total_tax), 0),
            func.coalesce(func.sum(base.c.total_amount), 0),
        )
    ).one()
    return {
        "count": row[0],
        "total_taxable": Decimal(row[1]),
        "total_tax": Decimal(row[2]),
        "total_amount": Decimal(row[3]),
    }


def dump_rows_query(principal: Principal, **filters: Any) -> Select:
    base = list_query(principal, permission="report.read", **filters).subquery()
    return (
        select(
            Expense.expense_no,
            User.employee_code,
            User.full_name.label("employee_name"),
            func.coalesce(Expense.snapshot_department_name, Department.name).label("department"),
            func.coalesce(Expense.snapshot_branch_name, Branch.name).label("branch"),
            func.coalesce(Expense.snapshot_expense_type_name, ExpenseType.name).label(
                "expense_type"
            ),
            BillMonth.label.label("bill_month"),
            ExpenseItem.line_no,
            ExpenseItem.expense_date,
            ExpenseItem.description,
            ExpenseItem.snapshot_vendor_name.label("vendor_name"),
            ExpenseItem.snapshot_vendor_gstin.label("vendor_gstin"),
            ExpenseItem.bill_number,
            ExpenseItem.bill_date,
            ExpenseItem.taxable_amount,
            ExpenseItem.cgst_amount,
            ExpenseItem.sgst_amount,
            ExpenseItem.igst_amount,
            ExpenseItem.total_amount.label("item_total"),
            Expense.status,
            Expense.submitted_at,
            Expense.approved_at,
        )
        .join(base, base.c.id == Expense.id)
        .join(ExpenseItem, ExpenseItem.expense_id == Expense.id)
        .join(User, User.id == Expense.owner_id)
        .join(Department, Department.id == Expense.department_id)
        .join(Branch, Branch.id == Expense.branch_id)
        .join(ExpenseType, ExpenseType.id == Expense.expense_type_id)
        .outerjoin(BillMonth, BillMonth.id == Expense.bill_month_id)
        .order_by(Expense.id, ExpenseItem.line_no)
    )


def _cell(key: str, value: Any) -> Any:
    if value is None:
        return ""
    if key in MONEY_KEYS:
        return f"{Decimal(value):.2f}"
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _rows(db: Session, stmt: Select, columns: list[tuple[str, str]]) -> Iterator[list[Any]]:
    for row in db.execute(stmt.execution_options(yield_per=500)).mappings():
        yield [_cell(k, row[k]) for k, _ in columns]


def csv_stream(
    db: Session, stmt: Select, columns: list[tuple[str, str]], total_row: list[Any] | None
) -> Iterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf)
    yield "﻿".encode()  # BOM so Excel opens UTF-8 correctly
    writer.writerow([label for _, label in columns])
    for row in _rows(db, stmt, columns):
        writer.writerow(row)
        if buf.tell() > 65536:
            yield buf.getvalue().encode()
            buf.seek(0)
            buf.truncate()
    if total_row:
        writer.writerow(total_row)
    yield buf.getvalue().encode()


def xlsx_bytes(
    db: Session,
    stmt: Select,
    columns: list[tuple[str, str]],
    total_row: list[Any] | None,
    title: str,
) -> bytes:
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title[:31])
    ws.append([label for _, label in columns])
    money_idx = {i for i, (k, _) in enumerate(columns) if k in MONEY_KEYS}
    for row in _rows(db, stmt, columns):
        ws.append([Decimal(v) if i in money_idx and v != "" else v for i, v in enumerate(row)])
    if total_row:
        ws.append(total_row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def report_total_row(t: dict[str, Any]) -> list[Any]:
    row: list[Any] = [""] * len(REPORT_COLUMNS)
    row[0] = f"TOTAL ({t['count']} expenses)"
    keys = [k for k, _ in REPORT_COLUMNS]
    for key in ("total_taxable", "total_tax", "total_amount"):
        row[keys.index(key)] = f"{t[key]:.2f}"
    return row


def audit_export(db: Session, principal: Principal, kind: str, fmt: str, filters: dict) -> None:
    audit_service.record(
        db,
        "REPORT_EXPORTED",
        "report",
        kind,
        actor=principal.user,
        details={"format": fmt, "filters": {k: v for k, v in filters.items() if v is not None}},
    )
    db.commit()
