"""V-KPI 新赛道机会分析路由(S2 · 机会矩阵)。

- GET /api/admin/vkpi/strategy/category-tracks
  → 品类维 × 焦段维赛道矩阵:每赛道四个决定性信号(需求声量+环比 / 我方覆盖 /
  竞品密度 / 机会分)+ top 机会排序 + 「不进」清单。
  实现在 app.domains.market.category_tracks(纯 SQL/词表规则聚合库内真数据,
  零新采集、零 LLM、零迁移;机会分公式与权重常量写在响应 basis 里,v0 待校准)。

诚实态:数据不足由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0;绝不编造市场数据。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-category-tracks"])


@router.get("/strategy/category-tracks")
def get_category_tracks(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """新赛道机会矩阵:下一个该进的品类/焦段赛道(全只读,不写库,零 LLM)。"""
    del staff
    from app.domains.market import category_tracks

    try:
        return category_tracks.tracks()
    except Exception as exc:  # noqa: BLE001 — 聚合失败不炸接口,诚实回原因
        logger.warning("category_tracks failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}
