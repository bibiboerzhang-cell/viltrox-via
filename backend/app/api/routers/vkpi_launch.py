"""V-KPI N3 Launch plan routes (additive).

POST /api/admin/vkpi/launch/{project_id}/plan
    为 source_type='launch' 的项目生成计划:KOL 候选(复用 new_launch_match,
    persist_run=False 只读)+ 内容验证任务占位 + 观察窗口占位。

红线:generate_launch_plan 纯只读 / 占位生成,不写库,零触 viltrox_fit_score。
本 router 为加性专属文件,不与他人改的 router 撞;include 注册行交主线整合进 main.py。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.domains.projects.launch_project import generate_launch_plan

router = APIRouter(prefix="/api/admin/vkpi/launch", tags=["vkpi-launch"])


@router.post("/{project_id}/plan")
def post_launch_plan(
    project_id: int,
    body: dict[str, Any] | None = Body(default=None),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """为 launch project 生成计划并返回(只读,不写库)。

    body 可选:``{"candidate_limit": <int 1..100>}``;缺省走 generate_launch_plan 的默认 20。
    """
    del staff  # 权限已由 require_tab 校验;本端点无 scope 二次过滤需求。
    payload = body if isinstance(body, dict) else {}
    raw_limit = payload.get("candidate_limit")
    candidate_limit = 20
    if raw_limit is not None:
        try:
            candidate_limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="candidate_limit must be an integer") from exc
        if candidate_limit < 1 or candidate_limit > 100:
            raise HTTPException(status_code=400, detail="candidate_limit must be between 1 and 100")

    try:
        return generate_launch_plan(int(project_id), candidate_limit=candidate_limit)
    except ValueError as exc:
        # _load_launch_project 找不到项目时抛 ValueError -> 404。
        raise HTTPException(status_code=404, detail=str(exc)) from exc
