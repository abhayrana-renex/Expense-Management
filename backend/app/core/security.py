"""Password hashing (bcrypt), JWT access tokens, opaque refresh tokens, signed URLs."""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


# A real bcrypt hash so failed lookups take as long as real ones (no user enumeration).
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def utcnow() -> datetime:
    return datetime.now(UTC)


def create_access_token(user_id: int, session_id: int) -> tuple[str, int]:
    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now = utcnow()
    payload = {
        "sub": str(user_id),
        "sid": session_id,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["sub", "sid", "exp", "jti"]},
    )
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Return (token, sha256 hash). Only the hash is stored."""
    token = secrets.token_urlsafe(48)
    return token, sha256_hex(token.encode())


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signing_key() -> bytes:
    return hashlib.sha256(b"signed-url:" + settings.JWT_SECRET_KEY.encode()).digest()


def sign_document_url(document_id: int, user_id: int, expires: int) -> str:
    msg = f"{document_id}:{user_id}:{expires}".encode()
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def verify_document_signature(document_id: int, user_id: int, expires: int, sig: str) -> bool:
    if expires < int(utcnow().timestamp()):
        return False
    expected = sign_document_url(document_id, user_id, expires)
    return hmac.compare_digest(expected, sig)
