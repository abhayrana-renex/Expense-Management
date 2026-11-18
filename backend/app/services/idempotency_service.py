"""Idempotency-Key support: a retried request returns the original response."""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import IdempotencyRecord


@dataclass
class IdempotencyContext:
    user_id: int
    key: str
    scope: str
    request_hash: str


def begin(
    db: Session, user_id: int, key: str | None, scope: str, payload: Any
) -> tuple[IdempotencyContext | None, IdempotencyRecord | None]:
    """Return (context to store later, previously stored record to replay)."""
    if not key:
        return None, None
    if not 8 <= len(key) <= 100:
        raise AppError(422, "VALIDATION_ERROR", "Idempotency-Key must be 8-100 characters")
    request_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key
        )
    ).scalar_one_or_none()
    if existing:
        if existing.scope != scope or existing.request_hash != request_hash:
            raise AppError(
                422,
                "IDEMPOTENCY_KEY_REUSED",
                "This Idempotency-Key was already used for a different request",
            )
        return None, existing
    return IdempotencyContext(user_id, key, scope, request_hash), None


def store(db: Session, ctx: IdempotencyContext | None, status_code: int, body: Any) -> None:
    """Add to the caller's transaction; the caller commits."""
    if ctx is None:
        return
    db.add(
        IdempotencyRecord(
            user_id=ctx.user_id,
            key=ctx.key,
            scope=ctx.scope,
            request_hash=ctx.request_hash,
            status_code=status_code,
            response_body=body,
        )
    )
