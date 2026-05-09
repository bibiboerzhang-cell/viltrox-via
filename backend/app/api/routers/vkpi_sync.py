"""backend/app/api/routers/vkpi_sync.py

R60: Sync 状态监控路由

新增 endpoint:
  GET  /api/admin/vkpi/sync/overview              (read 即可)
  GET  /api/admin/vkpi/sync/industry/failures     (read 即可)
  POST /api/admin/vkpi/sync/trigger/{job_name}    (admin 权限,装饰器审计)

注: 现有 vkpi_industry_automation.py 有 industry refresh,
    本 router 是更高层的"监控 + 手动触发"统一入口.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.dependencies.perms import require_tab
from app.services.vkpi import sync_status, cron
from app.services.vkpi.audit_decorator import audit_action


router = APIRouter(prefix="/api/admin/vkpi/sync", tags=["vkpi-sync"])


# ─── Read endpoints ─────────────────────────────


@router.get("/overview")
def get_overview(staff=Depends(require_tab("vkpi", "read"))) -> dict:
    """
    获取 Sync 全景:
      - industry 同步状态
      - shopify 同步状态
      - cron job 运行历史
      - 平台设置 + budget
      - 整体健康度
    """
    return sync_status.get_overview()


@router.get("/industry/failures")
def get_industry_failures(
    limit: int = Query(default=50, ge=1, le=500),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """最近 industry 抓取失败列表"""
    return sync_status.get_industry_recent_failures(limit=limit)


# ─── Write endpoints (admin 权限 + 审计) ──────


_ALLOWED_JOBS = {
    "morning_sync",
    "kpi_rollup",
    "lineage_snapshot",
    "channels_sync",
    "weekly_report",
    "alerts",
    "analytics_monitor",
    "daily_outreach_digest_only",
}


@router.post("/trigger/{job_name}")
@audit_action(
    action_type="sync_trigger",
    target_type="cron_job",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("job_name", "")),
    detail_extractor=lambda result, kwargs: f"manual trigger {kwargs.get('job_name')}",
)
async def trigger_sync(
    job_name: str,
    body: dict | None = Body(default=None),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict:
    """
    手动触发 cron job (admin 权限).
    
    URL: job_name 路径参数,必须在 _ALLOWED_JOBS 内
    Body: 任意 cron payload 参数 (period_days / max_videos 等)
    """
    if job_name not in _ALLOWED_JOBS:
        raise HTTPException(
            status_code=400,
            detail=f"job '{job_name}' not allowed. Allowed: {sorted(_ALLOWED_JOBS)}",
        )
    
    payload = body or {}
    payload["staff"] = staff
    
    try:
        result = await cron.run_job(job_name, payload)
        # 装饰器记录的 audit log,这里加入 result 状态
        return {
            "job": job_name,
            "status": "ok",
            "result_summary": _safe_summary(result),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"job failed: {exc}") from exc


def _safe_summary(result: dict | None) -> dict:
    """取 result 的可序列化摘要,避免大对象进 audit"""
    if not isinstance(result, dict):
        return {}
    summary = {}
    for key in ("job", "status", "ran_at", "synced", "runs"):
        if key in result:
            summary[key] = result[key]
    return summary
