"""Master data endpoints. FAR-shared masters (departments, branches, vendors, employees) are
read and written in FAR's own tables; cost centres, categories, bill months and approval
rules are portal-only."""

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import Principal, require
from app.models import ApprovalLevel, ApprovalRule, BillMonth, GstRate
from app.schemas.common import ERROR_RESPONSES, ErrorResponse, PageParams
from app.schemas.masters import (
    ApprovalLevelIn,
    ApprovalLevelOut,
    ApprovalRuleIn,
    ApprovalRuleOut,
    ApprovalRuleUpdate,
    BillMonthOut,
    BillMonthUpdate,
)
from app.services import config_service, master_service
from app.services.master_service import MasterSpec
from app.utils.pagination import page_params, paginate

router = APIRouter(prefix="/masters", responses=ERROR_RESPONSES)
read = require("master.read")
manage = require("master.manage")
manage_rules = require("approval_rule.manage")
MANAGED = {409: {"model": ErrorResponse, "description": "DUPLICATE (in the portal or FAR)"}}


def _register(spec: MasterSpec) -> None:
    tag = [f"Masters: {spec.label}"]
    sortable = {f: getattr(spec.model, f) for f in spec.sort_fields}
    shared_note = (
        "Shared live with FAR: changes made here appear in FAR immediately and vice versa. "
        if spec.shared
        else "Portal-only master. "
    )

    def list_items(
        response: Response,
        q: str | None = Query(None, max_length=100),
        is_active: bool | None = Query(None, description="Omit for all"),
        page: PageParams = Depends(page_params),
        _: Principal = Depends(read),
        db: Session = Depends(get_db),
    ) -> list:
        stmt = master_service.list_query(spec, q, is_active)
        return paginate(db, stmt, page, sortable, spec.model.id, response)

    def get_item(item_id: int, _: Principal = Depends(read), db: Session = Depends(get_db)) -> Any:
        return master_service.get(db, spec, item_id)

    def create_item(
        body: spec.create_schema = Body(...),  # type: ignore[name-defined]
        actor: Principal = Depends(manage),
        db: Session = Depends(get_db),
    ) -> Any:
        return master_service.create(db, actor, spec, body)

    def update_item(
        item_id: int,
        body: spec.update_schema = Body(...),  # type: ignore[name-defined]
        actor: Principal = Depends(manage),
        db: Session = Depends(get_db),
    ) -> Any:
        return master_service.update(db, actor, spec, item_id, body)

    def deactivate_item(
        item_id: int, actor: Principal = Depends(manage), db: Session = Depends(get_db)
    ) -> Any:
        return master_service.set_active(db, actor, spec, item_id, False)

    def activate_item(
        item_id: int, actor: Principal = Depends(manage), db: Session = Depends(get_db)
    ) -> Any:
        return master_service.set_active(db, actor, spec, item_id, True)

    out = spec.out_schema
    base = f"/{spec.key}"
    label = spec.label.lower()
    router.add_api_route(
        base,
        list_items,
        methods=["GET"],
        response_model=list[out],
        tags=tag,
        summary=f"List {label}s",
        description=shared_note
        + "Dropdowns should pass is_active=true. Sort by: "
        + ", ".join(sortable),
        operation_id=f"list_{spec.key.replace('-', '_')}",
    )
    router.add_api_route(
        base,
        create_item,
        methods=["POST"],
        response_model=out,
        status_code=201,
        tags=tag,
        summary=f"Create {label}",
        description=shared_note,
        responses=MANAGED,
        operation_id=f"create_{spec.key.replace('-', '_')}",
    )
    router.add_api_route(
        f"{base}/{{item_id}}",
        get_item,
        methods=["GET"],
        response_model=out,
        tags=tag,
        summary=f"Get {label}",
        operation_id=f"get_{spec.key.replace('-', '_')}",
    )
    router.add_api_route(
        f"{base}/{{item_id}}",
        update_item,
        methods=["PATCH"],
        response_model=out,
        tags=tag,
        summary=f"Update {label}",
        description=shared_note,
        responses=MANAGED,
        operation_id=f"update_{spec.key.replace('-', '_')}",
    )
    router.add_api_route(
        f"{base}/{{item_id}}/deactivate",
        deactivate_item,
        methods=["POST"],
        response_model=out,
        tags=tag,
        summary=f"Deactivate {label}",
        description=shared_note + "Never deletes: expenses (and FAR assets) keep their "
        "reference; FAR rows get status Inactive.",
        responses=MANAGED,
        operation_id=f"deactivate_{spec.key.replace('-', '_')}",
    )
    router.add_api_route(
        f"{base}/{{item_id}}/activate",
        activate_item,
        methods=["POST"],
        response_model=out,
        tags=tag,
        summary=f"Activate {label}",
        responses=MANAGED,
        operation_id=f"activate_{spec.key.replace('-', '_')}",
    )


for _spec in master_service.MASTERS.values():
    _register(_spec)


# ---------- Bill months (local) ----------


@router.get(
    "/bill-months",
    response_model=list[BillMonthOut],
    tags=["Masters: Bill month"],
    summary="List bill months",
    description="Generated on a rolling financial-year basis.",
)
def list_bill_months(
    open_only: bool = False, _: Principal = Depends(read), db: Session = Depends(get_db)
) -> list:
    stmt = select(BillMonth).where(BillMonth.is_active.is_(True))
    if open_only:
        stmt = stmt.where(BillMonth.is_open.is_(True))
    return list(db.execute(stmt.order_by(BillMonth.month_start.desc())).scalars())


@router.post(
    "/bill-months/generate",
    tags=["Masters: Bill month"],
    summary="Ensure previous, current and next FY months exist (idempotent)",
)
def generate_bill_months(actor: Principal = Depends(manage), db: Session = Depends(get_db)) -> dict:
    created = config_service.ensure_bill_months(db, actor=actor.user)
    db.commit()
    return {"created": created}


@router.patch(
    "/bill-months/{month_id}",
    response_model=BillMonthOut,
    tags=["Masters: Bill month"],
    summary="Open or close a bill month",
)
def update_bill_month(
    month_id: int,
    body: BillMonthUpdate,
    actor: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> BillMonth:
    return config_service.set_bill_month_open(db, actor, month_id, body.is_open)


# ---------- Approval levels & rules (local) ----------


@router.get(
    "/approval-levels",
    response_model=list[ApprovalLevelOut],
    tags=["Masters: Approval rules"],
    summary="List approval levels",
)
def list_levels(_: Principal = Depends(read), db: Session = Depends(get_db)) -> list:
    return list(db.execute(select(ApprovalLevel).order_by(ApprovalLevel.rank)).scalars())


@router.post(
    "/approval-levels",
    response_model=ApprovalLevelOut,
    status_code=201,
    tags=["Masters: Approval rules"],
    summary="Create approval level",
)
def create_level(
    body: ApprovalLevelIn, actor: Principal = Depends(manage_rules), db: Session = Depends(get_db)
) -> ApprovalLevel:
    return config_service.create_level(db, actor, body)


@router.get(
    "/approval-rules",
    response_model=list[ApprovalRuleOut],
    tags=["Masters: Approval rules"],
    summary="List approval rules",
    description="A level is required when total > threshold_amount; the highest "
    "required level must have approval_limit >= total (null = no limit). An expense-type "
    "rule set overrides the department-wide one.",
)
def list_rules(
    response: Response,
    department_id: int | None = None,
    include_inactive: bool = False,
    page: PageParams = Depends(page_params),
    _: Principal = Depends(manage_rules),
    db: Session = Depends(get_db),
) -> list:
    stmt = config_service.rules_query(department_id, include_inactive)
    sortable = {
        "department_id": ApprovalRule.department_id,
        "threshold_amount": ApprovalRule.threshold_amount,
    }
    return paginate(db, stmt, page, sortable, ApprovalRule.id, response)


@router.post(
    "/approval-rules",
    response_model=ApprovalRuleOut,
    status_code=201,
    tags=["Masters: Approval rules"],
    summary="Create approval rule",
    responses={409: {"model": ErrorResponse}},
)
def create_rule(
    body: ApprovalRuleIn, actor: Principal = Depends(manage_rules), db: Session = Depends(get_db)
) -> ApprovalRule:
    return config_service.create_rule(db, actor, body)


@router.patch(
    "/approval-rules/{rule_id}",
    response_model=ApprovalRuleOut,
    tags=["Masters: Approval rules"],
    summary="Update approval rule",
)
def update_rule(
    rule_id: int,
    body: ApprovalRuleUpdate,
    actor: Principal = Depends(manage_rules),
    db: Session = Depends(get_db),
) -> ApprovalRule:
    return config_service.update_rule(db, actor, rule_id, body)


@router.post(
    "/approval-rules/{rule_id}/deactivate",
    response_model=ApprovalRuleOut,
    tags=["Masters: Approval rules"],
    summary="Deactivate approval rule",
)
def deactivate_rule(
    rule_id: int, actor: Principal = Depends(manage_rules), db: Session = Depends(get_db)
) -> ApprovalRule:
    return config_service.deactivate_rule(db, actor, rule_id)


# ---------- GST rates (local) ----------


class GstRateIn(BaseModel):
    rate: Decimal = Field(ge=0, le=100, decimal_places=2, examples=["18"])
    applies_to: Literal["ALL", "CGST", "SGST", "IGST"] = "ALL"


class GstRateOut(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    rate: Decimal
    applies_to: str
    is_active: bool


@router.get(
    "/gst-rates",
    response_model=list[GstRateOut],
    tags=["Masters: GST rates"],
    summary="GST rate options for the CGST / SGST / IGST dropdowns",
    description="Admin-managed (never hardcoded). Pass include_inactive=true for admin.",
)
def list_gst_rates(
    include_inactive: bool = False, _: Principal = Depends(read), db: Session = Depends(get_db)
) -> list:
    stmt = select(GstRate).order_by(GstRate.rate, GstRate.applies_to)
    if not include_inactive:
        stmt = stmt.where(GstRate.is_active.is_(True))
    return list(db.execute(stmt).scalars())


@router.post(
    "/gst-rates",
    response_model=GstRateOut,
    status_code=201,
    tags=["Masters: GST rates"],
    summary="Add a GST rate option",
    responses={409: {"model": ErrorResponse}},
)
def create_gst_rate(
    body: GstRateIn, actor: Principal = Depends(manage), db: Session = Depends(get_db)
) -> GstRate:
    return config_service.create_gst_rate(db, actor, body.rate, body.applies_to)


@router.post(
    "/gst-rates/{rate_id}/{action}",
    response_model=GstRateOut,
    tags=["Masters: GST rates"],
    summary="Activate or deactivate a GST rate option",
)
def toggle_gst_rate(
    rate_id: int,
    action: Literal["activate", "deactivate"],
    actor: Principal = Depends(manage),
    db: Session = Depends(get_db),
) -> GstRate:
    return config_service.set_gst_rate_active(db, actor, rate_id, action == "activate")
