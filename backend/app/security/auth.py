from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import settings

PBKDF2_PREFIX = "pbkdf2_sha256"
PBKDF2_ITERS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERS)
    digest = dk.hex()
    return f"{PBKDF2_PREFIX}${PBKDF2_ITERS}${salt}${digest}"


def verify_password(password: str, hashed_password: str) -> bool:
    # Совместимость с уже сохраненными записями: если формат не PBKDF2, сравниваем как plain.
    if not hashed_password.startswith(f"{PBKDF2_PREFIX}$"):
        return hmac.compare_digest(password, hashed_password)
    try:
        _, iter_s, salt, digest = hashed_password.split("$", 3)
        iterations = int(iter_s)
    except ValueError:
        return False
    expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return hmac.compare_digest(expected, digest)


def create_access_token(*, subject: str, role: str) -> str:
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
