"""
services/auth/tokens.py — JWT + email token 管理

S-08(2026-09-02):邀请 / 重置 / 验证 token 不再明文入库。``email_tokens.token`` 列存
``sha256$<hex>`` 摘要(:func:`token_digest`),原文只在签发瞬间回给调用方(邮件 /
激活链接)。校验端一律经 :func:`token_lookup_values` 生成 ``token IN (?, ?)`` 的两个
候选值:摘要 + 原文。原文候选只为兼容切换前已签发、尚未过期的明文行(邀请 48h /
重置 1h / 验证 7d 后自然清零);提交值本身若已是 ``sha256$`` 形态则不走原文分支,
避免库泄后拿摘要当 token 直接过闸。
"""
from __future__ import annotations

import hashlib
import secrets as _secrets_mod
import time as _t
from datetime import datetime

from app.core.security import make_token, verify_token
from app.db.connection import get_conn

TOKEN_DIGEST_PREFIX = "sha256$"


def token_digest(raw: str) -> str:
    """Return the at-rest form of a bearer token: ``sha256$<hex>`` of the raw value."""
    return TOKEN_DIGEST_PREFIX + hashlib.sha256(str(raw or "").encode("utf-8")).hexdigest()


def token_lookup_values(raw: str) -> tuple[str, str]:
    """Return ``(digest, legacy)`` for a ``token IN (?, ?)`` lookup.

    ``legacy`` is the raw value only when it is not itself digest-shaped; a submitted
    digest must never match a stored digest via the plaintext branch.
    """
    text = str(raw or "").strip()
    digest = token_digest(text)
    legacy = digest if text.startswith(TOKEN_DIGEST_PREFIX) else text
    return digest, legacy


def _token_ttl(token_type: str) -> int:
    """Return email token TTL in seconds by token type."""
    if token_type == "verify_email":
        return 7 * 86400
    if token_type == "staff_invite":
        return 48 * 3600
    if token_type in {"password_reset", "reset_password"}:
        return 3600
    return 3600


def create_email_token(user_id: int, token_type: str = "verify_email") -> str:
    """Issue a random token; only its sha256 digest is stored. Returns the raw token once."""
    token = _secrets_mod.token_urlsafe(32)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    ttl = _token_ttl(token_type)
    exp = datetime.utcfromtimestamp(_t.time() + ttl).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.execute(
        "INSERT INTO email_tokens (user_id,token,type,created_at,expires_at) VALUES (?,?,?,?,?)",
        (user_id, token_digest(token), token_type, now, exp),
    )
    conn.commit()
    return token


def email_token_expires_at(raw_token: str, token_type: str) -> str:
    """Read back ``expires_at`` for a just-issued raw token (digest lookup)."""
    row = get_conn().execute(
        "SELECT expires_at FROM email_tokens WHERE token IN (?, ?) AND type = ?",
        (*token_lookup_values(raw_token), token_type),
    ).fetchone()
    return str(row["expires_at"] if row else "")


__all__ = [
    "make_token",
    "verify_token",
    "create_email_token",
    "email_token_expires_at",
    "token_digest",
    "token_lookup_values",
]
