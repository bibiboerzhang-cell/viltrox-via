"""
api/dependencies/perms.py — Admin tab permission dependencies.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.api.dependencies.auth import get_admin
from app.core.permissions import check_system_permission, check_tab_permission, staff_context_for_user


def require_tab(tab_key: str, level: str = "read"):
    async def dep(admin=Depends(get_admin)):
        staff = staff_context_for_user(admin)
        if not check_tab_permission(staff, tab_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {tab_key}:{level}")
        return staff

    return dep


def require_system_permission(permission_key: str, level: str = "read"):
    async def dep(admin=Depends(get_admin)):
        staff = staff_context_for_user(admin)
        if not check_system_permission(staff, permission_key, level):
            raise HTTPException(status_code=403, detail=f"No permission for {permission_key}:{level}")
        return staff

    return dep
