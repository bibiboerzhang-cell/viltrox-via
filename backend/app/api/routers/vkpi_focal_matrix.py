"""V-KPI KOL 焦段矩阵路由。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/focal-matrix
  → KOL×焦段/产品线覆盖矩阵:covered(拍过哪些焦段/产品线)+ gaps(目录里有 SKU
  但该 KOL 零覆盖的空白,按目录营销价值排序)+ matched_products(命中的我方 SKU 家族)。
  实现在 app.domains.kol.focal_matrix(纯聚合已有数据,零新采集、零 LLM)。

诚实态:KOL 不存在 404;每块缺数据由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-focal-matrix"])


@router.get("/kol-pool/{kol_pool_id}/focal-matrix")
def get_kol_focal_matrix(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 焦段矩阵:拍过哪些焦段/产品线、哪块空白可切入(全只读,不写库)。"""
    del staff
    from app.domains.kol import focal_matrix

    try:
        return focal_matrix.focal_matrix(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("focal_matrix failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
