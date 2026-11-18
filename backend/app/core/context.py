"""Per-request context (request id, client IP, user agent) used by the audit log."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    request_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


_ctx: ContextVar[RequestContext | None] = ContextVar("request_ctx", default=None)
_EMPTY = RequestContext()


def set_request_context(ctx: RequestContext) -> None:
    _ctx.set(ctx)


def get_request_context() -> RequestContext:
    return _ctx.get() or _EMPTY
