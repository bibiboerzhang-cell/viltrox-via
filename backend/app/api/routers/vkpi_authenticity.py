"""V-KPI 受众真实性路由(G4 件③)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/authenticity
  → 受众真实性 v0:评论者重复率/评论模板化率/互动播放比池分位离群/既有
  suspect_inflation 列(P0-3)四路库内信号 → authenticity_score 0-100。
  实现在 app.domains.audience.authenticity(纯聚合已有数据,零外网、零 LLM、零写库)。

诚实态:KOL 不存在 404;评论样本 <10 时 confidence=low(诚实降级);
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,信号仅 info/warn 提示绝不下「买粉」结论;
零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-authenticity"])


@router.get("/kol-pool/{kol_pool_id}/authenticity")
def get_kol_authenticity(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 受众真实性 v0:四路库内信号 + 0-100 综合分(全只读,不写库)。"""
    del staff
    from app.domains.audience import authenticity

    try:
        return authenticity.authenticity_signal(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("authenticity_signal failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
