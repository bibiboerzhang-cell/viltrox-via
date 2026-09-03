"""
core/security.py — 密码哈希、JWT token、用户认证缓存
"""
from __future__ import annotations

import asyncio
import hashlib as _hashlib
import hmac as _hmac_mod
import base64 as _b64
import time as _time_mod
from typing import Optional
from urllib.parse import parse_qsl as _parse_qsl, urlencode as _urlencode

import jwt as _pyjwt
from fastapi import Request, Response

from app.core.config import IS_PRODUCTION, JWT_EXPIRES_DAYS, JWT_SECRET, JWT_SECRET_PREVIOUS, USER_CACHE_TTL_SEC
from app.core.logging import get_logger
from app.core.passwords import (
    PASSWORD_HASH_VERSION,
    PBKDF2_V1_ITERATIONS,
    PBKDF2_V2_ITERATIONS,
    hash_password,
    needs_password_rehash,
    verify_password,
)
from app.core.staff_avatars import serialize_staff_avatar_url
from app.db.connection import db_connection_sync_reusing_scope, get_conn
from app.services.auth.token_revocation import (
    _ensure_sqlite_column as _ensure_token_version_column,
    AUTH_USER_CACHE_PREFIX,
    TOKEN_VERSION_CLAIM,
    coerce_token_version,
    token_version_matches,
)
from app.services.cache import cache_clear, cache_get, cache_set

logger = get_logger(__name__)

JWT_ISSUER = "viltrox-vos"
JWT_AUDIENCE = "vos-app"
AUTH_COOKIE_NAME = "via_token"
#: 浏览器前端不再持有 JWT(S-02):它在 Authorization 头里送这个占位值,
#: 表示「凭 HttpOnly cookie 认证」。解析时等价于没带头,直接读 cookie。
COOKIE_SESSION_MARKER = "cookie-session"
AUTH_COOKIE_MAX_AGE_SEC = 86400 * int(JWT_EXPIRES_DAYS)
JWT_VERIFY_SECRETS = [JWT_SECRET, *[item for item in JWT_SECRET_PREVIOUS if item and item != JWT_SECRET]]
_ACTIVE_USER_STATUSES = {"active", "approved"}


def user_status_allows_auth(status: object, *, production: bool | None = None) -> bool:
    """Fail closed for disabled accounts while preserving local pending signup."""
    normalized = str(status or "").strip().lower()
    if normalized in _ACTIVE_USER_STATUSES:
        return True
    production_mode = IS_PRODUCTION if production is None else bool(production)
    return normalized == "pending" and not production_mode


# ── Password ──────────────────────────────
# ── JWT Token ──────────────────────────────
def make_token(user_id: int, role: str, token_version: int = 0) -> str:
    """签发登录 JWT;``tv`` = 签发时刻的 users.token_version,校验时必须仍然相等。"""
    now = int(_time_mod.time())
    payload = {
        "uid": user_id,
        "role": role,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + AUTH_COOKIE_MAX_AGE_SEC,
        TOKEN_VERSION_CLAIM: coerce_token_version(token_version),
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
    return f"{AUTH_USER_CACHE_PREFIX}{int(user_id)}:{digest}"


def invalidate_user_cache(user_id: int = None):
    if user_id is None:
        cache_clear(prefix=AUTH_USER_CACHE_PREFIX)
        return
    cache_clear(prefix=f"{AUTH_USER_CACHE_PREFIX}{int(user_id)}:")


def _resolve_request_token(request: Request, *, allow_query_token: bool = False) -> str:
    """Resolve a long-lived login token from a header or HttpOnly auth cookie.

    ``allow_query_token`` remains only as a source-compatible argument for old
    callers. URL query authentication is intentionally ignored: long-lived JWTs
    must never enter access logs, browser history or referrer metadata.

    The browser app never holds the JWT; it sends ``COOKIE_SESSION_MARKER`` as
    its bearer value, which means "authenticate me with the HttpOnly cookie".
    """
    del allow_query_token
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or token == COOKIE_SESSION_MARKER:
        token = request.cookies.get(AUTH_COOKIE_NAME, "")
    return token


def request_uses_cookie_session(request: Request) -> bool:
    """True when the caller authenticates with the HttpOnly cookie, not a real bearer JWT.

    Such callers must never receive a JWT in a response body (S-02): the cookie
    is the only credential transport for the browser app.
    """
    header = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if str(request.query_params.get("session") or "").strip().lower() == "cookie":
        return True
    return header in ("", COOKIE_SESSION_MARKER) and bool(request.cookies.get(AUTH_COOKIE_NAME))


def _load_user_for_auth(user_id: int, cache_key: str):
    uid = int(user_id)
    cached = cache_get(cache_key)
    if cached is not None:
        if isinstance(cached, dict) and user_status_allows_auth(cached.get("status")):
            truthful_cached = dict(cached)
            truthful_cached["avatar_url"] = serialize_staff_avatar_url(cached.get("avatar_url"))
            truthful_cached["avatar_required"] = not bool(truthful_cached["avatar_url"])
            return truthful_cached
        logger.warning(
            "security.inactive_cached_user_rejected uid=%s status=%s",
            uid,
            str(cached.get("status") if isinstance(cached, dict) else "invalid_cache_value"),
        )
        return None

    with db_connection_sync_reusing_scope():
        conn = get_conn()
        try:
            user = conn.execute("""
            SELECT id, email, name, creator_code, status, role, token_version,
                   points_balance, points_pending, points_total,
                   avatar_url, bio, signature,
                   tier_status, trust_score, trust_updated_at
            FROM users WHERE id=?
        """, (uid,)).fetchone()
        except Exception as exc:  # noqa: BLE001 - 仅 SQLite 缺 token_version 列时自愈一次后重试
            if "no such column" not in str(exc).lower():
                raise
            _ensure_token_version_column(conn)
            user = conn.execute("""
            SELECT id, email, name, creator_code, status, role, token_version,
                   points_balance, points_pending, points_total,
                   avatar_url, bio, signature,
                   tier_status, trust_score, trust_updated_at
            FROM users WHERE id=?
        """, (uid,)).fetchone()
        if not user:
            return None
        user_dict = dict(user)
        user_dict["avatar_url"] = serialize_staff_avatar_url(user_dict.get("avatar_url"))
        if not user_status_allows_auth(user_dict.get("status")):
            logger.warning(
                "security.inactive_user_rejected uid=%s status=%s",
                uid,
                str(user_dict.get("status") or ""),
            )
            return None
        try:
            from app.core.permissions import staff_context_for_user, staff_context_is_inactive

            # Keep the base-user and staff lookups in one bounded connection
            # scope.  The admin RBAC middleware and the route dependency may
            # both authenticate the same cold token before its cache entry is
            # visible.  Loading staff after this scope used the outer request
            # connection, then a second auth lookup opened another connection;
            # concurrent dashboard fan-out could therefore self-deadlock a
            # small pool (every request held one connection while waiting for
            # another).
            staff = staff_context_for_user(user_dict)
        except Exception:
            # 2026-07-03 降级陷阱根治:staff 上下文一失败,用户对象就没有 is_owner/permissions,
            # 前端会整体跌成员工视角(scope=self,成员名单都不拉)。此前 DEBUG 级日志不可见、
            # 降级对象还被缓存 30s 反复投毒 —— 现在:WARNING 可见 + 降级对象绝不入缓存,
            # 下一个请求重试完整拼装,自然自愈。
            logger.warning("security.staff_context_attach_failed uid=%s (degraded user NOT cached)", uid, exc_info=True)
            user_dict["token_version"] = coerce_token_version(user_dict.get("token_version"))
            return user_dict

        if staff_context_is_inactive(staff):
            logger.warning("security.inactive_staff_rejected uid=%s", uid)
            return None
        # 吊销版本号随用户行一起读出(整行查询已含该列),不再多一次往返——认证路径的
        # 有界作用域只允许「用户行 + staff」两次读(test_staff_auth_effective_role 钉死)。
        # SQLite 缺列时该键缺失 → 0(旧令牌仍有效),首次吊销时写端自愈加列后即生效;
        # Postgres 由迁移 307 保证。
        user_dict["token_version"] = coerce_token_version(user_dict.get("token_version"))

        auth_role = str(user_dict.get("role") or "")
        effective_role = str(staff.get("role") or auth_role or "readonly")
        user_dict["auth_role"] = auth_role
        user_dict["staff_role"] = effective_role
        user_dict["role"] = effective_role
        user_dict["permissions"] = staff.get("permissions", {})
        user_dict["is_owner"] = bool(staff.get("is_owner"))
        user_dict["staff_id"] = staff.get("id") or staff.get("staff_id") or staff.get("user_id")
        user_dict["employee_code"] = staff.get("employee_code") or user_dict.get("creator_code") or str(user_dict.get("email") or "").split("@")[0]
        user_dict["avatar_required"] = not bool(user_dict.get("avatar_url"))
    cache_set(cache_key, user_dict, ttl=int(USER_CACHE_TTL_SEC))
    return user_dict


def _token_version_rejected(payload: dict, user) -> bool:
    """登出 / 改密 / 踢人之后版本号已前进:旧令牌即使签名有效也一律拒绝。"""
    if user is None or token_version_matches(payload, user.get("token_version")):
        return False
    logger.warning("security.token_version_mismatch uid=%s", int(payload.get("uid") or 0))
    return True


def get_current_user(request: Request, *, allow_query_token: bool = False):
    request_state = getattr(request, "state", None)
    request_cached = getattr(request_state, "vkpi_authenticated_user", None) if request_state is not None else None
    if request_cached is not None:
        return request_cached

    token = _resolve_request_token(request, allow_query_token=allow_query_token)
    if not token:
        return None

    payload = verify_token(token)
    if not payload:
        return None

    uid = payload.get("uid")
    if not uid:
        return None
    user = _load_user_for_auth(int(uid), _user_cache_key(int(uid), token))
    if _token_version_rejected(payload, user):
        return None
    if user is not None and request_state is not None:
        # The global admin RBAC middleware and FastAPI dependencies authenticate
        # the same request independently.  Reuse that verified principal inside
        # the request so a cold/disabled shared cache cannot make the dependency
        # acquire a second pool connection while the middleware still holds the
        # first one.
        request_state.vkpi_authenticated_user = user
    return user


def get_current_user_stream(request: Request):
    """Authenticate one SSE connection with a path-bound, one-time ticket.

    The ticket is carried in an HttpOnly cookie, never in the URL or response
    payload. A user cached on ``request.state`` allows the global admin RBAC
    middleware and the endpoint dependency to share the same consumed ticket.
    """
    if "access_token" in request.query_params:
        # Remove the rejected legacy credential before downstream application
        # access/audit logging sees the query string. Edge logs must be rotated
        # separately because they observe the request before the application.
        pairs = [
            (key, value)
            for key, value in _parse_qsl(
                bytes(request.scope.get("query_string") or b"").decode("utf-8", errors="ignore"),
                keep_blank_values=True,
            )
            if key != "access_token"
        ]
        request.scope["query_string"] = _urlencode(pairs, doseq=True).encode("utf-8")
        for cached_attr in ("_query_params", "_url"):
            if hasattr(request, cached_attr):
                delattr(request, cached_attr)
        return None
    existing = getattr(request.state, "sse_user", None)
    if existing is not None:
        return existing

    from app.services.auth.sse_tickets import (
        SseTicketStoreUnavailable,
        consume_sse_ticket,
        ticket_cookie_name,
    )

    endpoint = str(request.url.path or "")
    try:
        cookie_name = ticket_cookie_name(endpoint)
        ticket = str(request.cookies.get(cookie_name) or "").strip()
        if not ticket:
            return None
        uid = consume_sse_ticket(ticket=ticket, endpoint=endpoint)
    except (ValueError, SseTicketStoreUnavailable):
        return None
    if not uid:
        return None
    user = _load_user_for_auth(int(uid), f"auth:user:{int(uid)}:sse")
    if user is not None:
        request.state.sse_user = user
    return user


async def get_current_user_async(request: Request, *, allow_query_token: bool = False):
    return await asyncio.to_thread(
        get_current_user, request, allow_query_token=allow_query_token
    )


async def get_current_user_stream_async(request: Request):
    return await asyncio.to_thread(get_current_user_stream, request)


def require_admin(request: Request):
    from fastapi import HTTPException
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


async def require_admin_async(request: Request):
    return await asyncio.to_thread(require_admin, request)
