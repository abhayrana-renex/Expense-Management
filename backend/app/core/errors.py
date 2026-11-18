"""Structured errors: every error response is {detail, code, errors[]}; no stack traces."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import get_request_context

log = logging.getLogger("app.errors")


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.errors = errors or []


def not_found(entity: str) -> AppError:
    return AppError(404, "NOT_FOUND", f"{entity} not found")


def forbidden(detail: str = "You do not have permission to perform this action") -> AppError:
    return AppError(403, "FORBIDDEN", detail)


def conflict(code: str, detail: str) -> AppError:
    return AppError(409, code, detail)


def unprocessable(code: str, detail: str, errors: list[dict[str, Any]] | None = None) -> AppError:
    return AppError(422, code, detail, errors)


# Catalogue of error codes (also served in docs/API_GUIDE.md).
ERROR_CODES: dict[str, str] = {
    "VALIDATION_ERROR": "Request body or parameters failed validation",
    "NOT_FOUND": "Resource does not exist or is outside your data scope",
    "FORBIDDEN": "Authenticated but not allowed",
    "NOT_AUTHENTICATED": "Missing, invalid, expired or revoked token",
    "INVALID_CREDENTIALS": "Wrong username or password",
    "ACCOUNT_LOCKED": "Too many failed logins; try again later",
    "ACCOUNT_INACTIVE": "User is deactivated",
    "RATE_LIMITED": "Too many requests",
    "PASSWORD_CHANGE_REQUIRED": "User must change password before using the API",
    "PASSWORD_POLICY": "New password does not meet the policy",
    "INVALID_TRANSITION": "Action not allowed in the expense's current state",
    "CONCURRENT_MODIFICATION": "Record changed since you loaded it; reload and retry",
    "DUPLICATE": "A record with the same unique value exists",
    "IN_USE": "Record is referenced by other records",
    "SUBMISSION_BLOCKED": "Expense has blocking validation issues",
    "DUPLICATE_INVOICE": "Same company's invoice number is already claimed (errors[] per line)",
    "NO_APPROVAL_MAPPING": "No approval rule covers this department/expense type",
    "AMOUNT_EXCEEDS_APPROVAL_LIMIT": "Total exceeds the highest approval limit",
    "NO_INDEPENDENT_APPROVER": "Only approver available is the submitter",
    "APPROVER_INACTIVE": "Mapped approver is inactive",
    "MASTER_MANAGED_EXTERNALLY": "Master is owned by another system; read-only here",
    "INVALID_FILE": "Upload is not an allowed file",
    "FILE_TOO_LARGE": "Upload exceeds the size limit",
    "INVALID_SIGNATURE": "Signed URL is invalid or expired",
    "IDEMPOTENCY_KEY_REUSED": "Idempotency-Key was used with a different request",
    "INTERNAL_ERROR": "Unexpected server error (see request id in logs)",
}


def _body(detail: str, code: str, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"detail": detail, "code": code, "errors": errors or []}
    rid = get_request_context().request_id
    if rid:
        body["request_id"] = rid
    return body


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(_body(exc.detail, exc.code, exc.errors), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return JSONResponse(_body("Validation failed", "VALIDATION_ERROR", errors), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_AUTHENTICATED" if exc.status_code == 401 else f"HTTP_{exc.status_code}"
        return JSONResponse(
            _body(str(exc.detail), code), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(StaleDataError)
    async def _stale(_: Request, exc: StaleDataError) -> JSONResponse:
        return JSONResponse(
            _body(ERROR_CODES["CONCURRENT_MODIFICATION"], "CONCURRENT_MODIFICATION"),
            status_code=409,
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        pgcode = getattr(exc.orig, "pgcode", None)
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if pgcode == "23505":
            return JSONResponse(
                _body(
                    "A record with the same unique value already exists",
                    "DUPLICATE",
                    [{"constraint": constraint}],
                ),
                status_code=409,
            )
        if pgcode == "23503":
            return JSONResponse(
                _body(
                    "Record is referenced by other records", "IN_USE", [{"constraint": constraint}]
                ),
                status_code=409,
            )
        log.exception("Integrity error", extra={"request_id": get_request_context().request_id})
        return JSONResponse(_body("Data integrity error", "INTEGRITY_ERROR"), status_code=409)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", extra={"request_id": get_request_context().request_id})
        return JSONResponse(_body("Internal server error", "INTERNAL_ERROR"), status_code=500)
