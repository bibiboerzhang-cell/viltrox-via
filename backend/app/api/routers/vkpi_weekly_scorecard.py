"""V-KPI 周度记分卡路由(L 轨道收官件 A)。

- GET /api/admin/vkpi/learning/weekly-scorecard?weeks=8
  → 九组预测台账按 ISO 周分桶的周度命中率曲线 + 本周 vs 上周动量
    + pending 积压计数(776 条待对答案=头号信号)+ 最老 top5 催办名单。
  实现在 app.domains.learning.weekly_scorecard(prediction_ledger 同款裁决口径,
  纯聚合已有数据,零新采集、零 LLM、零写库)。

诚实态:样本荒周 sparse;整组无裁决 pending/empty;domain 层永不 raise,
路由兜底异常也不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-weekly-scorecard"])


@router.get("/learning/weekly-scorecard")
def get_weekly_scorecard(
    weeks: int = Query(default=8, ge=1, le=52),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """周度记分卡:九组台账周度命中率曲线 + pending 积压(全只读,不写库)。"""
    del staff
    from app.domains.learning import weekly_scorecard as ws

    try:
        return ws.weekly_scorecard(weeks=int(weeks))
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("weekly_scorecard failed weeks=%s: %s", weeks, exc)
        return {"status": "error", "reason": str(exc)[:300], "weeks": int(weeks)}
