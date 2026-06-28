"""任务队列只读侧栏投影端点(从 vkpi_kol_pool.py 抽出,行为不变)。

无自身 prefix:被 vkpi_kol_pool.router include 后继承 /api/admin/vkpi,路径不变。
与 KOL Pool 零业务耦合,只依赖 task_queue_view。红线:纯只读,零触 viltrox_fit_score。
"""
from __future__ import annotations

import app.domains.tasks.queue_view as task_queue_view
from fastapi import APIRouter, Depends, Query

from app.api.dependencies.perms import require_tab

router = APIRouter(tags=["vkpi-task-queue"])


@router.get("/task-queue")
def get_vkpi_task_queue(
    limit: int = Query(default=50, ge=1, le=100),
    recent_minutes: int = Query(default=10, ge=1, le=120),
    include_llm_calls: bool = Query(default=True),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Read-only sidebar task queue projection; no worker/provider side effects."""
    # 波2 R1:重型端点同样按 viewer 遮蔽(此前 del staff 绕过 compact 隐私)
    return task_queue_view.get_task_queue(
        limit=int(limit),
        recent_minutes=int(recent_minutes),
        include_llm_calls=bool(include_llm_calls),
        viewer=staff if isinstance(staff, dict) else None,
    )


@router.get("/task-queue/compact")
def get_vkpi_task_queue_compact(
    limit: int = Query(default=30, ge=1, le=50),
    recent_minutes: int = Query(default=5, ge=1, le=30),
    staff=Depends(require_tab("vkpi", "read")),
) -> dict:
    """Cached read-only sidebar task queue projection for 2.5s polling."""
    # viewer 用于队列隐私(非管理员只见他人任务的存在与位次,内容遮蔽)——缓存仍全员共享。
    return task_queue_view.get_task_queue_compact(
        limit=int(limit),
        recent_minutes=int(recent_minutes),
        viewer=staff if isinstance(staff, dict) else None,
    )
