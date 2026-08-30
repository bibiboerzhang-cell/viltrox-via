"""Pure password hashing primitives shared by auth and DB bootstrap.

This module deliberately has no database or permission imports. Keeping the
bootstrap primitive below authentication orchestration breaks the historic
``permissions -> connection -> security -> permissions`` dependency cycle.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from app.core.logging import get_logger


# Preserve the historical logger identity for malformed-hash diagnostics.
logger = get_logger("app.core.security")

PBKDF2_V1_ITERATIONS = 100_000
PBKDF2_V2_ITERATIONS = 600_000
PASSWORD_HASH_VERSION = "v2"


def _pbkdf2_hex(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations)).hex()


def hash_password(password: str, salt_hex: str | None = None) -> str:
    if salt_hex is None:
        salt_hex = secrets.token_hex(16)
    salt = bytes.fromhex(str(salt_hex))
    hashed = _pbkdf2_hex(password, salt, PBKDF2_V2_ITERATIONS)
    return f"{PASSWORD_HASH_VERSION}:{salt_hex}:{hashed}"


def needs_password_rehash(stored: str) -> bool:
    return not str(stored or "").startswith(f"{PASSWORD_HASH_VERSION}:")


def verify_password(password: str, stored: str) -> bool:
    normalized = str(stored or "").strip()
    if not normalized:
        return False
    try:
        if normalized.startswith(f"{PASSWORD_HASH_VERSION}:"):
            _, salt_hex, _ = normalized.split(":", 2)
            salt = bytes.fromhex(salt_hex)
            expected = (
                f"{PASSWORD_HASH_VERSION}:{salt_hex}:"
                f"{_pbkdf2_hex(password, salt, PBKDF2_V2_ITERATIONS)}"
            )
            return hmac.compare_digest(expected, normalized)
        if ":" in normalized:
            salt_hex, _ = normalized.split(":", 1)
            salt = bytes.fromhex(salt_hex)
            expected = f"{salt_hex}:{_pbkdf2_hex(password, salt, PBKDF2_V1_ITERATIONS)}"
            return hmac.compare_digest(expected, normalized)
    except Exception:
        logger.warning("security.verify_password_failed", exc_info=True)
        return False
    return False


__all__ = [
    "PASSWORD_HASH_VERSION",
    "PBKDF2_V1_ITERATIONS",
    "PBKDF2_V2_ITERATIONS",
    "hash_password",
    "needs_password_rehash",
    "verify_password",
]
