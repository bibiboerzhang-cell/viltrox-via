"""Invite, validation, and email helpers for staff administration."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from app.core.config import SITE_URL
from app.core.logging import get_logger
from app.core.permissions import OWNER_EMAILS
from app.services.auth.email import send_email

logger = get_logger(__name__)


def _load_allowed_domains() -> list[str]:
    """Load staff email domain allowlist from environment."""
    domains = ["viltrox.com"]
    extra = os.environ.get("ALLOWED_EXTERNAL_STAFF_DOMAINS", "").strip()
    if extra:
        for domain in extra.split(","):
            normalized = domain.strip().lower().lstrip("@")
            if normalized and normalized not in domains:
                domains.append(normalized)
    return domains


def _allow_any_external() -> bool:
    """Return whether any external staff email domain is allowed."""
    return os.environ.get("ALLOW_EXTERNAL_STAFF_EMAILS", "").strip().lower() in {"1", "true", "yes"}


def _validate_staff_email(email: str) -> None:
    if "@" not in email:
        raise ValueError("valid email required")
    domain = email.rsplit("@", 1)[-1].lower()
    allowed = _load_allowed_domains()
    if domain in allowed or _allow_any_external():
        return
    raise ValueError(
        f"Email domain '{domain}' not in allowed list. "
        f"Allowed: {', '.join(allowed)}. "
        "Set ALLOW_EXTERNAL_STAFF_EMAILS=1 or "
        "ALLOWED_EXTERNAL_STAFF_DOMAINS=domain1,domain2 to allow others."
    )


def _validate_email_domain(email: str) -> None:
    _validate_staff_email(email)


def _inviter_is_owner(conn, inviter_id: int) -> bool:
    try:
        row = conn.execute(
            """
            SELECT s.is_owner, u.email AS user_email
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.user_id = ?
            ORDER BY s.active DESC, s.id DESC
            LIMIT 1
            """,
            (int(inviter_id),),
        ).fetchone()
    except Exception:
        logger.debug("staff.inviter_owner_lookup_failed", extra={"inviter_id": inviter_id}, exc_info=True)
        return False
    if not row:
        return False
    return bool(row["is_owner"]) or str(row["user_email"] or "").strip().lower() in OWNER_EMAILS


def _strip_admin_levels(permissions: dict[str, str]) -> dict[str, str]:
    return {key: ("write" if value == "admin" else value) for key, value in permissions.items()}


def _permissions_from_invite_body(body: dict) -> dict[str, Any]:
    raw = (
        body.get("permissions")
        or body.get("permissions_override")
        or body.get("permissions_json")
        or {}
    )
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            logger.debug("staff.permissions_json_payload_parse_failed", exc_info=True)
            return {}
    return raw if isinstance(raw, dict) else {}


def _augment_member_invite_status(conn, member: dict[str, Any]) -> None:
    member.setdefault("email", member.get("user_email"))
    member.setdefault("full_name", member.get("user_name"))
    active_token = _get_active_invite_token(conn, int(member.get("user_id") or 0))
    member["invite_token_active"] = bool(active_token)
    member["verification_status"] = _compute_verification_status(member, active_token)
    member["delivery_method"] = _compute_delivery_method(member, active_token)


def _get_active_invite_token(conn, user_id: int) -> dict[str, Any] | None:
    if not user_id:
        return None
    try:
        row = conn.execute(
            """
            SELECT token, expires_at, used_at
            FROM email_tokens
            WHERE user_id = ? AND type = 'staff_invite' AND used_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
    except Exception:
        logger.debug("staff.active_invite_token_lookup_failed", extra={"user_id": user_id}, exc_info=True)
        return None
    if not row:
        return None
    token = dict(row)
    expires_at = str(token.get("expires_at") or "")
    if expires_at and expires_at < _utcnow():
        return None
    return token


def _compute_verification_status(member: dict[str, Any], active_token: dict[str, Any] | None) -> str:
    try:
        if _truthy(member.get("user_email_verified")):
            return "verified"
        if member.get("accepted_at"):
            return "activated"
        if member.get("invited_at"):
            return "pending" if active_token else "expired"
        return "draft"
    except Exception:
        logger.debug("staff.verification_status_compute_failed", extra={"staff_id": member.get("id")}, exc_info=True)
        return "unknown"


def _compute_delivery_method(member: dict[str, Any], active_token: dict[str, Any] | None) -> str:
    if member.get("invited_at") and not member.get("accepted_at"):
        return "pending_invite" if active_token else "unknown"
    if member.get("accepted_at") and _truthy(member.get("user_email_verified")):
        return "email"
    if member.get("accepted_at"):
        return "manual_link"
    return "unknown"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _staff_activation_url(token: str) -> str:
    site_url = os.environ.get("SITE_URL", "http://localhost:5173").strip() or "http://localhost:5173"
    return f"{site_url.rstrip('/')}/activate?token={token}"


def _password_reset_url(token: str) -> str:
    site_url = os.environ.get("SITE_URL", "http://localhost:5173").strip() or "http://localhost:5173"
    return f"{site_url.rstrip('/')}?reset_token={token}"


def _token_hint(token: str) -> str:
    if len(token) <= 8:
        return "..." if token else ""
    return f"{token[:4]}...{token[-4:]}"


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _staff_columns(conn) -> set[str]:
    try:
        rows = conn.execute("PRAGMA table_info(staff)").fetchall()
        return {str(row["name"]) for row in rows}
    except Exception:
        return set()


def _staff_id_for_user(conn, user_id: int) -> int | None:
    try:
        row = conn.execute("SELECT id FROM staff WHERE user_id = ? ORDER BY id DESC LIMIT 1", (int(user_id),)).fetchone()
        return int(row["id"]) if row else None
    except Exception:
        return None


def _send_staff_invite_email(email: str, token: str) -> bool:
    url = f"{SITE_URL.rstrip('/')}/login?staff_invite={token}"
    html = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">'
        '<p style="font-size:20px;font-weight:900;color:#0f172a">Viltrox Marketing</p>'
        '<h2 style="font-size:22px;font-weight:800">You have been invited to Viltrox Marketing</h2>'
        '<p style="color:#5f6673;font-size:14px">Use the secure link below to set your password and accept the invite.</p>'
        f'<a href="{url}" style="display:inline-block;padding:13px 28px;background:#1a1d23;color:#fff;'
        'font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Accept invite</a>'
        '<p style="color:#aaa;font-size:12px;margin-top:24px">Link expires automatically.</p></div>'
    )
    return send_email(email, "Viltrox Marketing invitation", html)


def _send_staff_password_reset_email(email: str, name: str, reset_url: str) -> bool:
    html = (
        '<div style="font-family:Inter,Arial,sans-serif;max-width:560px;margin:0 auto;padding:32px 24px">'
        '<p style="font-size:20px;font-weight:900;color:#0f172a">Viltrox Marketing</p>'
        '<h2 style="font-size:22px;font-weight:800">Reset your password</h2>'
        f'<p style="color:#5f6673;font-size:14px">Hi {name}, use the secure link below to reset your password.</p>'
        f'<a href="{reset_url}" style="display:inline-block;padding:13px 28px;background:#1a1d23;color:#fff;'
        'font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Reset password</a>'
        '<p style="color:#aaa;font-size:12px;margin-top:24px">Link expires in 1 hour.</p></div>'
    )
    return send_email(email, "Reset your Viltrox Marketing password", html)
