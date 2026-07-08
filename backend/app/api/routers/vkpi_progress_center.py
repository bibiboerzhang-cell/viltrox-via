"""U1 全局任务进度中心(顶栏)只读聚合端点。

GET /api/admin/vkpi/progress/center —— 一次请求喂顶栏 TopProgressCenter:
  跑中任务(含进度%/阶段/ETA)+ 排队深度与各任务 ETA + 最近完成 5 条。

纯读投影,零写库、零 worker 触碰、零 LLM 调用。口径完全同源复用
app.domains.tasks.queue_view(侧栏任务板同款):
  - 活跃/最近列表、排队位次与 eta_seconds、stage 推断、隐私遮蔽
    (_apply_viewer_visibility)全部来自 get_task_queue(viewer=staff);
  - 跑中任务的进度% 是本模块唯一新增口径:elapsed(now-started_at,迁移112
    的 worker claim 时间)/ 近7天同类型 done 均时(_avg_duration_by_job_type,
    与排队 ETA 同一均时表),clamp 3..95 —— 只是「大约走到哪了」的观感估算,
    不是真实完成度,故永不显示 100%(落库完成由 recent_done 说话)。

红线:不触 viltrox_fit_score / rule_v0;显示层宪法照守 —— 不透出任何内部评分。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import app.domains.tasks.queue_view as task_queue_view
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.dependencies.perms import require_tab, require_tab_stream
from app.core.logging import get_logger
from app.db.connection import get_conn

try:
    from sse_starlette.sse import EventSourceResponse

    _SSE_AVAILABLE = True
except ImportError:
    _SSE_AVAILABLE = False
    EventSourceResponse = None

router = APIRouter(prefix="/api/admin/vkpi/progress", tags=["vkpi-progress-center"])

logger = get_logger(__name__)

# A4 聚合事件流:server 端轻量轮询转推 —— 每 _STREAM_INTERVAL_SEC 重算同源投影,
# 仅当投影 diff(排除易变的 generated_at)才 push 一帧 snapshot。不减后端查询,但减
# 前端连接数 + 让前端近实时;SSE 依赖缺失 / 断线一律优雅收尾,前端 useEventStreamOrPoll
# 自动无感回退固定间隔轮询(行为与切换前完全一致)。
_STREAM_INTERVAL_SEC = 4.0

# 跑中/排队之外不进 tasks 列表(recent 单列)。与 queue_view ACTIVE_STATUSES 对齐,
# 但本端点 tasks 只分两桶:running(含 processing/retrying 归并)与 queued。
_RUNNING_STATUSES = {"running", "processing"}
_QUEUED_STATUSES = {"queued", "retrying"}

# 阶段流(前端 4 步文案:队列中→抓取→分析→落库)。stage 值与 queue_view 同源。
STAGE_FLOW: list[dict[str, str]] = [
    {"stage": "queued", "label": "队列中"},
    {"stage": "search", "label": "抓取"},
    {"stage": "thinking", "label": "分析"},
    {"stage": "summarizing", "label": "落库"},
]


def _started_at_by_apify_id(conn: Any) -> dict[str, Any]:
    """跑中 apify_jobs 的 started_at(worker claim 时写,迁移112)。

    queue_view 的 apify 投影不带 started_at(历史契约,不动它),这里补一层
    只读小查询按 id 回填,供进度% 估算。失败回空 dict —— 进度% 缺省为 None,
    前端降级为不定长呼吸条,端点本身不炸。
    """
    try:
        rows = conn.execute(
            """
            SELECT id, started_at
            FROM apify_jobs
            WHERE status IN ('processing', 'running') AND started_at IS NOT NULL
            LIMIT 200
            """,
        ).fetchall()
        return {str(dict(r).get("id")): dict(r).get("started_at") for r in rows}
    except Exception:
        logger.warning("progress_center: started_at 回填查询失败(降级为无进度%)", exc_info=True)
        return {}


def _running_progress(
    item: dict[str, Any],
    started_by_id: dict[str, Any],
    avg_by_type: dict[str, float],
    now: datetime,
) -> tuple[int | None, int | None]:
    """跑中任务:(progress_pct, eta_seconds)。started_at 不可得时 (None, None)。"""
    job_type = str(item.get("job_type") or "")
    expected = float(avg_by_type.get(job_type, task_queue_view.DEFAULT_JOB_DURATION_SEC))
    expected = max(expected, 30.0)
    started_raw = item.get("started_at") or started_by_id.get(str(item.get("id")))
    started = task_queue_view._as_datetime(started_raw)
    if started is None:
        return None, None
    elapsed = max(0.0, (now - started).total_seconds())
    pct = int(min(95.0, max(3.0, elapsed / expected * 100.0)))
    eta = int(max(10.0, expected - elapsed))
    return pct, eta


def _project_task(item: dict[str, Any]) -> dict[str, Any]:
    """瘦身投影:顶栏抽屉只要展示级字段,不透传 payload 级细节。"""
    target = item.get("target") if isinstance(item.get("target"), dict) else {}
    label = str(
        target.get("label")
        or target.get("source_url")
        or target.get("target_id")
        or ""
    ).strip()
    out: dict[str, Any] = {
        "id": item.get("id"),
        "source": item.get("source"),
        "kind": item.get("kind"),
        "job_type": item.get("job_type"),
        "label": label or None,
        "platform": target.get("platform"),
        "kol_pool_id": target.get("kol_pool_id"),
        "status": item.get("status"),
        "stage": item.get("stage"),
        "stage_label": item.get("stage_label"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "masked": bool(item.get("masked")),
    }
    # 排队字段透传(queue_view 已算好:位次/前方数/预计等待)
    for key in ("queue_position", "ahead_count", "eta_seconds"):
        if item.get(key) is not None:
            out[key] = item.get(key)
    return out


def _project_recent(item: dict[str, Any]) -> dict[str, Any]:
    target = item.get("target") if isinstance(item.get("target"), dict) else {}
    return {
        "id": item.get("id"),
        "source": item.get("source"),
        "kind": item.get("kind"),
        "label": str(target.get("label") or "").strip() or None,
        "status": item.get("status"),
        "finished_at": item.get("finished_at") or item.get("updated_at") or item.get("created_at"),
        "has_error": bool(item.get("error")),
        "masked": bool(item.get("masked")),
    }


def _build_center_payload(viewer: dict | None, limit: int, recent_minutes: int) -> dict:
    """顶栏进度中心一次性聚合(纯读)。轮询端点与聚合流端点同源复用此投影。"""
    snapshot = task_queue_view.get_task_queue(
        limit=int(limit),
        recent_minutes=int(recent_minutes),
        include_llm_calls=False,
        viewer=viewer,
    )
    now = datetime.now(timezone.utc)

    conn = get_conn()
    avg_by_type = task_queue_view._avg_duration_by_job_type(conn)
    started_by_id = _started_at_by_apify_id(conn)

    running: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []
    for item in snapshot.get("active") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "")
        task = _project_task(item)
        if status in _RUNNING_STATUSES:
            pct, eta = _running_progress(item, started_by_id, avg_by_type, now)
            task["progress_pct"] = pct
            if eta is not None and task.get("eta_seconds") is None:
                task["eta_seconds"] = eta
            running.append(task)
        elif status in _QUEUED_STATUSES:
            task["progress_pct"] = 0
            queued.append(task)

    recent_done = [
        _project_recent(item)
        for item in (snapshot.get("recent") or [])[:5]
        if isinstance(item, dict)
    ]

    counts = snapshot.get("counts") or {}
    return {
        "status": "ready",
        "generated_at": now.isoformat(),
        "counts": {
            # queue_view C1 真实计数(不受列表 LIMIT 截断)
            "running": int(counts.get("running") or 0),
            "queued": int(counts.get("queued") or 0),
            "active_total": int(counts.get("active_total") or 0),
            "recent_total": int(counts.get("recent_total") or 0),
        },
        "running": running[: int(limit)],
        "queued": queued[: int(limit)],
        "recent_done": recent_done,
        "stage_flow": STAGE_FLOW,
        "polling": {"recommended_interval_ms": 10000},
        "diagnostics": {
            "source": "task_queue_view.get_task_queue(include_llm_calls=False)+apify_jobs.started_at",
            "progress_model": "elapsed(now-started_at)/avg7d(job_type) clamp 3..95; unknown→null",
            "write_db": False,
            "llm_calls": False,
            "worker_touched": False,
        },
    }


def _center_signature(payload: dict) -> str:
    """diff 指纹:排除每拍必变的 generated_at,只在真实投影变化时才推。"""
    return json.dumps(
        {k: v for k, v in payload.items() if k != "generated_at"},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


@router.get("/center")
def get_progress_center(
    limit: int = Query(default=20, ge=1, le=50),
    # 默认取 queue_view 上限 120 分钟:「最近完成」流水尽量有料(闲时队列常整点空窗)。
    recent_minutes: int = Query(default=120, ge=1, le=120),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """顶栏进度中心一次性聚合(纯读)。10s 轮询友好:单请求 ≤3 个只读查询。"""
    viewer = staff if isinstance(staff, dict) else None
    return _build_center_payload(viewer, int(limit), int(recent_minutes))


@router.get("/center/stream")
async def stream_progress_center(
    request: Request,
    limit: int = Query(default=20, ge=1, le=50),
    recent_minutes: int = Query(default=120, ge=1, le=120),
    staff=Depends(require_tab_stream("vkpi", "read")),
):
    """A4 顶栏进度中心聚合事件流:与 GET /center 同源同投影,仅传输形态不同。

    server 端每 _STREAM_INTERVAL_SEC 重算投影,diff 变化才 push 一帧 `snapshot` 事件;
    首拍必推(播首屏)。客户端断开 / 聚合异常一律优雅收尾断流,前端自动回退轮询。
    SSE 依赖缺失 → 503 JSON(前端同样回退轮询,骨架不炸)。
    """
    if not _SSE_AVAILABLE:
        return JSONResponse(status_code=503, content={"error": "SSE not available. Install sse-starlette."})
    viewer = staff if isinstance(staff, dict) else None
    lim = int(limit)
    rec = int(recent_minutes)

    async def event_generator():
        last_signature: str | None = None
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.to_thread(_build_center_payload, viewer, lim, rec)
            except Exception:
                logger.warning("progress_center/stream 聚合失败,优雅收尾断流(前端回退轮询)", exc_info=True)
                break
            signature = _center_signature(payload)
            if signature != last_signature:
                last_signature = signature
                yield {"event": "snapshot", "data": json.dumps(payload, ensure_ascii=False, default=str)}
            await asyncio.sleep(_STREAM_INTERVAL_SEC)

    return EventSourceResponse(event_generator())
