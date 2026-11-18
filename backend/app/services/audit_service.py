"""Hash-chained audit log. Always written in the caller's transaction (never commits)."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.context import get_request_context
from app.core.security import utcnow
from app.models import AuditLog, User

GENESIS_HASH = "0" * 64
# Serialises audit writers so the hash chain never forks. Held until the tx ends.
_AUDIT_LOCK_KEY = 815_001


def _canonical(row: dict[str, Any]) -> bytes:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()


def compute_hash(prev_hash: str, entry: AuditLog) -> str:
    payload = {
        # Normalise to UTC: the DB may return the value in its session time zone.
        "occurred_at": entry.occurred_at.astimezone(UTC).isoformat(),
        "actor_id": entry.actor_id,
        "actor_username": entry.actor_username,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "request_id": entry.request_id,
        "ip_address": entry.ip_address,
        "user_agent": entry.user_agent,
        "details": entry.details,
    }
    return hashlib.sha256(prev_hash.encode() + _canonical(payload)).hexdigest()


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    actor: User | None = None,
    details: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> AuditLog:
    """Add an audit row to the current transaction. The caller commits."""
    ctx = get_request_context()
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
    prev = db.execute(select(AuditLog.row_hash).order_by(AuditLog.id.desc()).limit(1)).scalar()
    entry = AuditLog(
        occurred_at=occurred_at or utcnow(),
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        request_id=ctx.request_id,
        ip_address=ctx.ip_address,
        user_agent=(ctx.user_agent or "")[:255] or None,
        details=json.loads(_canonical(details)) if details else None,
        prev_hash=prev or GENESIS_HASH,
    )
    entry.row_hash = compute_hash(entry.prev_hash, entry)
    db.add(entry)
    db.flush()
    return entry


def verify_chain(db: Session, batch_size: int = 1000) -> dict[str, Any]:
    """Recompute every hash in id order. Returns the first broken row, if any."""
    prev_hash = GENESIS_HASH
    checked = 0
    last_id = 0
    while True:
        rows = (
            db.execute(
                select(AuditLog)
                .where(AuditLog.id > last_id)
                .order_by(AuditLog.id)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        for row in rows:
            if row.prev_hash != prev_hash or compute_hash(row.prev_hash, row) != row.row_hash:
                return {"valid": False, "checked": checked, "first_broken_id": row.id}
            prev_hash = row.row_hash
            checked += 1
            last_id = row.id
    return {"valid": True, "checked": checked, "first_broken_id": None}


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Only the changed fields, as {field: [old, new]}."""
    return {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
