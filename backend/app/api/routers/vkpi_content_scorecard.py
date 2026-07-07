"""V-KPI 内容记分卡路由(E2 三平台换轴,GTM-2)。

- GET /api/admin/vkpi/content/scorecard/video/{evidence_id}
  → 单条视频证据按平台北极星轴判档(A/B/C/淘汰/不可判)+ rule_refs 溯源;
- GET /api/admin/vkpi/content/scorecard/channel/{channel_id}
  → 官号(vkpi_employee_channels)全部帖子判档分布聚合。
  实现在 app.domains.content.content_scorecard(纯读聚合已有数据 + growth_playbook
  规则库消费,零新采集、零 LLM、零重析、零写库)。

诚实态:evidence/channel 不存在 404;北极星真数据(完播/2s留存/CTR/sends)拿不到
一律 unknown/unavailable + proxy 标注,由 domain 层如实给 reason;聚合内部异常
不 500,回 {status:"error", reason}(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0、绝不触发任何重析/入队。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-content-scorecard"])


@router.get("/content/scorecard/video/{evidence_id}")
def get_video_scorecard(
    evidence_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """单视频内容判档(全只读,不写库,不触发重析)。"""
    del staff
    from app.domains.content import content_scorecard

    try:
        return content_scorecard.score_video(int(evidence_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("content_scorecard.score_video failed for evidence_id=%s: %s", evidence_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "evidence_id": int(evidence_id)}


@router.get("/content/scorecard/channel/{channel_id}")
def get_channel_scorecard(
    channel_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """官号内容档位分布(全只读,不写库,不触发采集)。"""
    del staff
    from app.domains.content import content_scorecard

    try:
        return content_scorecard.score_channel_posts(int(channel_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("content_scorecard.score_channel_posts failed for channel_id=%s: %s", channel_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "channel_id": int(channel_id)}
