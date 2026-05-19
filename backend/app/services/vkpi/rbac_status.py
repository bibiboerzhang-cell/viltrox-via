"""P12 read-only RBAC and staff invite status snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.permissions import check_system_permission, check_tab_permission, is_owner, normalize_permissions
from app.db.connection import get_conn


SCENARIO = "p12_rbac_status"


def _safe_fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        rows = get_conn().execute(sql, params).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _safe_fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = _safe_fetchall(sql, params)
    return rows[0] if rows else {}


def _count_admin_audit_actions() -> dict[str, int]:
    rows = _safe_fetchall(
        """
        SELECT COALESCE(NULLIF(action, ''), 'unknown') AS action, COUNT(*) AS count
        FROM admin_audit_log
        WHERE action IN (
          'invite_staff',
          'resend_staff_invite',
          'update_staff',
          'update_staff_permissions',
          'suspend_staff',
          'reactivate_staff',
          'delete_staff'
        )
        GROUP BY COALESCE(NULLIF(action, ''), 'unknown')
        ORDER BY count DESC, action
        """
    )
    return {str(row.get("action") or "unknown"): int(row.get("count") or 0) for row in rows}


def _invite_token_status() -> dict[str, int]:
    row = _safe_fetchone(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN used_at IS NULL AND expires_at >= CURRENT_TIMESTAMP THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN used_at IS NULL AND expires_at < CURRENT_TIMESTAMP THEN 1 ELSE 0 END) AS expired_unused,
          SUM(CASE WHEN used_at IS NOT NULL THEN 1 ELSE 0 END) AS used
        FROM email_tokens
        WHERE type = 'staff_invite'
        """
    )
    return {
        "total": int(row.get("total") or 0),
        "active": int(row.get("active") or 0),
        "expired_unused": int(row.get("expired_unused") or 0),
        "used": int(row.get("used") or 0),
    }


def _staff_rows() -> list[dict[str, Any]]:
    return _safe_fetchall(
        """
        SELECT
          s.id,
          s.user_id,
          s.role,
          s.permissions_json,
          s.active,
          s.accepted_at,
          s.invited_at,
          s.suspended_at,
          s.suspended_reason,
          COALESCE(s.is_owner, 0) AS is_owner,
          COALESCE(s.email_domain_verified, 0) AS email_domain_verified,
          u.email AS email,
          u.name AS name,
          u.status AS user_status,
          u.email_verified AS user_email_verified,
          u.last_login AS user_last_login
        FROM staff s
        LEFT JOIN users u ON u.id = s.user_id
        ORDER BY s.active DESC, s.id
        """
    )


def _level_distribution(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = {"admin": 0, "write": 0, "read": 0, "none": 0}
    for row in rows:
        perms = row.get("normalized_permissions") or {}
        level = str(perms.get(key) or "none").lower()
        if level not in counts:
            level = "none"
        counts[level] += 1
    return counts


def _role_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        role = str(row.get("role") or "readonly").strip().lower() or "readonly"
        counts[role] = counts.get(role, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _staff_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = len(rows)
    active = sum(1 for row in rows if int(row.get("active") or 0) == 1)
    accepted = sum(1 for row in rows if row.get("accepted_at"))
    pending_invite = sum(1 for row in rows if int(row.get("active") or 0) == 1 and not row.get("accepted_at"))
    suspended = sum(1 for row in rows if int(row.get("active") or 0) != 1 or bool(row.get("suspended_at")))
    owners = sum(1 for row in rows if bool(row.get("computed_owner")))
    active_owners = sum(
        1
        for row in rows
        if bool(row.get("computed_owner")) and int(row.get("active") or 0) == 1
    )
    missing_email = sum(1 for row in rows if not str(row.get("email") or "").strip())
    domain_verified = sum(1 for row in rows if int(row.get("email_domain_verified") or 0) == 1)
    return {
        "total": total,
        "active": active,
        "accepted": accepted,
        "pending_invite": pending_invite,
        "suspended": suspended,
        "owners": owners,
        "active_owners": active_owners,
        "missing_email": missing_email,
        "domain_verified": domain_verified,
    }


def _normalize_staff_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        owner = is_owner(row)
        permissions = normalize_permissions(
            row.get("permissions_json"),
            str(row.get("role") or "readonly"),
            owner=owner,
        )
        normalized.append(
            {
                **row,
                "computed_owner": owner,
                "normalized_permissions": permissions,
                "can_read_vkpi": check_tab_permission(row, "vkpi", "read"),
                "can_write_vkpi": check_tab_permission(row, "vkpi", "write"),
                "can_admin_vkpi": check_tab_permission(row, "vkpi", "admin"),
                "can_manage_members": check_system_permission(row, "system.members", "write"),
            }
        )
    return normalized


def _permission_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_rows = [row for row in rows if int(row.get("active") or 0) == 1]
    return {
        "vkpi_permissions": _level_distribution(rows, "vkpi"),
        "active_vkpi_permissions": _level_distribution(active_rows, "vkpi"),
        "system_members_permissions": _level_distribution(rows, "system.members"),
        "active_system_members_permissions": _level_distribution(active_rows, "system.members"),
        "active_can_read_vkpi": sum(1 for row in active_rows if row.get("can_read_vkpi")),
        "active_can_write_vkpi": sum(1 for row in active_rows if row.get("can_write_vkpi")),
        "active_can_admin_vkpi": sum(1 for row in active_rows if row.get("can_admin_vkpi")),
        "active_can_manage_members": sum(1 for row in active_rows if row.get("can_manage_members")),
    }


def _build_gaps(staff: dict[str, int], permissions: dict[str, Any], invite_tokens: dict[str, int]) -> list[str]:
    gaps = []
    if staff["total"] == 0:
        gaps.append("staff_table_empty")
    if staff["active_owners"] == 0:
        gaps.append("no_active_owner_staff")
    if permissions["active_can_read_vkpi"] == 0:
        gaps.append("no_active_vkpi_reader")
    if permissions["active_can_admin_vkpi"] == 0:
        gaps.append("no_active_vkpi_admin")
    if staff["pending_invite"] > 0:
        gaps.append("pending_staff_invites")
    if invite_tokens["expired_unused"] > 0:
        gaps.append("expired_unused_staff_invite_tokens")
    if staff["missing_email"] > 0:
        gaps.append("staff_rows_missing_user_email")
    return gaps


def build_rbac_status(*, include_staff: bool = False, json_out: str = "", md_out: str = "") -> dict[str, Any]:
    rows = _normalize_staff_rows(_staff_rows())
    staff = _staff_summary(rows)
    permissions = _permission_summary(rows)
    invite_tokens = _invite_token_status()
    payload: dict[str, Any] = {
        "scenario": SCENARIO,
        "provider_calls": False,
        "write_db": False,
        "staff": staff,
        "roles": _role_distribution(rows),
        "vkpi_permissions": permissions["vkpi_permissions"],
        "active_vkpi_permissions": permissions["active_vkpi_permissions"],
        "system_members_permissions": permissions["system_members_permissions"],
        "active_system_members_permissions": permissions["active_system_members_permissions"],
        "effective_access": {
            "active_can_read_vkpi": permissions["active_can_read_vkpi"],
            "active_can_write_vkpi": permissions["active_can_write_vkpi"],
            "active_can_admin_vkpi": permissions["active_can_admin_vkpi"],
            "active_can_manage_members": permissions["active_can_manage_members"],
        },
        "invite_tokens": invite_tokens,
        "staff_audit_actions": _count_admin_audit_actions(),
        "gaps": _build_gaps(staff, permissions, invite_tokens),
    }
    if include_staff:
        payload["staff_rows"] = [_public_staff_row(row) for row in rows]
    markdown = format_rbac_status(payload)
    payload["markdown"] = markdown
    if json_out:
        Path(json_out).write_text(
            json.dumps({key: value for key, value in payload.items() if key != "markdown"}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    if md_out:
        Path(md_out).write_text(markdown, encoding="utf-8")
    return payload


def _public_staff_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "email": row.get("email") or "",
        "name": row.get("name") or "",
        "role": row.get("role") or "readonly",
        "active": bool(int(row.get("active") or 0)),
        "accepted": bool(row.get("accepted_at")),
        "suspended": bool(row.get("suspended_at") or int(row.get("active") or 0) != 1),
        "is_owner": bool(row.get("computed_owner")),
        "vkpi": (row.get("normalized_permissions") or {}).get("vkpi", "none"),
        "system_members": (row.get("normalized_permissions") or {}).get("system.members", "none"),
    }


def format_rbac_status(payload: dict[str, Any]) -> str:
    lines = [
        "# P12 RBAC Status",
        "",
        "```text",
        f"scenario={payload.get('scenario', '')}",
        f"provider_calls={str(bool(payload.get('provider_calls'))).lower()}",
        f"write_db={str(bool(payload.get('write_db'))).lower()}",
    ]
    for key, value in sorted((payload.get("staff") or {}).items()):
        lines.append(f"staff.{key}={int(value or 0)}")
    for key, value in sorted((payload.get("effective_access") or {}).items()):
        lines.append(f"access.{key}={int(value or 0)}")
    for key, value in sorted((payload.get("invite_tokens") or {}).items()):
        lines.append(f"invite_tokens.{key}={int(value or 0)}")
    lines.append("```")
    lines.extend(["", "## Role Distribution", ""])
    for key, value in (payload.get("roles") or {}).items():
        lines.append(f"- {key}: {value}")
    if not payload.get("roles"):
        lines.append("- none")
    lines.extend(["", "## V-KPI Permissions", ""])
    for key, value in (payload.get("active_vkpi_permissions") or {}).items():
        lines.append(f"- active {key}: {value}")
    lines.extend(["", "## Gaps", ""])
    for gap in payload.get("gaps") or []:
        lines.append(f"- {gap}")
    if not payload.get("gaps"):
        lines.append("- none")
    return "\n".join(lines).rstrip() + "\n"
