"""V-KPI 失败 Triage 队列(10E·人工)路由。

背景:Wave1 让不可重试的失败 apify_jobs 分类停在 status='triage'(no_data/auth/下架/
代码错等),不再消耗重试预算,等待人工裁决。本路由把这条队列暴露给运维:

- GET  /api/admin/vkpi/triage/jobs              列出 status='triage' 的任务(可调 LIMIT);
- POST /api/admin/vkpi/triage/jobs/{id}/requeue 把某条人工重置回 status='queued',让 worker 再跑。

红线:只读 + 仅改 status(triage→queued)。零触 viltrox_fit_score,本文件绝不写该列。

compat 约定:占位符用 ?(非 %s);BOOLEAN 读回是 int 1/0 故用 _truthy 容错;
status 写明确字符串字面量。复用既有 apify_jobs 表,不加新迁移。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger
from app.db.connection import get_conn


logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi", tags=["vkpi-triage"])


def _iso(value: Any) -> str | None:
    """把 updated_at 安全序列化成 ISO 字符串(可能是 datetime / str / None)。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


@router.get("/triage/jobs")
def list_triage_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
):
    """列出停在人工 triage 的失败任务,按 updated_at DESC。"""
    del staff
    conn = get_conn()
    # apify_jobs 没有顶层 url 列;每条任务的目标 URL 存在 payload(JSONB)里,key 因 job_type
    # 而异(url / video_url / source_url / profile_url / target_url),用 COALESCE 兜常见键。
    rows = conn.execute(
        """
        SELECT
          id,
          COALESCE(job_type, '') AS job_type,
          COALESCE(
            payload->>'url',
            payload->>'video_url',
            payload->>'source_url',
            payload->>'profile_url',
            payload->>'target_url',
            ''
          ) AS url,
          COALESCE(last_error_category, '') AS category,
          COALESCE(last_error, '') AS error,
          updated_at
        FROM apify_jobs
        WHERE status = 'triage'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        items.append(
            {
                "id": int(data["id"]),
                "job_type": _text(data.get("job_type")) or None,
                "url": _text(data.get("url")) or None,
                "category": _text(data.get("category")) or None,
                "error": _text(data.get("error")) or None,
                "updated_at": _iso(data.get("updated_at")),
            }
        )

    return {"items": items, "count": len(items), "limit": limit}


@router.post("/triage/jobs/{job_id}/requeue")
def requeue_triage_job(
    job_id: int,
    staff=Depends(require_tab("vkpi", "admin")),
):
    """人工把一条 triage 任务重置回 queued,清掉重试退避让 worker 立刻可领。

    只对 status='triage' 的行生效(防误把已 done/failed 行复活);其它状态返回 409。
    attempts 不清零(保留审计),但清 next_retry_at 让其可被立即 claim;
    last_error / last_error_category 保留作为这条任务为何曾进 triage 的审计痕迹。
    """
    del staff
    conn = get_conn()

    existing = conn.execute(
        "SELECT id, status FROM apify_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if existing is None:
        raise HTTPException(status_code=404, detail="job not found")

    current_status = _text(dict(existing).get("status"))
    if current_status != "triage":
        raise HTTPException(
            status_code=409,
            detail=f"job is not in triage (status={current_status or 'unknown'})",
        )

    conn.execute(
        """
        UPDATE apify_jobs
        SET status = 'queued',
            next_retry_at = NULL,
            updated_at = NOW()
        WHERE id = ?
          AND status = 'triage'
        """,
        (job_id,),
    )
    try:
        conn.commit()
    except Exception:  # noqa: BLE001 — autocommit 环境无显式 commit,忽略
        logger.debug("requeue commit skipped (autocommit env)", exc_info=True)

    logger.warning("triage job requeued by operator | id=%s", job_id)
    return {"id": job_id, "status": "queued", "requeued": True}
