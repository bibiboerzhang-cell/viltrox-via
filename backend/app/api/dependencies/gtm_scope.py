"""Fail-closed tenant gate for legacy-global GTM tables.

The current GTM evidence and outcome tables predate organization scoping and
belong to the original Viltrox workspace (organization 1).  Until those tables
carry and enforce ``organization_id``, every route that reads or mutates them
must stop before cache, builder, database, or management-policy work for any
other/unresolved tenant.
"""
from __future__ import annotations

from typing import Any

from app.api.dependencies.legacy_scope import legacy_system_admin_scope_guard


LEGACY_GTM_ORGANIZATION_ID = 1

_SURFACE_REASONS = {
    "summary": (
        "GTM Summary 底层市场、产品和学习聚合尚未完成 organization_id 收窄；"
        "为防止返回默认工作区数据，本租户暂不执行聚合。"
    ),
    "preview": (
        "GTM Preview 底层产品、市场、Dealer 与学习证据尚未完成 organization_id 收窄；"
        "为防止返回默认工作区数据，本租户暂不执行预览。"
    ),
}


def legacy_gtm_scope_guard(
    staff: dict[str, Any] | None,
    *,
    surface: str = "legacy-global GTM",
) -> dict[str, Any] | None:
    """Return ``None`` only for an explicitly resolved organization 1.

    A cache partition or role check cannot make an unscoped source table
    tenant-safe.  Requiring both the explicit organization id and the resolved
    membership status prevents missing/ambiguous auth context from silently
    falling back to the original workspace.
    """

    reason = _SURFACE_REASONS.get(
        surface,
        (
            f"{surface} 仍依赖默认工作区的 legacy-global 数据表，尚未完成 organization_id 收窄；"
            "为防止跨租户读取或写入，本租户请求已安全停止。"
        ),
    )
    return legacy_system_admin_scope_guard(staff, surface=surface, reason=reason)


__all__ = ["LEGACY_GTM_ORGANIZATION_ID", "legacy_gtm_scope_guard"]
