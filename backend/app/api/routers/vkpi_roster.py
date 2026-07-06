"""V-KPI Roster 覆盖最大化组合路由(件D)。

- POST /api/admin/vkpi/roster/optimize  body {candidate_ids: [int], max_size: int=8}
  → 受众重叠×地理×平台 的去重触达最大化组合(发射台第⑥输出消费)。
  实现在 app.domains.kol.roster_optimizer(决定性贪心 set-cover,零 LLM、纯读不写库)。

诚实态:空候选/全部查无 → domain 层返回 {status:"empty"/"no_candidates", reason};
内部异常不 500,回 {status:"error", reason}(发射台增益块非阻塞)。
红线:纯读展示,不触 fit 分存储列、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-roster"])


class RosterOptimizeBody(BaseModel):
    """组合优化请求体:候选 KOL id 列表 + 组合上限(domain 层再做 1..20 夹取)。"""

    candidate_ids: list[int] = Field(default_factory=list, max_length=200)
    max_size: int = Field(default=8, ge=1, le=20)


@router.post("/roster/optimize")
def post_roster_optimize(
    body: RosterOptimizeBody,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """覆盖最大化组合:候选 → ≤max_size 人 roster(纯读聚合计算,POST 只为带列表体)。"""
    del staff
    from app.domains.kol import roster_optimizer

    try:
        return roster_optimizer.optimize_roster(list(body.candidate_ids or []), max_size=int(body.max_size))
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("roster optimize failed candidates=%s: %s", len(body.candidate_ids or []), exc)
        return {"status": "error", "reason": str(exc)[:300], "selected": [], "dropped_overlap": []}
