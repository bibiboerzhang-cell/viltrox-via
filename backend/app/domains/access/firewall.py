"""backend/app/domains/access/firewall.py

R59: 平台级防火墙装饰器

把已有的 platform_crawl_settings 检查包装成统一装饰器,
避免散点检查 (provider_gate / 手动调用) 不一致。

设计:
  - @firewall_check(platform="instagram", action="crawl")
    自动检查:
      1. feature_flags 里对应 flag 是否开启 (可选)
      2. platform_settings.crawl_enabled
      3. platform_settings.monthly_budget_usd > 0
    任一不通过 → HTTPException(503, detail=...)
  
  - 装饰器只做闸门,不改业务逻辑
  - 失败信息明确告诉用户"哪一关没过"

使用示例:
    from app.domains.access.firewall import firewall_check
    
    @router.post("/kol_pool/import")
    @firewall_check(platform="instagram", action="import")
    def import_kol(payload: dict, staff=Depends(require_tab("vkpi", "write"))):
        return kol_pool.import_items(...)
"""
from __future__ import annotations

import functools
from typing import Any, Callable

from fastapi import HTTPException

import importlib

platform_crawl_settings = importlib.import_module("app.domains.settings.platform_crawl")


# ─── 公开装饰器 ─────────────────────────────────────────


def firewall_check(
    *,
    platform: str = "",
    action: str = "",
    feature_flag: str = "",
    require_budget: bool = True,
    bypass_param: str = "force",
) -> Callable:
    """
    装饰器: 在调用 endpoint 前检查防火墙状态.
    
    参数:
      platform:       平台名 (instagram / youtube / tiktok / xiaohongshu / etc)
                      为空时跳过 platform 检查
      action:         动作描述 (用于错误信息,例如 "crawl" / "import" / "scan")
      feature_flag:   要检查的 feature_flag key (可选,例如 "vkpi.apify_import")
      require_budget: 是否要求 monthly_budget_usd > 0 (默认 True)
      bypass_param:   request body 里的 bypass 参数名 (默认 "force",仅 owner 可用)
    
    抛出:
      HTTPException(503): 防火墙拒绝,detail 说明哪一关没过
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _check_firewall(
                platform=platform,
                action=action,
                feature_flag=feature_flag,
                require_budget=require_budget,
                bypass_param=bypass_param,
                kwargs=kwargs,
            )
            result = func(*args, **kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _check_firewall(
                platform=platform,
                action=action,
                feature_flag=feature_flag,
                require_budget=require_budget,
                bypass_param=bypass_param,
                kwargs=kwargs,
            )
            return func(*args, **kwargs)

        # 检测 func 是否 async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# ─── 内部:统一检查函数(便于直接调用 / 单测) ─────────────


def check_firewall(
    *,
    platform: str = "",
    action: str = "",
    feature_flag: str = "",
    require_budget: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """
    直接调用版(非装饰器).返回 {"allowed": bool, "reason": str}.
    
    用于在装饰器不方便的场景直接调用。
    """
    if force:
        return {"allowed": True, "reason": "bypass_by_force"}
    
    # 1. feature_flag 检查
    if feature_flag:
        flags_data = platform_crawl_settings.feature_flags()
        flags_map = {row["flag_key"]: bool(row.get("enabled")) for row in flags_data.get("flags", [])}
        if not flags_map.get(feature_flag, False):
            return {
                "allowed": False,
                "reason": "feature_flag_disabled",
                "detail": f"功能开关 '{feature_flag}' 未开启",
            }
    
    # 2. platform 设置检查
    if platform:
        try:
            settings_data = platform_crawl_settings.platform_settings()
            platforms_map = {row["platform"]: row for row in settings_data.get("platforms", [])}
            config = platforms_map.get(platform.lower(), {})
            
            if not int(config.get("crawl_enabled") or 0):
                return {
                    "allowed": False,
                    "reason": "platform_crawl_disabled",
                    "detail": f"平台 '{platform}' 抓取开关未开启",
                }
            
            if require_budget and float(config.get("monthly_budget_usd") or 0) <= 0:
                return {
                    "allowed": False,
                    "reason": "platform_budget_zero",
                    "detail": f"平台 '{platform}' 月度预算为 0,未授权外部调用",
                }
        except Exception as exc:
            return {
                "allowed": False,
                "reason": "platform_settings_error",
                "detail": f"无法读取平台设置: {exc}",
            }
    
    return {"allowed": True, "reason": "passed", "action": action or "ok"}


# ─── 内部辅助 ────────────────────────────────────────


def _check_firewall(
    *,
    platform: str,
    action: str,
    feature_flag: str,
    require_budget: bool,
    bypass_param: str,
    kwargs: dict[str, Any],
) -> None:
    """装饰器内部用:检查通过则 return,不通过抛 HTTPException(503)"""
    # 检查 bypass 参数(只信任 dict body 里的显式参数)
    force = False
    body = kwargs.get("body") or kwargs.get("payload") or {}
    if isinstance(body, dict) and body.get(bypass_param):
        # 只允许 owner 角色 bypass(由 staff 参数判断)
        staff = kwargs.get("staff") or {}
        if isinstance(staff, dict) and staff.get("is_owner"):
            force = True
    
    result = check_firewall(
        platform=platform,
        action=action,
        feature_flag=feature_flag,
        require_budget=require_budget,
        force=force,
    )
    
    if not result["allowed"]:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "firewall_blocked",
                "reason": result.get("reason", "unknown"),
                "message": result.get("detail", "防火墙拒绝"),
                "platform": platform,
                "action": action,
            },
        )
