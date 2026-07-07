"""V-KPI 本地算力 Worker 路由(W1 地基件,契约五端点)。

- POST /api/admin/vkpi/local-workers/devices/register            注册设备
- POST /api/admin/vkpi/local-workers/devices/{device_id}/heartbeat 心跳
- POST /api/admin/vkpi/local-workers/lease                       领活(短期任务 token)
- POST /api/admin/vkpi/local-workers/lease/{lease_id}/submit     提交结果(服务端校验)
- GET  /api/admin/vkpi/local-workers/devices                     设备列表(在线态+当前任务)

实现全部在 app.domains.local_workers.registry(路由只做薄壳转发+错误翻译)。
安全红线:本地 worker 永不持有长期 API key,只拿短期任务 token;
提交结果只落 staging 表,服务端校验通过才可信,绝不直写核心业务表;
零触 viltrox_fit_score、零触 rule_v0。

诚实态:设备/租约不存在 404;参数越界(白名单外任务类型等)400;
token 无效/过期不 5xx,诚实回 accepted=false 与 error_code。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/vkpi/local-workers", tags=["vkpi-local-workers"])


@router.post("/devices/register")
def register_device(
    device_name: str = Body(..., embed=True),
    platform: str = Body(default="", embed=True),
    capabilities: dict[str, Any] | None = Body(default=None, embed=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """注册本地算力设备(幂等:同员工+同名+同平台复用既有 device_id)。"""
    from app.domains.local_workers import registry

    staff_id = None
    try:
        staff_id = int(staff.get("staff_id") or staff.get("id") or 0) or None
    except (TypeError, ValueError, AttributeError):
        staff_id = None
    try:
        return registry.register_device(
            device_name=device_name,
            platform=platform,
            capabilities=capabilities,
            staff_id=staff_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/devices/{device_id}/heartbeat")
def device_heartbeat(
    device_id: str,
    current_task: str | None = Body(default=None, embed=True),
    stats: dict[str, Any] | None = Body(default=None, embed=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """设备心跳:刷新在线态与运行时信息。"""
    del staff
    from app.domains.local_workers import registry

    try:
        return registry.heartbeat(device_id, current_task=current_task, stats=stats)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/lease")
def lease_task(
    device_id: str = Body(..., embed=True),
    task_types: list[str] | None = Body(default=None, embed=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """领活:只发四类安全任务(video_precheck/metadata_extract/download_frames/comment_clean),
    返回短期 task_token(HMAC 签发,库里只存 hash);没有可领任务回 {status:'no_task'}。"""
    del staff
    from app.domains.local_workers import registry

    try:
        return registry.lease_task(device_id, task_types)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/lease/{lease_id}/submit")
def submit_lease_result(
    lease_id: int,
    task_token: str = Body(..., embed=True),
    result: dict[str, Any] | None = Body(default=None, embed=True),
    files_meta: list[dict[str, Any]] | None = Body(default=None, embed=True),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """提交结果:token 三验(hash/状态/过期)+ 服务端校验后只落 staging 表。"""
    del staff
    from app.domains.local_workers import registry

    try:
        return registry.submit_result(
            int(lease_id),
            task_token=task_token,
            result=result,
            files_meta=files_meta,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/devices")
def list_devices(
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """设备列表(含在线态/当前任务);顺手回收过期租约,看板永远看到干净读数。"""
    del staff
    from app.domains.local_workers import registry

    try:
        registry.reclaim_expired()
    except Exception as exc:  # noqa: BLE001 — 回收失败不挡看板读数,诚实记日志
        logger.warning("reclaim_expired failed (list_devices continues): %s", exc)
    return registry.list_devices()
