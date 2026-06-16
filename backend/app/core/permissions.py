"""
core/permissions.py — Admin tab permission policy.

R59-FW-PERM 修正:
  - _level_allows 增加 "admin" 层级,实现真正的 read < write < admin 关系
  - default_permissions_for_role: admin role 默认权限改为 "admin" (替代 "write")
  - normalize_permissions: 保留 OWNER_ONLY 降级逻辑,但允许 admin level 存在

层级关系 (修复后):
  none      < 无权限
  read      < 只读
  write     < 读写 (普通员工)
  admin     < 读写 + 管理 (admin role + 信任员工)
  is_owner  < bypass 所有检查 (创始人专属)

The owner account bypasses all tab and system-subpermission checks. Other
staff accounts are governed by the JSON matrix stored on the staff row.
"""
from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn

logger = get_logger(__name__)

_DEFAULT_OWNER_EMAILS = {"jianboz@viltrox.com", "jiangboz@viltrox.com"}
OWNER_EMAILS = {
    email.strip().lower()
    for email in os.environ.get("OWNER_EMAILS", ",".join(sorted(_DEFAULT_OWNER_EMAILS))).split(",")
    if email.strip()
}

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
    "vkpi",
    "activities",
    "insights",
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

_LEVEL_RANK = {"none": 0, "read": 1, "write": 2, "admin": 3}

# 2026-06-16 C7「两态·默认显示」(用户拍板):授权抽屉里非 owner-only 的业务模块,
# 对所有非 owner 成员默认至少「显示」(read)。授权只在 显示(read)/可使用(write)
# 之间选,不再存在「无」——这既消除「某员工被显式存成 none → 入口被 AdminRoute 锁死」
# 的坑(默认人人进得了系统、看得到全部模块),也让 owner 只需把人「升级到可使用」。
# 注:OWNER_ONLY_SYSTEM_KEYS(api_keys/models/members/restart)不在此列,维持按需授权 +
# 写级降级;这些 read 仅是「可见」,真正敏感写操作仍 owner 专属。
DEFAULT_VISIBLE_KEYS = (
    "overview",
    "kol_ops",
    "vkpi",
    "activities",
    "analytics",
    "insights",
    "products",
    "runtime",
    "system",
    "system.usage",
)


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
    """
    R59-FW-PERM: 真正的权限层级关系.
    
    层级:
      none < read < write < admin
    
    规则:
      level=read:  user_value 是 read/write/admin 都通过
      level=write: user_value 是 write/admin 通过 (read 拒绝)
      level=admin: user_value 必须是 admin (read/write 都拒绝)
    
    历史 bug (修复前):
      level=admin 时 fall through 到 == "write"
      → admin level 实际等价于 write level
      → 25 个 require_tab(*, "admin") 接口实际只是 write gate
    """
    normalized = str(value or "none").lower()
    if level == "read":
        return normalized in {"read", "write", "admin"}
    if level == "write":
        return normalized in {"write", "admin"}
    if level == "admin":
        return normalized == "admin"
    return False


def is_owner(staff: dict[str, Any] | None) -> bool:
    if not staff:
        return False
    if int(staff.get("is_owner") or 0) == 1:
        return True
    return str(staff.get("email") or staff.get("user_email") or "").strip().lower() in OWNER_EMAILS


def default_permissions_for_role(role: str, *, owner: bool = False) -> dict[str, str]:
    """
    R59-FW-PERM 修正:
      - owner 默认所有 key 是 "admin" (而不是 "write")
      - admin role 默认 TAB key 是 "admin" (而不是 "write")
      - manager / employee / viewer 保持原行为不变
    
    注: OWNER_ONLY_SYSTEM_KEYS 仍受 normalize_permissions 限制,
       admin role 即使默认 "admin" 也会被降级到 "read"/"none"
    """
    if owner:
        return {key: "admin" for key in [*TAB_PERMISSION_KEYS, *SYSTEM_PERMISSION_KEYS]}
    role_key = str(role or "readonly").lower()
    if role_key == "admin":
        # R59-FW-PERM: admin role 默认 "admin" 级别 (历史是 "write")
        perms = {key: "admin" for key in TAB_PERMISSION_KEYS}
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
    if role_key in {"manager", "lead", "marketing_lead", "marketing-manager", "marketing_manager"}:
        perms = {key: "none" for key in TAB_PERMISSION_KEYS}
        perms.update({"vkpi": "write", "kol_ops": "write", "insights": "read", "activities": "read"})
        return {
            **perms,
            "system.api_keys": "read",
            "system.usage": "read",
            "system.models": "read",
            "system.restart": "none",
            "system.members": "none",
        }
    if role_key in {"employee", "staff", "operator", "ops", "operations", "marketing", "analyst"}:
        perms = {key: "none" for key in TAB_PERMISSION_KEYS}
        perms.update({"vkpi": "write", "kol_ops": "read"})
        return {
            **perms,
            "system.api_keys": "none",
            "system.usage": "none",
            "system.models": "none",
            "system.restart": "none",
            "system.members": "none",
        }
    if role_key in {"viewer", "readonly", "read_only"}:
        perms = {key: "none" for key in TAB_PERMISSION_KEYS}
        perms["vkpi"] = "read"
        return {
            **perms,
            "system.api_keys": "none",
            "system.usage": "none",
            "system.models": "none",
            "system.restart": "none",
            "system.members": "none",
        }
    return {
        **{key: "none" for key in TAB_PERMISSION_KEYS},
        "system.api_keys": "none",
        "system.usage": "none",
        "system.models": "none",
        "system.restart": "none",
        "system.members": "none",
    }


def normalize_permissions(raw: Any, role: str = "readonly", *, owner: bool = False) -> dict[str, str]:
    """
    R59-FW-PERM:
      - 支持 admin level 在 OWNER_ONLY_SYSTEM_KEYS 上的降级
      - admin role 在 OWNER_ONLY 字段也不能拥有 admin/write
        - api_keys / models: 降到 read
        - restart / members:  降到 none
    """
    merged = default_permissions_for_role(role, owner=owner)
    if not owner:
        merged.update(_parse_permissions(raw))
        for key in OWNER_ONLY_SYSTEM_KEYS:
            current = merged.get(key, "none")
            # R59-FW-PERM: write 或 admin 都触发降级
            if current in {"write", "admin"}:
                merged[key] = "read" if key in {"system.api_keys", "system.models"} else "none"
        # C7「两态·默认显示」:业务模块对非 owner 至少 read(none/缺省/非法值 → read)。
        for key in DEFAULT_VISIBLE_KEYS:
            if _LEVEL_RANK.get(str(merged.get(key, "none")).lower(), 0) < 1:
                merged[key] = "read"
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
    if key in OWNER_ONLY_SYSTEM_KEYS and level in {"write", "admin"}:
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
