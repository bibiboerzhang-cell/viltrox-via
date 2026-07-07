"""V-KPI 品牌安全扫描路由(G4 件②)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/brand-safety
  → 品牌安全 v0:四路库内信号(FTC 披露/评论负面聚类/争议词表/竞品独占迹象)
  + 12 类风险框架(对齐同行口径,诚实标「库内信号 v0,外网扫描待接」)。
  实现在 app.domains.kol.brand_safety(纯聚合已有数据,零外网、零 LLM、零写库)。

诚实态:KOL 不存在 404;数据缺口由 domain 层逐信号返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,risk_level 仅 none/info/warn 提示绝不下结论;
零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-brand-safety"])


@router.get("/kol-pool/{kol_pool_id}/brand-safety")
def get_kol_brand_safety(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 品牌安全 v0:12 类风险框架 + 四路库内信号(全只读,不写库)。"""
    del staff
    from app.domains.kol import brand_safety

    try:
        return brand_safety.brand_safety_scan(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("brand_safety_scan failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
