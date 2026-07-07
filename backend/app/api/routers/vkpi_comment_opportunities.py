"""G3 评论区营销机会雷达路由 —— 「官号值得去哪些高热视频下评论」Top5 清单。

- GET /api/admin/vkpi/market/comment-opportunities?days=
  → 近 N 天(默认 7)库内高播放视频按「热度×主题相关×竞品语境×新鲜度」打分,
  Top5 机会带真 URL/为何值得去(规则模板)/建议评论角度(词表模板非 LLM)。
  实现在 app.domains.market.comment_opportunities(纯读聚合,零采集零写库)。

诚实态:窗口无数据由 domain 层返回 status="empty"+reason(含库内最新发布日);
聚合内部异常不 500,回 {status:"error", reason}(增益面板非阻塞)。
红线:只出机会清单绝不自动发评论(一切评论人工撰写发布);
纯读展示,零触 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-comment-opportunities"])


@router.get("/market/comment-opportunities")
def get_comment_opportunities(
    days: int = Query(default=7, ge=1, le=180),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """近 N 天「官号值得去评论」Top5 机会(全只读;决定性:同输入同输出)。"""
    del staff
    from app.domains.market import comment_opportunities

    try:
        return comment_opportunities.opportunities(days=int(days))
    except Exception as exc:  # noqa: BLE001 — 增益面板失败不炸接口,诚实回原因
        logger.warning("comment_opportunities failed for days=%s: %s", days, exc)
        return {"status": "error", "reason": str(exc)[:300], "days": int(days)}
