"""V-KPI GTM 总脑页 summary + 增长打法规则库路由(GTM-1 W2)。

- GET /api/admin/vkpi/market-brain/summary
  → 五卡一次聚合:weekly_signals / product_opportunities / recommended_actions /
    strategy_defaults / learning_digest。实现在 app.domains.market_brain.summary
    (纯读聚合既有数据,零写库、零 LLM、零采集)。
- GET /api/admin/vkpi/market-brain/playbook
  → 增长打法规则库(平台数字入册为数据,非系统真理);可按 platform 过滤、
    按 rule_id 取单条。实现在 app.domains.market_brain.growth_playbook(纯内存数据)。

红线:全新建纯读端点——绝不复用带副作用的 GET(marketing-brain/daily 与
market/trends 有隐藏写入,一律不碰);响应无 private 字段(评分明细/原始评论
大段/竞品笔记/黑名单);不触 viltrox_fit_score / rule_v0。
诚实态:聚合内部单段失败由 domain 层回 {status:"error"} 不拖垮整卡;
整卡失败不 500,回 {status:"error", reason}(前端安静降级)。
"""
from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.gtm_scope import legacy_gtm_scope_guard
from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.domains.market_brain.read_cache import cacheable_payload, freshness_version
from app.services.cache import cache_get_or_build

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-market-brain"])
_GTM_READ_CACHE_TTL_SEC = 30


def _organization_id_for_cache(staff: dict | None) -> int:
    raw = (staff or {}).get("organization_id")
    try:
        if int(raw or 0) > 0:
            return int(raw)
    except (TypeError, ValueError):
        pass
    return 0


def _summary_cache_key(staff: dict | None) -> str:
    """Partition the cached card by tenant and the Action Inbox read scope."""
    from app.domains.access import scope
    from app.domains.market_brain.summary import METHOD

    organization_id = _organization_id_for_cache(staff)
    data_version = freshness_version(METHOD, ttl_seconds=_GTM_READ_CACHE_TTL_SEC)
    authorization = [
        max(0, int(scope.actor_staff_id(staff))),
        scope.role_key(staff),
        bool(scope.is_owner(staff)),
        bool(scope.can_view_all(staff)),
    ]
    auth_digest = hashlib.sha256(
        json.dumps(authorization, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return (
        f"vkpi_gtm:summary:v3:data:{data_version}:"
        f"org:{organization_id}:auth:{auth_digest}"
    )


@router.get("/market-brain/summary")
def get_market_brain_summary(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """GTM 总脑页五卡数据,前端一次请求(全只读,不写库)。"""
    from app.domains.market_brain import summary

    scope_unavailable = legacy_gtm_scope_guard(staff, surface="summary")
    if scope_unavailable is not None:
        return scope_unavailable
    try:
        return cache_get_or_build(
            _summary_cache_key(staff),
            lambda: summary.build_summary(staff),
            ttl=_GTM_READ_CACHE_TTL_SEC,
            cache_if=cacheable_payload,
        )
    except Exception as exc:  # noqa: BLE001 — 聚合失败不炸接口,诚实回原因
        logger.warning("market_brain summary failed: %s", exc, exc_info=True)
        return {"status": "error", "reason": str(exc)[:300]}


@router.get("/market-brain/playbook")
def get_growth_playbook(
    platform: str | None = Query(default=None, max_length=40),
    rule_id: str | None = Query(default=None, max_length=80),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """增长打法规则库(纯数据读):全量/按平台过滤;rule_id 取单条,不存在 404。"""
    del staff
    from app.domains.market_brain import growth_playbook

    if rule_id:
        try:
            return {"status": "ok", "rule": growth_playbook.rule(rule_id)}
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return growth_playbook.rules(platform)
    except Exception as exc:  # noqa: BLE001 — 纯内存读不该失败,但契约上不 500
        logger.warning("growth_playbook rules failed: %s", exc, exc_info=True)
        return {"status": "error", "reason": str(exc)[:300]}
