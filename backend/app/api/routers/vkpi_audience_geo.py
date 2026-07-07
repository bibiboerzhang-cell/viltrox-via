"""V-KPI 受众地图 v1 路由(B2 多信号融合)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/audience-geo
  → 受众地理分布:评论语言 / 评论者名字字系 / 评论者档案国别 / 创作者国别弱先验
  多信号融合 + confidence(high/medium/low)。实现在 app.domains.audience.geo_ensemble
  (纯聚合已有数据,零新采集、零 LLM;payload 恒带 geo_source 与 signals_used,
  绝不把创作者国别代理伪装成受众实测)。

诚实态:KOL 不存在 404;每路信号缺数据由 domain 层标 status=absent + reason;
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-audience-geo"])


@router.get("/kol-pool/{kol_pool_id}/audience-geo")
def get_kol_audience_geo(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """受众地图 v1:该 KOL 受众地理的多信号融合读数(全只读,不写库)。"""
    del staff
    from app.domains.audience import geo_ensemble

    try:
        return geo_ensemble.audience_geo(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("audience_geo failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
