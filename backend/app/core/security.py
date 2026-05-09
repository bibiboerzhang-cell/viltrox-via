"""
core/security.py — 密码哈希、JWT token、用户认证缓存
"""
from __future__ import annotations

import asyncio
import hashlib as _hashlib
import hmac as _hmac_mod
import base64 as _b64
import secrets as _secrets_mod
import time as _time_mod
from typing import Optional

import jwt as _pyjwt
from fastapi import Request, Response

from app.core.config import IS_PRODUCTION, JWT_EXPIRES_DAYS, JWT_SECRET, JWT_SECRET_PREVIOUS, USER_CACHE_TTL_SEC
from app.core.logging import get_logger
from app.db.connection import db_connection_sync_scope, get_conn
from app.services.cache import cache_clear, cache_get, cache_set

logger = get_logger(__name__)

PBKDF2_V1_ITERATIONS = 100_000
PBKDF2_V2_ITERATIONS = 600_000
PASSWORD_HASH_VERSION = "v2"
JWT_ISSUER = "viltrox-vos"
JWT_AUDIENCE = "vos-app"
AUTH_COOKIE_NAME = "via_token"
AUTH_COOKIE_MAX_AGE_SEC = 86400 * int(JWT_EXPIRES_DAYS)
JWT_VERIFY_SECRETS = [JWT_SECRET, *[item for item in JWT_SECRET_PREVIOUS if item and item != JWT_SECRET]]


# ── Password ──────────────────────────────
def _pbkdf2_hex(password: str, salt: bytes, iterations: int) -> str:
    return _hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations)).hex()


def hash_password(password: str, salt_hex: str = None) -> str:
    if salt_hex is None:
        salt_hex = _secrets_mod.token_hex(16)
    salt = bytes.fromhex(str(salt_hex))
    h = _pbkdf2_hex(password, salt, PBKDF2_V2_ITERATIONS)
    return f"{PASSWORD_HASH_VERSION}:{salt_hex}:{h}"


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
            expected = f"{PASSWORD_HASH_VERSION}:{salt_hex}:{_pbkdf2_hex(password, salt, PBKDF2_V2_ITERATIONS)}"
            return _hmac_mod.compare_digest(expected, normalized)
        if ":" in normalized:
            salt_hex, _ = normalized.split(":", 1)
            salt = bytes.fromhex(salt_hex)
            expected = f"{salt_hex}:{_pbkdf2_hex(password, salt, PBKDF2_V1_ITERATIONS)}"
            return _hmac_mod.compare_digest(expected, normalized)
    except Exception:
        logger.warning("security.verify_password_failed", exc_info=True)
        return False
    return False


# ── JWT Token ──────────────────────────────
def make_token(user_id: int, role: str) -> str:
    now = int(_time_mod.time())
    payload = {
        "uid": user_id,
        "role": role,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + AUTH_COOKIE_MAX_AGE_SEC,
    }
    return _pyjwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _verify_legacy_token(token: str, secret: str):
    import json as _json

    parts = str(token or "").split(".")
    if len(parts) != 2:
        return None
    payload_b64, sig = parts
    expected = _hmac_mod.new(
        secret.encode(), payload_b64.encode(), _hashlib.sha256
    ).hexdigest()
    if not _hmac_mod.compare_digest(sig, expected):
        return None
    payload = _json.loads(_b64.urlsafe_b64decode(payload_b64 + "==").decode())
    if payload.get("exp", 0) < _time_mod.time():
        return None
    return payload


def verify_token(token: str):
    for secret in JWT_VERIFY_SECRETS:
        try:
            return _pyjwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                audience=JWT_AUDIENCE,
                issuer=JWT_ISSUER,
            )
        except _pyjwt.PyJWTError:
            try:
                legacy_payload = _verify_legacy_token(token, secret)
                if legacy_payload:
                    return legacy_payload
            except Exception:
                continue
        except Exception:
            continue
    logger.warning("security.verify_token_failed")
    return None


def apply_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=str(token or ""),
        httponly=True,
        secure=bool(IS_PRODUCTION),
        samesite="lax",
        max_age=AUTH_COOKIE_MAX_AGE_SEC,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        secure=bool(IS_PRODUCTION),
        samesite="lax",
    )


def _user_cache_key(user_id: int, token: str) -> str:
    digest = _hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"auth:user:{int(user_id)}:{digest}"


def invalidate_user_cache(user_id: int = None):
    if user_id is None:
        cache_clear(prefix="auth:user:")
        return
    cache_clear(prefix=f"auth:user:{int(user_id)}:")


def get_current_user(request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token:
        token = request.cookies.get(AUTH_COOKIE_NAME, "")
    if not token:
        return None

    payload = verify_token(token)
    if not payload:
        return None

    uid = payload.get("uid")
    if not uid:
        return None
    cache_key = _user_cache_key(int(uid), token)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    with db_connection_sync_scope():
        conn = get_conn()
        user = conn.execute("""
            SELECT id, email, name, creator_code, status, role,
                   points_balance, points_pending, points_total,
                   avatar_url, bio, signature,
                   tier_status, trust_score, trust_updated_at
            FROM users WHERE id=?
        """, (uid,)).fetchone()
    if not user:
        return None
    user_dict = dict(user)
    try:
        from app.core.permissions import staff_context_for_user

        staff = staff_context_for_user(user_dict)
        user_dict["permissions"] = staff.get("permissions", {})
        user_dict["is_owner"] = bool(staff.get("is_owner"))
        user_dict["staff_id"] = staff.get("id") or staff.get("staff_id") or staff.get("user_id")
        user_dict["employee_code"] = staff.get("employee_code") or user_dict.get("creator_code") or str(user_dict.get("email") or "").split("@")[0]
        user_dict["avatar_required"] = not bool(str(user_dict.get("avatar_url") or "").strip())
    except Exception:
        logger.debug("security.staff_context_attach_failed", exc_info=True)
    cache_set(cache_key, user_dict, ttl=int(USER_CACHE_TTL_SEC))
    return user_dict


async def get_current_user_async(request: Request):
    return await asyncio.to_thread(get_current_user, request)


def require_admin(request: Request):
    from fastapi import HTTPException
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


async def require_admin_async(request: Request):
    return await asyncio.to_thread(require_admin, request)
