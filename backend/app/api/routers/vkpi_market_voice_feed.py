"""V-KPI 市场之声「反馈流」分页只读路由(与前端并行开发,契约固定)。

- GET /api/admin/vkpi/market/voice-feed?offset=0&limit=20&identity=&sentiment=&category=
  → {items, total, offset, limit};item 含 id/source_table/platform/text/language/
    identity(kol|owned|user)/identity_ref/post_url/likes/created_at/prov。
  实现在 app.domains.market.voice_feed(vkpi_comments 三路 JOIN 身份判定,纯读)。
  limit 封顶 50(FastAPI 校验 + domain 双保险);identity / category 非法 400。
  category(纯增量,数据点下钻):COMPLAINT_CATEGORIES 六类 key + 'wishlist' 词族
  过滤(词族匹配在 Python 层,SQL 零 LIKE,扫描双层封顶),响应增量回 category 键。

显示层宪法:返回体不含 raw_data_json / author_id / author_handle 等内部字段,
身份主体只给 identity_ref(KOL handle / 官号名,普通用户留空)。
诚实态:聚合内部异常不 500,回契约形状 + status:"error"(前端安静降级)。
红线:纯读展示,零写库,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-market-voice-feed"])


@router.get("/market/voice-feed")
def get_voice_feed(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    identity: str = Query(""),
    sentiment: str = Query(""),
    category: str = Query(""),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """市场之声反馈流:vkpi_comments 分页原声(全只读,不写库,零 LLM)。"""
    del staff
    from app.domains.market import voice_feed

    try:
        return voice_feed.get_voice_feed(
            offset=offset,
            limit=limit,
            identity=identity,
            sentiment=sentiment,
            category=category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — 反馈流读取失败不炸接口,诚实回原因
        logger.warning("voice_feed failed offset=%s limit=%s: %s", offset, limit, exc)
        return {
            "items": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
            "status": "error",
            "reason": str(exc)[:300],
        }
