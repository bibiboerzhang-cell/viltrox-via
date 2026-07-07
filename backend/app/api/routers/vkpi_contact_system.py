"""B1 联系方式 L0 · 可联系性路由(件④)。

- GET  /api/admin/vkpi/kol-pool/{kol_pool_id}/contactability
  → 可联系性评估:0-100 分 + 建议触达渠道排序 + 来源汇总(纯读,不写库)。
- POST /api/admin/vkpi/kol-pool/{kol_pool_id}/contactability/refresh
  → 重算并写回 contactability_score 等 214 迁移 4 列(write 权限)。

实现在 app.domains.kol.contact_system(纯规则聚合已有数据,零新采集、零 LLM)。
合规:返回值联系方式全脱敏(与 pool_common 掩码同口径);真值仍走既有
contact_reveal 二次确认 + 审计门控,本路由绝不裸露明文。
诚实态:KOL 不存在 404;无渠道诚实 0 分(reason=no_contact_channels);
列缺失(未跑迁移 214)refresh 回明确错误码不 500。
红线:零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-contact-system"])


@router.get("/kol-pool/{kol_pool_id}/contactability")
def get_contactability(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """KOL 可联系性评估(全只读,不写库;联系方式全脱敏)。"""
    del staff
    from app.domains.kol import contact_system

    try:
        return contact_system.contactability(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("contactability failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.post("/kol-pool/{kol_pool_id}/contactability/refresh")
def refresh_contactability(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict:
    """重算并写回可联系性分(只写 214 迁移 4 列 + updated_at,列缺失诚实降级)。"""
    del staff
    from app.domains.kol import contact_system

    try:
        return contact_system.refresh_contactability(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_contactability failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}
