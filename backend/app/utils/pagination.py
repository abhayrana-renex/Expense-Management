"""Pagination + sorting for list endpoints. The total goes in the X-Total-Count header."""

from typing import Any

from fastapi import Query, Response
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.schemas.common import PageParams, SortOrder


def page_params(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: str | None = Query(None, description="Field to sort by (see endpoint description)"),
    order: SortOrder = Query("desc"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size, sort=sort, order=order)


def paginate(
    db: Session,
    stmt: Select,
    page: PageParams,
    sortable: dict[str, Any],
    tiebreak: Any,
    response: Response | None = None,
) -> list[Any]:
    if page.sort and page.sort not in sortable:
        raise AppError(
            422,
            "VALIDATION_ERROR",
            f"Cannot sort by '{page.sort}'",
            [{"allowed": sorted(sortable)}],
        )
    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    col = sortable[page.sort] if page.sort else next(iter(sortable.values()))
    ordering = (col.asc() if page.order == "asc" else col.desc()).nulls_last()
    rows = (
        db.execute(
            stmt.order_by(ordering, tiebreak.desc()).offset(page.offset).limit(page.page_size)
        )
        .unique()
        .scalars()
        .all()
    )
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return list(rows)
