"""段级创意资产库路由(D 段级 v0)——「哪个开头/哪种画面最灵」可检索。

- GET /api/admin/vkpi/creative-segments/search?query=&style=&focal=&limit=
  → 已深析(final_v1)视频拆段索引:开头类型/拍法标签/焦段 三路词表过滤,
    按所属视频播放数降序。实现在 app.domains.content.creative_segments
    (纯已有分析文本的索引,零切片文件、零外部调用、零 LLM、零写库)。

诚实态:深析库为空回 {status:"empty", reason};过滤零命中回 matched=0 空 items;
聚合内部异常不 500,回 {status:"error", reason}(增益块非阻塞,前端安静缺席)。
红线:纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-creative-segments"])


@router.get("/creative-segments/search")
def creative_segments_search(
    query: str = Query(default="", max_length=120),
    style: str = Query(default="", max_length=60),
    focal: str = Query(default="", max_length=20),
    limit: int = Query(default=30, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """段级创意资产检索(全只读,不写库)。"""
    del staff
    from app.domains.content import creative_segments

    try:
        return creative_segments.segment_search(query=query, style=style, focal=focal, limit=limit)
    except Exception as exc:  # noqa: BLE001 — 增益块失败不炸接口,诚实回原因
        logger.warning("creative segment_search failed (query=%s style=%s focal=%s): %s", query, style, focal, exc)
        return {"status": "error", "reason": str(exc)[:300], "items": []}
