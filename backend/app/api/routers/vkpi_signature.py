"""V-KPI KOL 招牌面板路由(件①)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/signature
  → 三块聚合读数:🎬 擅长拍法 / 🏆 最爆代表作 TOP3 / 💬 最引反馈 TOP3。
  实现在 app.domains.kol.signature_profile(纯聚合已有数据,零新采集、零 LLM)。

诚实态:KOL 不存在 404;每块缺数据由 domain 层返回 {status:"empty", reason};
聚合内部异常不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-signature"])


@router.get("/kol-pool/{kol_pool_id}/signature")
def get_kol_signature(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 招牌面板:该 KOL「最擅长什么」的三块聚合(全只读,不写库)。"""
    del staff
    from app.domains.kol import signature_profile

    try:
        return signature_profile.signature_profile(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("signature_profile failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
