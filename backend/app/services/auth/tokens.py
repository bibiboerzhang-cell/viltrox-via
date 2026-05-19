"""
services/auth/tokens.py — JWT + email token 管理
"""
from __future__ import annotations

import secrets as _secrets_mod
import time as _t
from datetime import datetime

from app.core.security import make_token, verify_token
from app.db.connection import get_conn


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
    token = _secrets_mod.token_urlsafe(32)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    ttl = _token_ttl(token_type)
    exp = datetime.utcfromtimestamp(_t.time() + ttl).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    conn.execute(
        "INSERT INTO email_tokens (user_id,token,type,created_at,expires_at) VALUES (?,?,?,?,?)",
        (user_id, token, token_type, now, exp),
    )
    conn.commit()
    return token


__all__ = ["make_token", "verify_token", "create_email_token"]
