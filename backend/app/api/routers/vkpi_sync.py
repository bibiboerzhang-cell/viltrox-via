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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_tab
from app.domains import sync as sync_domain
from app.domains.sync import sync_status
from app.domains.sync import cron
from app.domains.audit.decorator import audit_action


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


@router.get("/sentinel/v0")
def get_sync_sentinel_v0(
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only P7.80 sync sentinel report. Does not trigger sync or write alerts."""
    del staff
    return sync_domain.build_sync_sentinel_agent_v0(limit=limit)


# ─── Write endpoints (admin 权限 + 审计) ──────


@router.post("/trigger/{job_name}")
@audit_action(
    action_type="sync_trigger",
    target_type="cron_job",
    target_id_extractor=lambda result, kwargs: str(kwargs.get("job_name", "")),
    detail_extractor=lambda result, kwargs: f"manual trigger {kwargs.get('job_name')}",
)
async def trigger_sync(
    request: Request,
    job_name: str,
    body: dict | None = Body(default=None),
    staff=Depends(require_tab("vkpi", "admin")),
) -> dict:
    """
    手动触发 cron job (admin 权限).
    
    URL: job_name 路径参数,必须在 cron.manual_job_names() 内.
    Body: 任意 cron payload 参数,但必须包含 confirm="RUN {job}".
    """
    if job_name not in cron.manual_job_names():
        raise HTTPException(
            status_code=400,
            detail=f"job '{job_name}' not allowed. Allowed: {cron.manual_job_names()}",
        )
    
    payload = body or {}
    payload["staff"] = staff
    
    try:
        result = await cron.run_manual_job(job_name, payload, staff=staff, queue=getattr(request.app.state, "job_queue", None))
        # 装饰器记录的 audit log,这里加入 result 状态
        return {
            "job": job_name,
            "status": "ok",
            "result_summary": _safe_summary(result),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
