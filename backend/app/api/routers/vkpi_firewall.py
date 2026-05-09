"""backend/app/api/routers/vkpi_firewall.py

R59-FW-PERM: 撤回本地 require_vkpi_admin helper,改回标准 require_tab.

为什么撤回:
  R59 v3.2 加了本地 require_vkpi_admin helper 是因为 _level_allows
  的 admin level fall through 到 write,导致 require_tab(*, "admin") 失效.
  
  R59-FW-PERM 修复了 _level_allows,admin level 真生效.
  现在可以撤回本地 helper,改回 require_tab("vkpi", "admin").
  
  好处:
    - 全项目权限风格统一 (与 vkpi_settings.py 等一致)
    - 不需要每个 router 维护自己的 admin gate
    - 后续修改权限模型只动 permissions.py

保留:
  - target_id_extractor 兼容批量 payload (R59 v3.1 修正)
  - payload 形状自动包装 (R59 v3 修正)
  - audit_decorator 集成

新增 endpoint (vs R59 v3.2 不变):
  GET  /api/admin/vkpi/settings/firewall/control-status
  POST /api/admin/vkpi/settings/firewall/feature-flags
  POST /api/admin/vkpi/settings/firewall/platform/{platform}
  POST /api/admin/vkpi/settings/firewall/budget/{key}
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.services.vkpi import platform_crawl_settings
from app.services.vkpi.audit_decorator import audit_action


router = APIRouter(prefix="/api/admin/vkpi/settings/firewall", tags=["vkpi-firewall"])


# ─── target_id 提取 helper (兼容单条 / 批量) ─────


def _extract_first_key(
    body: Any,
    *,
    single_key: str,
    batch_collection: str,
) -> str:
    """
    从 body 提取第一个 target key.
    
    优先级:
      1. body.{single_key}                       (单条 payload)
      2. body.{batch_collection}[0].{single_key} (批量 payload)
      3. ""
    """
    if not isinstance(body, dict):
        return ""
    
    direct = body.get(single_key)
    if direct:
        return str(direct).strip()
    
    items = body.get(batch_collection)
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            return str(first.get(single_key) or "").strip()
    
    return ""


def _count_payload_items(body: Any, batch_collection: str, single_key: str) -> int:
    """计算 payload 里包含多少 item"""
    if not isinstance(body, dict):
        return 0
    items = body.get(batch_collection)
    if isinstance(items, list):
        return len(items)
    if body.get(single_key):
        return 1
    return 0


# ─── Read (vkpi:read 即可) ──────────────────────


@router.get("/control-status")
def control_status(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """
    获取防火墙全景:feature_flags + platform_settings + budget_settings.
    """
    return {
        "feature_flags": platform_crawl_settings.feature_flags(),
        "platform_settings": platform_crawl_settings.platform_settings(),
        "budget_settings": platform_crawl_settings.budget_settings(),
    }


# ─── Write (R59-FW-PERM: 改回 require_tab admin) ────────


@router.post("/feature-flags")
@audit_action(
    action_type="firewall_feature_flag_toggle",
    target_type="feature_flag",
    target_id_extractor=lambda result, kwargs: _extract_first_key(
        kwargs.get("body"),
        single_key="flag_key",
        batch_collection="flags",
    ),
    detail_extractor=lambda result, kwargs: (
        f"toggled {_count_payload_items(kwargs.get('body'), 'flags', 'flag_key')} flags"
    ),
)
def update_feature_flag(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict:
    """切换 feature flag (vkpi:admin 权限)"""
    if isinstance(body.get("flags"), list):
        return platform_crawl_settings.update_feature_flags(body, staff=staff)
    
    flag_key = str(body.get("flag_key") or "").strip()
    if not flag_key:
        raise HTTPException(status_code=400, detail="flag_key required")
    
    item = {"flag_key": flag_key, "enabled": bool(body.get("enabled"))}
    if "description" in body:
        item["description"] = str(body["description"] or "")
    if "metadata" in body:
        item["metadata"] = body["metadata"]
    
    return platform_crawl_settings.update_feature_flags(
        {"flags": [item]},
        staff=staff,
    )


@router.post("/platform/{platform}")
@audit_action(
    action_type="firewall_platform_update",
    target_type="platform_settings",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("platform", "")),
    detail_extractor=lambda result, kwargs: (
        f"updated platform={kwargs.get('platform')} keys={list((kwargs.get('body') or {}).keys())}"
    ),
)
def update_platform(
    platform: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict:
    """更新平台抓取设置 (vkpi:admin 权限)"""
    if not platform:
        raise HTTPException(status_code=400, detail="platform required")
    
    if isinstance(body.get("platforms"), list):
        return platform_crawl_settings.update_platform_settings(body, staff=staff)
    
    item = dict(body or {})
    item["platform"] = platform
    
    return platform_crawl_settings.update_platform_settings(
        {"platforms": [item]},
        staff=staff,
    )


@router.post("/budget/{budget_key}")
@audit_action(
    action_type="firewall_budget_update",
    target_type="budget_settings",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("budget_key", "")),
    detail_extractor=lambda result, kwargs: (
        f"updated budget={kwargs.get('budget_key')} keys={list((kwargs.get('body') or {}).keys())}"
    ),
)
def update_budget(
    budget_key: str,
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict:
    """更新预算设置 (vkpi:admin 权限)"""
    if not budget_key:
        raise HTTPException(status_code=400, detail="budget_key required")
    
    if isinstance(body.get("budgets"), list):
        return platform_crawl_settings.update_budget_settings(body, staff=staff)
    
    item = dict(body or {})
    item["budget_key"] = budget_key
    
    return platform_crawl_settings.update_budget_settings(
        {"budgets": [item]},
        staff=staff,
    )
