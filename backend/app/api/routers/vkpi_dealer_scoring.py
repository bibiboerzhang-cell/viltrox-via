"""V-KPI CB4 · Dealer 适配评分路由(Channel Brain 层)。

- GET /api/admin/vkpi/channel/dealer-fit?sku=<sku>&limit=<n>
  → 该 SKU 的 Dealer geo/category 适配目标榜(实现在
    app.domains.channel.dealer_scoring.dealer_targets,纯读真库 vkpi_dealers)。

诚实态:vkpi_dealers 本地 0 行(硬件品牌盲区)→ domain 层回 status="data_missing" +
  ready_when(前端诚实空态渲染),绝不编数;整端点异常不 500,回
  {status:"error", reason}(前端安静降级)。
红线:纯读、零副作用 GET、零 LLM、零采集、零写库;不触 viltrox_fit_score / rule_v0;
  与 vkpi_dealers(地图/地理)路由物理隔离——此处只做适配评分。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-dealer-scoring"])


@router.get("/channel/dealer-fit")
def get_dealer_fit(
    sku: str = Query(..., min_length=1, description="产品 SKU(vkpi_products 命中则带品类,否则 geo-only)"),
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """SKU 的 Dealer 适配目标榜(纯读;0 行诚实 data_missing)。"""
    del staff
    from app.domains.channel import dealer_scoring

    try:
        return dealer_scoring.dealer_targets(str(sku), limit=int(limit))
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("dealer_scoring.dealer_targets failed for sku=%s: %s", sku, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)}
