"""CB3 独立站/Shopify 承接建议路由(Conversion Readiness Actions,纯读)。

前缀 /api/admin/vkpi/channel;鉴权 require_tab("vkpi","read"),镜像 vkpi_emotion_tags
一类只读增益端点。
- GET /api/admin/vkpi/channel/indie-site-actions?sku=
  → 对给定 SKU 输出「承接层」4 项就绪度 checklist(短链就绪 / 落地页+样片+FAQ /
    佣金码 / 购买路径),每项 ready|missing|unknown + basis(依据)。

实现在 app.domains.channel.indie_site_actions(纯读、零副作用、零 LLM、零采集)。
诚实态:SKU 未命中 vkpi_products → status='not_found' + checklist 结构就位(各项 unknown);
本地 0 Shopify 订单 → shopify.status='data_missing' + note,绝不编数。

红线:零触 viltrox_fit_score / rule_v0;零写库(纯 GET,无副作用)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab


router = APIRouter(prefix="/api/admin/vkpi/channel", tags=["vkpi-indie-site"])


@router.get("/indie-site-actions")
def get_indie_site_actions(
    sku: str = Query(..., min_length=1),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """独立站/Shopify 承接就绪 checklist(纯读)。"""
    del staff
    from app.domains.channel import indie_site_actions as mod

    try:
        return mod.indie_site_actions(sku)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — 承接建议纯读失败不炸接口,诚实回原因
        raise HTTPException(status_code=500, detail=f"indie-site-actions error: {exc}") from exc
