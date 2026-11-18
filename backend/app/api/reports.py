"""Approver queue, reports (paginated + export) and the admin dump."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import Principal, require
from app.core.security import utcnow
from app.models import Expense
from app.schemas.common import ERROR_RESPONSES, Money, PageParams
from app.schemas.expense import ExpenseSummaryOut
from app.services import approval_service, report_service
from app.services.expense_service import list_query
from app.utils.pagination import page_params, paginate

router = APIRouter(responses=ERROR_RESPONSES)
SORTABLE = {
    "submitted_at": Expense.submitted_at,
    "total_amount": Expense.total_amount,
    "created_at": Expense.created_at,
    "expense_no": Expense.expense_no,
}


class ReportTotals(BaseModel):
    count: int
    total_taxable: Money
    total_tax: Money
    total_amount: Money


class ReportPage(BaseModel):
    rows: list[ExpenseSummaryOut]
    totals: ReportTotals


def _filters(
    status: str | None = Query(None, max_length=30),
    date_from: date | None = None,
    date_to: date | None = None,
    expense_type_id: int | None = None,
    department_id: int | None = None,
    branch_id: int | None = None,
    q: str | None = Query(None, max_length=100),
) -> dict[str, Any]:
    return {
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "expense_type_id": expense_type_id,
        "department_id": department_id,
        "branch_id": branch_id,
        "q": q,
    }


@router.get(
    "/approvals/queue",
    response_model=list[ExpenseSummaryOut],
    tags=["Approvals"],
    summary="Expenses waiting for my decision",
)
def approval_queue(
    response: Response,
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(require("expense.approve")),
    db: Session = Depends(get_db),
) -> list:
    return paginate(
        db, approval_service.queue_query(principal), page, SORTABLE, Expense.id, response
    )


@router.get(
    "/reports/expenses",
    response_model=ReportPage,
    tags=["Reports"],
    summary="Expense report with totals",
    description="Scoped: ADMIN sees all, APPROVER their department plus assigned, USER "
    "their own. Total row count in X-Total-Count.",
)
def expense_report(
    response: Response,
    filters: dict = Depends(_filters),
    page: PageParams = Depends(page_params),
    principal: Principal = Depends(require("report.read")),
    db: Session = Depends(get_db),
) -> dict:
    stmt = list_query(principal, permission="report.read", **filters)
    rows = paginate(db, stmt, page, SORTABLE, Expense.id, response)
    return {"rows": rows, "totals": report_service.totals(db, principal, **filters)}


def _download(
    db: Session, principal: Principal, kind: str, fmt: str, filters: dict
) -> StreamingResponse:
    if kind == "dump":
        stmt = report_service.dump_rows_query(principal, **filters)
        columns, total_row = report_service.DUMP_COLUMNS, None
    else:
        stmt = report_service.report_rows_query(principal, **filters)
        columns = report_service.REPORT_COLUMNS
        total_row = report_service.report_total_row(report_service.totals(db, principal, **filters))
    report_service.audit_export(db, principal, kind, fmt, filters)
    stamp = f"{utcnow():%Y%m%d-%H%M%S}"
    if fmt == "csv":
        return StreamingResponse(
            report_service.csv_stream(db, stmt, columns, total_row),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{kind}-{stamp}.csv"'},
        )
    data = report_service.xlsx_bytes(db, stmt, columns, total_row, kind)
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{kind}-{stamp}.xlsx"'},
    )


@router.get(
    "/reports/expenses/export",
    tags=["Reports"],
    summary="Export expense report",
    response_class=StreamingResponse,
)
def export_report(
    format: Literal["xlsx", "csv"] = "xlsx",
    filters: dict = Depends(_filters),
    principal: Principal = Depends(require("report.export")),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return _download(db, principal, "expense-report", format, filters)


@router.get(
    "/reports/dump",
    tags=["Reports"],
    response_class=StreamingResponse,
    summary="Admin dump: one row per line item, all columns incl. Approved Date",
)
def export_dump(
    format: Literal["xlsx", "csv"] = "xlsx",
    filters: dict = Depends(_filters),
    principal: Principal = Depends(require("report.dump")),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return _download(db, principal, "dump", format, filters)
