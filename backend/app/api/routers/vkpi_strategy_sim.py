"""S3 商业策略评估模拟器路由 —— 「这笔预算怎么花最划算」what-if 决定性模拟。

- GET /api/admin/vkpi/strategy/simulate?sku=&budget_usd=&max_roster=
  → 三策略并排模拟(A 头部集中 / B 长尾铺量 / C 混合 50/50)+ 推荐排序。
  实现在 app.domains.market.strategy_sim(纯 SQL/规则聚合,零 LLM 零采集零写库,
  复用第4轮三引擎 rate_card / performance_forecast / roster_optimizer)。

诚实态:SKU 不存在 404;候选/预算不足由 domain 层返回 status="empty"+reason;
聚合内部异常不 500,回 {status:"error", reason}(增益面板非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0;数字全带 basis 可追溯。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-strategy-sim"])


@router.get("/strategy/simulate")
def strategy_simulate(
    sku: str = Query(..., min_length=1, max_length=120),
    budget_usd: float = Query(..., gt=0, le=10_000_000),
    max_roster: int = Query(default=20, ge=1, le=20),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """三策略预算 what-if 模拟(全只读,不写库;同输入同输出决定性)。"""
    del staff
    from app.domains.market import strategy_sim

    try:
        return strategy_sim.simulate(sku, float(budget_usd), max_roster=int(max_roster))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益面板失败不炸接口,诚实回原因
        logger.warning("strategy_sim failed for sku=%s budget=%s: %s", sku, budget_usd, exc)
        return {"status": "error", "reason": str(exc)[:300], "sku": str(sku)[:120]}
