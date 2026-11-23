"""FastAPI application factory."""

import logging

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    bills,
    contracts,
    discussion,
    engagement,
    expenses,
    masters,
    reports,
    roles,
    system,
    users,
    vendor_master,
)
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.middleware import install_middleware

API_DESCRIPTION = """
Expense Management Portal API. **The OpenAPI document is the contract.**

* Money is a string with 2 decimals (`"1250.00"`); never send floats.
* Timestamps are ISO 8601 in UTC with offset; dates are `YYYY-MM-DD`.
* Lists accept `page`, `page_size`, `sort`, `order`; the total is in `X-Total-Count`.
* Errors are `{detail, code, errors[]}`; codes are listed at `GET /api/meta/enums`.
* UIs render buttons from `GET /api/expenses/{id}/allowed-actions` and
  permissions from `GET /api/auth/me` - never from hardcoded role checks.
"""


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description=API_DESCRIPTION,
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    register_error_handlers(app)
    install_middleware(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Total-Count", "X-Request-ID", "Content-Disposition"],
        max_age=600,
    )
    api = APIRouter(prefix=settings.API_V1_PREFIX)
    for module in (
        auth,
        users,
        roles,
        masters,
        bills,
        contracts,
        engagement,
        discussion,  # before expenses: /expenses/{id}/messages is not a workflow action
        expenses,
        reports,
        system,
        vendor_master,
    ):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
