"""Request id, client context and security headers."""

import uuid

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings
from app.core.context import RequestContext, set_request_context

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Swagger UI needs inline scripts from its CDN; everything else gets a locked-down CSP.
API_CSP = "default-src 'none'; frame-ancestors 'none'"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = (
            incoming if 8 <= len(incoming) <= 64 and incoming.isalnum() else uuid.uuid4().hex
        )
        client_ip = request.client.host if request.client else None
        set_request_context(
            RequestContext(
                request_id=request_id,
                ip_address=client_ip,
                user_agent=request.headers.get("User-Agent"),
            )
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        csp = response.headers.get("Content-Security-Policy", "")
        framable = "frame-ancestors" in csp and "frame-ancestors 'none'" not in csp
        for name, value in SECURITY_HEADERS.items():
            if name == "X-Frame-Options" and framable:
                continue  # the endpoint allowed specific origins to frame it (bill preview)
            response.headers.setdefault(name, value)
        path = request.url.path
        if not (path.startswith("/docs") or path.startswith("/redoc")):
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if path.startswith(settings.API_V1_PREFIX):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def install_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
