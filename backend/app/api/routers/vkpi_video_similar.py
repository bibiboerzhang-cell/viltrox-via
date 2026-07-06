"""V-KPI 以视频找相似路由。

- GET /api/admin/vkpi/video/{evidence_id}/similar?limit=10&exclude_same_kol=true
  → 给一条已深析视频,找全库相似视频与其背后 KOL(「还有谁在拍同款内容」)。
  实现在 app.domains.kol.video_similarity(决定性降级:final_v1 数值维余弦 +
  词表标签 Jaccard,method=dimensions_cosine_v0,零 LLM、零新采集)。

诚实态:evidence 不存在 404;该视频没深析/没可比语料由 domain 层返回
{status:"empty", reason};聚合内部异常不 500,回 {status:"error", reason}
(前端安静缺席,增益块非阻塞)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-video-similar"])


@router.get("/video/{evidence_id}/similar")
def get_similar_videos(
    evidence_id: int,
    limit: int = 10,
    exclude_same_kol: bool = True,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """以视频找相似:决定性混合打分(维度余弦+标签 Jaccard),默认排除同 KOL 自己的视频。"""
    del staff
    from app.domains.kol import video_similarity

    try:
        return video_similarity.similar_videos(
            int(evidence_id),
            limit=int(limit),
            exclude_same_kol=bool(exclude_same_kol),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("similar_videos failed for evidence_id=%s: %s", evidence_id, exc)
        return {"status": "error", "reason": str(exc)[:300], "evidence_id": int(evidence_id)}
