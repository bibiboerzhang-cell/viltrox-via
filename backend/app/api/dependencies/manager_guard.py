"""api/dependencies/manager_guard.py — 共享管理层校验(owner+manager 闸)。

背景:permissions.py 给 manager+employee 默认 vkpi=write,故
``require_tab("vkpi", "write")`` 对全员恒真;敏感写口需要在 tab 权限之上再加
一道管理层闸。口径 = owner + 管理岗(统一走 ``staff_domain.is_manager_staff``,
不用 vkpi:admin 档)。

两个形态:
- ``require_manager_tab(tab_key, level)`` — 可作 ``Depends`` 的依赖:先过
  ``require_tab``,再校验管理层,通过后返回 staff(全端点闸用)。
- ``require_manager_staff(staff)`` — handler 内显式调用的函数(条件闸用,
  例如 dry_run=False 才收紧)。

判定与 403 文案照抄既有局部实现(vkpi_industry_market / vkpi_dashboard_staff /
vkpi_operations),行为完全一致:非管理层 → 403 "management permission required"。
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.domains.staff import is_manager_staff


def require_manager_staff(staff: dict) -> None:
    """显式管理层校验:非 owner/管理岗 → 403(与三处原局部实现同判定同文案)。"""
    if not is_manager_staff(staff):
        raise HTTPException(status_code=403, detail="management permission required")


def require_manager_tab(tab_key: str = "vkpi", level: str = "write"):
    """Depends 形态:tab 权限 + 管理层双闸,通过后返回 staff(与 require_tab 同用法)。"""
    tab_dep = require_tab(tab_key, level)

    async def dep(staff=Depends(tab_dep)):
        require_manager_staff(staff)
        return staff

    return dep
