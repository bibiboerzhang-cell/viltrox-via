"""V-KPI 外联信号路由(G1 外联三承诺 + 敢给差评信号)。

- GET /api/admin/vkpi/kol-pool/{kol_pool_id}/critic-signal
  → 「敢给差评」信号:该 KOL 历史上对品牌/产品公开给过批评意见 = 可信度加分信号。
  实现在 app.domains.kol.critic_signal(词表法纯聚合已有数据,零新采集、零 LLM)。
- GET /api/admin/vkpi/outreach/three-promises
  → 外联三承诺文案读端(7 天内付款 / 送测留用不回收 / 书面承诺不干预差评,
  中英双语模板常量 + 开关态);实现在 app.domains.kol.outreach_promises。

诚实态:KOL 不存在 404;聚合内部异常不 500,回 {status:"error", reason}
(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零写库;critic 信号不进任何评分,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-outreach-signals"])


@router.get("/kol-pool/{kol_pool_id}/critic-signal")
def get_kol_critic_signal(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """「敢给差评」信号:词表法聚合创作者标题/描述 + 深析转述(全只读,不写库)。"""
    del staff
    from app.domains.kol import critic_signal as critic_signal_domain

    try:
        return critic_signal_domain.critic_signal(int(kol_pool_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("critic_signal failed for kol_pool_id=%s: %s", kol_pool_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "kol_pool_id": int(kol_pool_id)}


@router.get("/outreach/three-promises")
def get_outreach_three_promises(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """外联三承诺文案 + 开关态(纯常量读端,零 SQL/零 LLM)。"""
    del staff
    from app.domains.kol import outreach_promises

    return outreach_promises.promises_payload()
