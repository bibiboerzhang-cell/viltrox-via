"""
api/dependencies/perms.py — Admin tab permission dependencies.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_user_required
from app.core.permissions import check_system_permission, check_tab_permission, staff_context_for_user


def require_tab(tab_key: str, level: str = "read"):
    async def dep(user=Depends(get_user_required)):
        staff = staff_context_for_user(user)
        if not check_tab_permission(staff, tab_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {tab_key}:{level}")
        return staff

    return dep


def require_system_permission(permission_key: str, level: str = "read"):
    async def dep(user=Depends(get_user_required)):
        staff = staff_context_for_user(user)
        if not check_system_permission(staff, permission_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {permission_key}:{level}")
        return staff

    return dep
