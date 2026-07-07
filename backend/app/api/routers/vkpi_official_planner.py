"""V-KPI Channel Brain · 官号内容计划器路由(CB1,纯读)。

- GET /api/admin/vkpi/channel/official-plan?sku=&category=
  → 全官号内容计划:每号最灵内容形式 × 最灵时段 → 动作建议(可按 SKU/品类聚焦排序)。
- GET /api/admin/vkpi/channel/official/{channel_id}/performance
  → 单官号内容表现侧写(形式/时段分布 + top 帖 + 趋势线)。

实现在 app.domains.channel.official_planner(纯读聚合,词表法零 LLM 零采集零写库)。

诚实态:官号 0 / 帖子 0 由 domain 层返回 status=data_missing/empty + note;
单号非官方口径/不存在 → status=not_found;聚合内部异常不 500 裸奔,回 {status:"error"}。
红线:零写库、零 LLM、零采集;绝不触碰 viltrox_fit_score、不碰 rule_v0;
compat SQL ? 占位无字面 %;绝不复用带副作用的 GET。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-channel-planner"])


@router.get("/channel/official-plan")
def get_official_content_plan(
    sku: str | None = Query(None, max_length=120, description="可选 SKU 码(vkpi_products 口径),聚焦相关官号排序"),
    category: str | None = Query(None, max_length=120, description="可选品类关键词,词表匹配标题聚焦"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """全官号内容计划(纯读聚合;同输入同输出,零写库零 LLM 零采集)。"""
    del staff
    from app.domains.channel import official_planner

    try:
        return official_planner.official_content_plan(sku=sku, category=category)
    except Exception as exc:  # noqa: BLE001 — 聚合失败不 500 裸奔,诚实回原因
        logger.warning("official_content_plan failed sku=%s cat=%s: %s", sku, category, exc)
        return {"status": "error", "reason": str(exc)[:300]}


@router.get("/channel/official/{channel_id}/performance")
def get_channel_performance(
    channel_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """单官号内容表现侧写(纯读聚合;非官方口径/不存在 → not_found)。"""
    del staff
    from app.domains.channel import official_planner

    try:
        return official_planner.channel_performance(channel_id)
    except Exception as exc:  # noqa: BLE001 — 聚合失败不 500 裸奔,诚实回原因
        logger.warning("channel_performance failed channel_id=%s: %s", channel_id, exc)
        return {"status": "error", "reason": str(exc)[:300]}
