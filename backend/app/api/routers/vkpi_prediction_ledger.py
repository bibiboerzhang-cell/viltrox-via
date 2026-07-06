"""V-KPI 预测台账路由(D 件)—— 「系统的预测有多准」,驾照升降级的数据引擎读数口。

- GET /api/admin/vkpi/prediction-ledger/summary
  → 全部分组台账摘要(kol_recommend/market_bet/alert_signal/brand_signal/performance_forecast
    + 执行台账动态 category 组),每组命中率/样本数/置信度/basis。
- GET /api/admin/vkpi/prediction-ledger/{action_type}?window=20
  → 单组近 window 次命中率(契约键 status/hit_rate/sample_count/confidence/basis)。
  注意:/summary 必须先于 /{action_type} 声明,否则会被路径参数吞掉。

实现在 app.domains.agents.prediction_ledger(纯 SQL 聚合已有数据,零 LLM、零写库)。
诚实态:数据荒是常态,空组 sample_count=0 照实返回;domain 层永不 raise,
路由层再兜一层,聚合异常不 500,回 {status:"error", reason}。
红线:纯读展示与判定,绝不执行外部动作;台账结果永不影响任何评分
(「影响评分」永久 NO —— 不回写 fit 评分存储列、不碰 rule_v0)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-prediction-ledger"])


@router.get("/prediction-ledger/summary")
def get_prediction_ledger_summary(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """预测台账摘要:每组命中率条 + 样本数 + 置信度(全只读,不写库)。"""
    del staff
    from app.domains.agents import prediction_ledger

    try:
        return prediction_ledger.ledger_summary()
    except Exception as exc:  # noqa: BLE001 — domain 层已兜底,这里是最后保险,不 500
        logger.warning("prediction_ledger summary route failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300], "groups": []}


@router.get("/prediction-ledger/{action_type}")
def get_prediction_ledger_group(
    action_type: str,
    window: int = Query(default=20, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """单组近 window 次命中率;未知 action_type 诚实返回 unknown_action_type,不 404 不编数。"""
    del staff
    from app.domains.agents import prediction_ledger

    try:
        return prediction_ledger.hit_rate_for(str(action_type), window=int(window))
    except Exception as exc:  # noqa: BLE001 — 最后保险,不 500
        logger.warning("prediction_ledger group route failed action_type=%s: %s", action_type, exc)
        return {"status": "error", "reason": str(exc)[:300], "action_type": str(action_type)}
