"""
core/permissions.py — Admin tab permission policy.

The owner account bypasses all tab and system-subpermission checks. Other
staff accounts are governed by the JSON matrix stored on the staff row.
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

OWNER_EMAILS = {"jianboz@viltrox.com"}

TAB_PERMISSION_KEYS = [
    "overview",
    "operations",
    "creators",
    "products",
    "analytics",
    "student",
    "via",
    "command",
    "runtime",
    "intelligence",
    "deepsight",
    "system",
    "kol_ops",
]

SYSTEM_PERMISSION_KEYS = [
    "system.api_keys",
    "system.usage",
    "system.models",
    "system.restart",
    "system.members",
]

OWNER_ONLY_SYSTEM_KEYS = {
    "system.api_keys",
    "system.models",
    "system.restart",
    "system.members",
}


def _parse_permissions(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except json.JSONDecodeError:
            logger.debug("permissions.parse_failed")
            return {}
    return {}


def _level_allows(value: str, level: str) -> bool:
    normalized = str(value or "none").lower()
    if level == "read":
        return normalized in {"read", "write"}
    return normalized == "write"


def is_owner(staff: dict[str, Any] | None) -> bool:
    if not staff:
        return False
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return str(staff.get("email") or staff.get("user_email") or "").strip().lower() in OWNER_EMAILS


def default_permissions_for_role(role: str, *, owner: bool = False) -> dict[str, str]:
    if owner:
        return {key: "write" for key in [*TAB_PERMISSION_KEYS, *SYSTEM_PERMISSION_KEYS]}
    role_key = str(role or "readonly").lower()
    if role_key == "admin":
        perms = {key: "write" for key in TAB_PERMISSION_KEYS}
        perms.update(
            {
                "system.api_keys": "read",
                "system.usage": "read",
                "system.models": "read",
                "system.restart": "none",
                "system.members": "none",
            }
        )
        return perms
    return {
        **{key: "read" for key in TAB_PERMISSION_KEYS},
        "system.api_keys": "read",
        "system.usage": "read",
        "system.models": "read",
        "system.restart": "none",
        "system.members": "none",
    }


def normalize_permissions(raw: Any, role: str = "readonly", *, owner: bool = False) -> dict[str, str]:
    merged = default_permissions_for_role(role, owner=owner)
    if not owner:
        merged.update(_parse_permissions(raw))
        for key in OWNER_ONLY_SYSTEM_KEYS:
            if merged.get(key) == "write":
                merged[key] = "read" if key in {"system.api_keys", "system.models"} else "none"
    return merged


def check_tab_permission(staff: dict[str, Any], tab_key: str, level: str = "read") -> bool:
    if is_owner(staff):
        return True
    tab = str(tab_key or "")
    if tab not in TAB_PERMISSION_KEYS:
        return False
    perms = normalize_permissions(
        staff.get("permissions_json") or staff.get("permissions"),
        str(staff.get("role") or "readonly"),
        owner=False,
    )
    return _level_allows(perms.get(tab, "none"), level)


def check_system_permission(staff: dict[str, Any], permission_key: str, level: str = "read") -> bool:
    if is_owner(staff):
        return True
    key = str(permission_key or "")
    if key not in SYSTEM_PERMISSION_KEYS:
        return False
    if key in OWNER_ONLY_SYSTEM_KEYS and level == "write":
        return False
    perms = normalize_permissions(
        staff.get("permissions_json") or staff.get("permissions"),
        str(staff.get("role") or "readonly"),
        owner=False,
    )
    return _level_allows(perms.get(key, "none"), level)


def staff_context_for_user(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {}
    email = str(user.get("email") or "").strip().lower()
    owner = email in OWNER_EMAILS
    base = {
        "user_id": int(user.get("id") or 0),
        "email": email,
        "role": str(user.get("role") or "readonly"),
        "is_owner": 1 if owner else 0,
        "permissions_json": "{}",
    }
    try:
        conn = get_conn()
        row = conn.execute(
            """
            SELECT s.*, u.email AS email, u.name AS name
            FROM staff s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.user_id = ?
            ORDER BY s.active DESC, s.id DESC
            LIMIT 1
            """,
            (int(user.get("id") or 0),),
        ).fetchone()
        if row:
            base.update(dict(row))
    except Exception:
        logger.debug("permissions.staff_context_lookup_failed", exc_info=True)
    owner = owner or is_owner(base)
    permissions = normalize_permissions(
        base.get("permissions_json"),
        str(base.get("role") or user.get("role") or "readonly"),
        owner=owner,
    )
    return {
        **base,
        "is_owner": 1 if owner else 0,
        "permissions": permissions,
    }
