"""
api/dependencies/perms.py — Admin tab permission dependencies.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_user_required, get_user_required_stream
from app.core.permissions import check_system_permission, check_tab_permission, staff_context_for_user


_ADMIN_ACTIONS = {
    "admin",
    "backfill",
    "batch_collect",
    "configure",
    "delete",
    "generate_all",
    "manage",
    "settings",
}

_WRITE_ACTIONS = {
    "analyze",
    "classify",
    "collect",
    "create",
    "generate",
    "import",
    "refresh",
    "run",
    "trigger",
    "update",
    "write",
}


def _permission_to_tab_level(permission_key: str) -> tuple[str, str]:
    """Map dotted P1 permission keys onto the existing tab-level matrix.

    P1 packages use keys such as ``vkpi.comments.collect`` while the current
    app stores coarse tab permissions (``vkpi:read/write/admin``). This mapper
    keeps those routers import-compatible without changing the RBAC schema.
    """
    parts = [part for part in str(permission_key or "").strip().lower().split(".") if part]
    if not parts:
        return "vkpi", "read"
    tab_key = parts[0]
    action = parts[-1]
    if action in _ADMIN_ACTIONS:
        return tab_key, "admin"
    if action in _WRITE_ACTIONS:
        return tab_key, "write"
    return tab_key, "read"


def require_tab(tab_key: str, level: str = "read"):
    async def dep(user=Depends(get_user_required)):
        staff = staff_context_for_user(user)
        if not check_tab_permission(staff, tab_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {tab_key}:{level}")
        return staff

    return dep


def require_tab_stream(tab_key: str, level: str = "read"):
    """require_tab 的 SSE/EventSource 变体:token 可走 ?access_token= 查询参数。

    与 require_tab 权限判定完全一致,只是取 token 时多认 URL 参数一条路
    (浏览器 EventSource 不能带 Bearer header)。仅用于 stream 端点。
    """
    async def dep(user=Depends(get_user_required_stream)):
        staff = staff_context_for_user(user)
        if not check_tab_permission(staff, tab_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {tab_key}:{level}")
        return staff

    return dep


def require_permission(permission_key: str):
    """Compatibility dependency for fine-grained P1 permission keys.

    Use this for new P1 routers only. Existing routers should continue using
    ``require_tab`` or ``require_system_permission`` directly where possible.
    """
    async def dep(user=Depends(get_user_required)):
        staff = staff_context_for_user(user)
        key = str(permission_key or "").strip().lower()
        if key.startswith("system."):
            level = _permission_to_tab_level(key)[1]
            if not check_system_permission(staff, key, level):
                raise HTTPException(status_code=403, detail=f"No permission for {key}:{level}")
            return staff

        tab_key, level = _permission_to_tab_level(key)
        if not check_tab_permission(staff, tab_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {key}:{level}")
        return staff

    return dep


def require_system_permission(permission_key: str, level: str = "read"):
    async def dep(user=Depends(get_user_required)):
        staff = staff_context_for_user(user)
        if not check_system_permission(staff, permission_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {permission_key}:{level}")
        return staff

    return dep
