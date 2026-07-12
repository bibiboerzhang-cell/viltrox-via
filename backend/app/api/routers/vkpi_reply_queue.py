"""件3 · ReplyQueue 评论区销售员 v0 半自动 API。

前缀 /api/admin/vkpi;权限:read 读、write 写(require_tab("vkpi", ...))。
  GET  /reply-queue                    列表(可按 status/platform 过滤;offset/limit 服务端分页 + total)
  GET  /reply-queue/kpi-series         KPI 时序:按日入队/回复序列 + 上一等长窗环比(纯读)
  POST /reply-queue/screen             触发从 vkpi_comments 筛购买意向入队(pending)
  POST /reply-queue/enqueue-comment    市场之声 V0e:按 vkpi_comments.id 单条手动入队(幂等)
  POST /reply-queue/{id}/draft         为一条生成品牌口吻草稿(RAG 369 SKU + LLM,预算闸降级模板)
  POST /reply-queue/{id}/mark          人工标记 replied / dismissed(v0 不自动发帖)

红线:纯待办队列路由,零触 viltrox_fit_score/rule_v0。领域逻辑全在 comments/reply_queue.py
(时序在 comments/reply_queue_series.py)。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-reply-queue"])


@router.get("/reply-queue")
def api_list(
    status: str = Query(default="", description="pending/drafted/replied/dismissed"),
    platform: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, description="服务端分页偏移(缺省 0 = 旧调用行为不变)"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """列出评论区销售员待办队列。只读(按 staff 认领可见性收敛,防撞车)。

    分页:offset/limit 双层封顶(Query 校验 + domain max/min);响应含 total
    (同 WHERE 口径过滤后总数,与页无关)。缺省 offset=0 与旧全量单页零差异。
    """
    from app.domains.comments import reply_queue
    return reply_queue.list_queue(status=status, platform=platform, limit=limit, offset=offset, staff=staff)


@router.get("/reply-queue/kpi-series")
def api_kpi_series(
    days: int = Query(default=30, ge=1, le=180, description="窗口天数(近 N 天,UTC 日轴)"),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """回复队列 KPI 时序:按日入队/回复序列 + 上一等长窗环比(voice-report-ext kpi_series 同模式)。

    纯读聚合;日轴 0 填齐、右沿钳 now;上窗为 0 → delta_pct=null(前端诚实省略药丸)。
    聚合内部异常不 500,回契约形状 + status:"error"(前端四卡诚实回落虚线)。
    """
    del staff
    from app.domains.comments import reply_queue_series
    try:
        return reply_queue_series.kpi_series(days=days)
    except Exception as exc:  # noqa: BLE001 — 时序失败不炸接口,契约形状诚实降级
        logger.warning("reply_queue kpi_series failed days=%s: %s", days, exc)
        return {
            "status": "error",
            "reason": str(exc)[:300],
            "granularity": "day",
            "days": days,
            "series": {m: [] for m in reply_queue_series.MEASURES},
            "prev": {},
        }


@router.post("/reply-queue/screen")
def api_screen(
    limit: int = Query(default=500, ge=1, le=5000),
    platform: str = Query(default=""),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """从 vkpi_comments 扫购买意向评论入队(pending,幂等)。"""
    from app.domains.comments import reply_queue
    return reply_queue.screen_intents(limit=limit, platform=platform)


class EnqueueCommentBody(BaseModel):
    comment_id: int = Field(..., ge=1, description="vkpi_comments.id(市场之声反馈流单条)")


@router.post("/reply-queue/enqueue-comment")
def api_enqueue_comment(
    body: EnqueueCommentBody,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """市场之声 V0e「转回复队列」:把 vkpi_comments 单条评论手动入队(幂等)。

    同 comment(平台 + external_comment_id)已在队 → 返回已有行(already_queued=True),
    绝不重复入队;评论不存在 404;缺幂等键/空正文等入不了队的情况诚实 400 带 reason。
    纯增量端点,不改 screen(全量筛)语义。
    """
    from app.domains.comments import reply_queue
    result = reply_queue.enqueue_comment(body.comment_id)
    if not result.get("ok"):
        reason = str(result.get("reason") or "enqueue_failed")
        if reason == "comment_not_found":
            raise HTTPException(status_code=404, detail=reason)
        raise HTTPException(status_code=400, detail=reason)
    return result


@router.post("/reply-queue/{reply_id}/draft")
def api_draft(
    reply_id: int,
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """为一条队列项生成品牌口吻草稿(RAG over SKU + LLM,预算闸先行降级模板)。

    起草前按 staff 原子认领:抢占失败(claimed_by_other)或已终态(already_finalized)/并发改状态
    (status_conflict)→ 409,绝不并发重复烧 LLM。
    """
    from app.domains.comments import reply_queue
    result = reply_queue.draft_reply(reply_id, staff=staff)
    if not result.get("ok"):
        reason = str(result.get("reason") or "draft_failed")
        if reason == "not_found":
            raise HTTPException(status_code=404, detail=reason)
        if reason in ("claimed_by_other", "already_finalized", "status_conflict"):
            raise HTTPException(status_code=409, detail=reason)
        raise HTTPException(status_code=400, detail=reason)
    return result


@router.post("/reply-queue/{reply_id}/mark")
def api_mark(
    reply_id: int,
    status: str = Query(..., description="replied / dismissed / pending / drafted"),
    expected_status: str = Query(
        default="",
        description="乐观锁:前端最后看到的状态;不匹配则 409(防并发丢更新)",
    ),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """人工标记队列项状态(v0 不自动发帖,仅落状态)。

    乐观锁:带 expected_status 时,若行的当前状态已被他人改动则返回 409 status_conflict。
    """
    from app.domains.comments import reply_queue
    result = reply_queue.mark(reply_id, status, expected_status=expected_status)
    if not result.get("ok"):
        reason = str(result.get("reason") or "mark_failed")
        if reason == "not_found":
            raise HTTPException(status_code=404, detail=reason)
        if reason == "status_conflict":
            raise HTTPException(status_code=409, detail=reason)
        raise HTTPException(status_code=400, detail=reason)
    return result
