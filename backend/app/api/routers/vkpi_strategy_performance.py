"""V-KPI S4 实际战略表现看板路由。

- GET /api/admin/vkpi/strategy/performance
  → 「我们的战略判断到底准不准」plan vs actual 三账对齐:
    ① 押注账(vkpi_bet_ledger won/lost/open + 最老 open 账龄)
    ② 预测军团(prediction_ledger 各组命中率精选 + 待对答案积压)
    ③ 履约战果(完成 loop 数 + 观察窗口→发布→实际播放 planned vs actual 样例)
    + lessons(已沉淀教训 top5)+ honesty_note(哪本账还在数据荒直说)。
  实现在 app.domains.market.strategy_performance(纯 SQL/规则聚合已有数据,决定性零 LLM)。

诚实态:单账失败该账 status="error" 其余照出;聚合整体异常不 500,
回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零写库、零触 viltrox_fit_score、不碰 rule_v0、不引外部行情。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-strategy-performance"])


@router.get("/strategy/performance")
def get_strategy_performance(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """S4 战略表现看板:三账记分牌 + 教训 top5 + 数据荒诚实条(全只读,不写库)。"""
    del staff
    from app.domains.market import strategy_performance

    try:
        return strategy_performance.performance()
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("strategy_performance endpoint failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}
