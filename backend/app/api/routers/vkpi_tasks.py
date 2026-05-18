"""V-KPI async task facade routes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.dependencies.perms import require_tab
from app.services.vkpi import task_enqueue


router = APIRouter(prefix="/api/admin/vkpi/tasks", tags=["vkpi-tasks"])


def _queue(request: Request):
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        raise HTTPException(status_code=503, detail="job queue unavailable")
    return queue


@router.post("")
async def create_vkpi_task(body: dict, request: Request, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return await task_enqueue.enqueue_vkpi_task(
            _queue(request),
            str(body.get("task_type") or ""),
            body.get("params") or {},
            priority=body.get("priority"),
            timeout_seconds=body.get("timeout_seconds"),
            staff=staff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
def list_vkpi_tasks(
    status: str = "",
    task_type: str = "",
    user_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    staff=Depends(require_tab("vkpi", "read")),
):
    return task_enqueue.list_tasks(status=status, task_type=task_type, user_id=user_id, limit=limit, staff=staff)


@router.get("/{task_id}")
def get_vkpi_task(task_id: str, staff=Depends(require_tab("vkpi", "read"))):
    try:
        return task_enqueue.get_task(task_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/cancel")
def cancel_vkpi_task(task_id: str, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return task_enqueue.cancel_task(task_id, staff=staff)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{task_id}/retry")
async def retry_vkpi_task(task_id: str, request: Request, staff=Depends(require_tab("vkpi", "write"))):
    try:
        return await task_enqueue.retry_task(_queue(request), task_id, staff=staff)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
