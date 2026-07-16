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
from app.services.cache import cache_get_or_build

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-category-tracks"])
_STRATEGY_READ_CACHE_TTL_SEC = 30


def _organization_id_for_cache(staff: dict | None) -> int:
    raw = (staff or {}).get("organization_id")
    try:
        if int(raw or 0) > 0:
            return int(raw)
    except (TypeError, ValueError):
        pass
    from app.domains.platform.tenancy import current_org_id

    return max(1, int(current_org_id(staff)))


def _build_for_organization(organization_id: int) -> dict:
    from app.domains.market import category_tracks
    from app.domains.platform.tenancy import default_organization_id

    if organization_id != default_organization_id():
        return {
            "status": "scope_unavailable",
            "reason": "赛道聚合的底层评论/证据/目录尚未完成多租户字段收窄，未返回默认工作区数据。",
            "organization_id": organization_id,
        }
    return category_tracks.tracks()


@router.get("/strategy/category-tracks")
def get_category_tracks(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """新赛道机会矩阵:下一个该进的品类/焦段赛道(全只读,不写库,零 LLM)。"""
    try:
        organization_id = _organization_id_for_cache(staff)
        return cache_get_or_build(
            f"vkpi_strategy:category_tracks:v2:org:{organization_id}",
            lambda: _build_for_organization(organization_id),
            ttl=_STRATEGY_READ_CACHE_TTL_SEC,
        )
    except Exception as exc:  # noqa: BLE001 — 聚合失败不炸接口,诚实回原因
        logger.warning("category_tracks failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:300]}
