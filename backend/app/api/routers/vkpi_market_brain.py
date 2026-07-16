"""V-KPI GTM 总脑路由(GTM-1,纯读)。

- GET /api/admin/vkpi/market-brain/gtm-plan/preview
  → 输入 SKU+国家+预算+目标+窗口,返回 11 段作战建议(public_plan)+ meta。
  实现在 app.domains.market_brain.gtm_plan_preview(纯读聚合,零写库零 LLM 零采集)。

诚实态:SKU 不存在 404;goal 非法 422;每段缺数据由 domain 层返回
{status:"empty"/"data_missing", reason};聚合内部异常不 500,回 {status:"error"}。
红线:显示层宪法——响应只出 public_plan,private_evidence 绝不返回
(?debug=1+owner 分支 v1 不实现);纯读展示,零触 viltrox_fit_score、不碰 rule_v0;
绝不复用带副作用的 GET(marketing-brain/daily 与 market/trends 一律不碰)。
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard
from app.core.logging import get_logger
from app.domains.market_brain.read_cache import cacheable_payload, freshness_version
from app.services.cache import cache_get_or_build

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-market-brain"])
_GTM_READ_CACHE_TTL_SEC = 30


def _canonical_preview_request(
    *,
    sku: str,
    country: str | None,
    budget_usd: float,
    goal: str,
    window_days: int,
) -> dict[str, str | float | int | None]:
    sku_clean = str(sku or "").strip()
    if not sku_clean or len(sku_clean) > 120:
        raise ValueError("sku must contain 1-120 non-whitespace characters")
    country_clean = str(country or "").strip().upper() or None
    if country_clean is not None and len(country_clean) > 8:
        raise ValueError("country must contain at most 8 characters")
    budget = float(budget_usd)
    if not (0 < budget <= 1_000_000):
        raise ValueError("budget_usd must be within (0, 1000000]")
    goal_clean = str(goal or "").strip().lower()
    days = int(window_days)
    if days < 7 or days > 90:
        raise ValueError("window_days must be within [7, 90]")
    return {
        "sku": sku_clean,
        "country": country_clean,
        "budget_usd": budget,
        "goal": goal_clean,
        "window_days": days,
    }


def _organization_id_for_cache(staff: dict | None) -> int:
    raw = (staff or {}).get("organization_id")
    try:
        if int(raw or 0) > 0:
            return int(raw)
    except (TypeError, ValueError):
        pass
    return 0


def _preview_cache_key(
    *,
    staff: dict | None,
    sku: str,
    country: str | None,
    budget_usd: float,
    goal: str,
    window_days: int,
) -> str:
    """Canonical, bounded cache identity without exposing raw SKU in Redis keys."""
    from app.domains.access import scope
    from app.domains.market_brain.gtm_plan_preview import METHOD

    canonical = _canonical_preview_request(
        sku=sku,
        country=country,
        budget_usd=budget_usd,
        goal=goal,
        window_days=window_days,
    )
    normalized = [
        canonical["sku"],
        canonical["country"],
        float(canonical["budget_usd"]).hex(),
        canonical["goal"],
        canonical["window_days"],
    ]
    digest = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    authorization = [
        max(0, int(scope.actor_staff_id(staff))),
        scope.role_key(staff),
        bool(scope.is_owner(staff)),
        bool(scope.can_view_all(staff)),
    ]
    auth_digest = hashlib.sha256(
        json.dumps(authorization, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    data_version = freshness_version(METHOD, ttl_seconds=_GTM_READ_CACHE_TTL_SEC)
    return (
        f"vkpi_gtm:plan_preview:v3:data:{data_version}:"
        f"org:{_organization_id_for_cache(staff)}:"
        f"auth:{auth_digest}:{digest}"
    )


@router.get("/market-brain/gtm-plan/preview")
def get_gtm_plan_preview(
    sku: str = Query(..., min_length=1, max_length=120, description="必填 SKU 码(vkpi_products 口径)"),
    country: str | None = Query(None, max_length=8, description="ISO 国家码;v1 只影响候选排序与 Dealer 占位说明"),
    budget_usd: float = Query(3000, gt=0, le=1_000_000, description="预算(USD),默认 3000"),
    goal: str = Query("exposure", description="exposure|conversion|content|channel"),
    window_days: int = Query(30, ge=7, le=90, description="预判窗口天数,默认 30"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """GTM Plan 纯读预览:11 段建议 + meta(零写库零 LLM 零采集,同输入同输出)。"""
    from app.domains.market_brain import gtm_plan_preview

    try:
        canonical = _canonical_preview_request(
            sku=sku,
            country=country,
            budget_usd=budget_usd,
            goal=goal,
            window_days=window_days,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    goal_clean = str(canonical["goal"])
    if goal_clean not in gtm_plan_preview.GOALS:
        raise HTTPException(
            status_code=422,
            detail=f"goal must be one of {list(gtm_plan_preview.GOALS)}, got {goal!r}",
        )
    scope_unavailable = legacy_gtm_scope_guard(staff, surface="preview")
    if scope_unavailable is not None:
        return scope_unavailable
    try:
        return cache_get_or_build(
            _preview_cache_key(
                staff=staff,
                sku=str(canonical["sku"]),
                country=(str(canonical["country"]) if canonical["country"] is not None else None),
                budget_usd=float(canonical["budget_usd"]),
                goal=goal_clean,
                window_days=int(canonical["window_days"]),
            ),
            lambda: gtm_plan_preview.build_preview(
                sku=str(canonical["sku"]),
                country=(str(canonical["country"]) if canonical["country"] is not None else None),
                budget_usd=float(canonical["budget_usd"]),
                goal=goal_clean,
                window_days=int(canonical["window_days"]),
            ),
            ttl=_GTM_READ_CACHE_TTL_SEC,
            cache_if=cacheable_payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 聚合失败不 500 裸奔,诚实回原因
        logger.warning("gtm_plan_preview failed for sku=%s: %s", sku, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(canonical["sku"])}
