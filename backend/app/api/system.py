"""Dashboard, audit log, role matrix, enums, health and readiness."""

import csv
import io
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import Principal, get_principal, require
from app.core.errors import ERROR_CODES
from app.models import AuditLog, Role
from app.schemas.common import ERROR_RESPONSES, ORMModel, PageParams
from app.services import audit_service, dashboard_service, expense_rules
from app.utils.pagination import page_params, paginate

router = APIRouter(responses=ERROR_RESPONSES)


class AuditOut(ORMModel):
    id: int
    occurred_at: datetime
    actor_id: int | None
    actor_username: str | None
    action: str
    entity_type: str
    entity_id: str | None
    request_id: str | None
    ip_address: str | None
    user_agent: str | None
    details: dict[str, Any] | None
    row_hash: str


class AuditVerify(BaseModel):
    valid: bool
    checked: int
    first_broken_id: int | None


@router.get("/dashboard/summary", tags=["Dashboard"], summary="Role-aware KPIs and series")
def dashboard(
    months: int = Query(6, ge=1, le=24),
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    return dashboard_service.summary(db, principal, months)


def _audit_query(
    action: str | None,
    entity_type: str | None,
    entity_id: str | None,
    actor_id: int | None,
    date_from: date | None,
    date_to: date | None,
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if date_from:
        stmt = stmt.where(AuditLog.occurred_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.occurred_at < date_to + timedelta(days=1))
    return stmt


@router.get(
    "/audit-logs", response_model=list[AuditOut], tags=["Audit"], summary="Search the audit log"
)
def list_audit(
    response: Response,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: PageParams = Depends(page_params),
    _: Principal = Depends(require("audit.read")),
    db: Session = Depends(get_db),
) -> list:
    stmt = _audit_query(action, entity_type, entity_id, actor_id, date_from, date_to)
    return paginate(db, stmt, page, {"occurred_at": AuditLog.occurred_at}, AuditLog.id, response)


@router.get(
    "/audit-logs/export",
    tags=["Audit"],
    summary="Export audit log as CSV",
    response_class=StreamingResponse,
)
def export_audit(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    principal: Principal = Depends(require("audit.read")),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    stmt = _audit_query(action, entity_type, entity_id, actor_id, date_from, date_to)
    audit_service.record(db, "AUDIT_EXPORTED", "audit_logs", None, actor=principal.user)
    db.commit()
    cols = [
        "id",
        "occurred_at",
        "actor_username",
        "action",
        "entity_type",
        "entity_id",
        "ip_address",
        "user_agent",
        "request_id",
        "details",
        "prev_hash",
        "row_hash",
    ]

    def rows() -> Iterator[bytes]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for row in db.execute(
            stmt.order_by(AuditLog.id).execution_options(yield_per=500)
        ).scalars():
            w.writerow([getattr(row, c) for c in cols])
            yield buf.getvalue().encode()
            buf.seek(0)
            buf.truncate()

    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )


@router.get(
    "/audit-logs/verify",
    response_model=AuditVerify,
    tags=["Audit"],
    summary="Verify the audit hash chain",
)
def verify_audit(
    _: Principal = Depends(require("audit.read")), db: Session = Depends(get_db)
) -> dict:
    return audit_service.verify_chain(db)


@router.get("/roles/matrix", tags=["Meta"], summary="Read-only role -> permission -> scope matrix")
def role_matrix(_: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    roles = db.execute(select(Role).order_by(Role.id)).scalars()
    return {r.code: {g.permission.code: g.scope for g in r.grants} for r in roles}


@router.get("/meta/enums", tags=["Meta"], summary="Enums and error codes for clients")
def enums() -> dict:
    return {
        "expense_status": list(expense_rules.STATUSES),
        "expense_actions": {k: v.description for k, v in expense_rules.TRANSITIONS.items()},
        "approval_step_status": [
            "WAITING",
            "PENDING",
            "APPROVED",
            "REJECTED",
            "RETURNED",
            "CANCELLED",
            "SKIPPED",
        ],
        "data_scopes": ["OWN", "ASSIGNED", "DEPARTMENT", "ALL"],
        "validation_severity": ["BLOCK", "WARN"],
        "error_codes": ERROR_CODES,
    }


@router.get("/health", tags=["Meta"], summary="Liveness")
def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", tags=["Meta"], summary="Readiness: database and migration version")
def ready(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return JSONResponse(
            {"status": "ready", "database": "ok", "migration": version, "last_master_sync": None}
        )
    except Exception:  # noqa: BLE001 - readiness must never raise
        return JSONResponse({"status": "not_ready", "database": "error"}, status_code=503)
