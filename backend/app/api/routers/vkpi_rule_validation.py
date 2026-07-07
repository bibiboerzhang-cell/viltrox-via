"""E4 · 规则库校准报告路由(growth_playbook × 自有已析视频回归验证)。

- GET /api/admin/vkpi/learning/rule-validation
  → 首份校准报告:①可得信号 × 互动率/播放 Spearman 侦察 ②30 条 playbook 规则
    逐条 verdict(supported/contradicted/insufficient_data,样本<30 一律 insufficient)
    ③ {rule_id, our_sample, our_finding, verdict, confidence} 消费结构。
  实现在 app.domains.learning.rule_validation(纯读聚合,零 LLM、零视频重析、零采集)。

诚实态:表缺/无已析视频回 {status:"empty", reason};聚合内部异常不 500,
回 {status:"error", reason}。周对答案(weekly_answers)可引用本报告,不在此接线。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-rule-validation"])


@router.get("/learning/rule-validation")
def get_rule_validation(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """规则库校准报告:playbook 规则 × 自有数据的初步 verdict(全只读,不写库)。"""
    del staff
    from app.domains.learning import rule_validation

    try:
        return rule_validation.validate_rules()
    except Exception as exc:  # noqa: BLE001 — 报告失败不炸接口,诚实回原因
        logger.warning("rule_validation report failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}
