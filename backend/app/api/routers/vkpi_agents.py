"""路线1 · Agent 编排 API(PLAN-ONLY)。

POST /api/admin/vkpi/agents/plan      — 一句话目标 → 分步计划(只产计划不执行)。
GET  /api/admin/vkpi/agents/tools     — 工具白名单 manifest。
GET  /api/admin/vkpi/agents/plan/{id} — 读单条计划留痕。
红线:编排器 PLAN-ONLY;写库/烧 LLM 步骤走 Action Inbox 人审;零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.perms import require_tab
from app.domains.agents import orchestrator, tool_registry

router = APIRouter(prefix="/api/admin/vkpi/agents", tags=["vkpi-agents"])


@router.post("/plan")
def plan(
    body: dict = Body(default_factory=dict),
    staff=Depends(require_tab("vkpi", "write")),
) -> dict[str, Any]:
    """一句话目标 → 分步计划(PLAN-ONLY,不执行)。body: {goal, context?}。"""
    goal = str((body or {}).get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal required")
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    return orchestrator.plan_goal(goal, context=context, staff=staff)


@router.get("/tools")
def tools(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """工具白名单 manifest。"""
    return {"tools": tool_registry.list_tools(), "count": len(tool_registry.TOOL_REGISTRY)}


@router.get("/plan/{plan_id}")
def read_plan(
    plan_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """读单条计划留痕。"""
    item = orchestrator.get_plan(int(plan_id), staff=staff)
    if item is None:
        raise HTTPException(status_code=404, detail="plan not found")
    return item


@router.get("/kol/{kol_pool_id}/provenance")
def kol_provenance(
    kol_pool_id: int,
    staff=Depends(require_tab("vkpi", "read")),
) -> dict[str, Any]:
    """路线3 · 系统"为什么记住这个 KOL"(视频/深析/项目/ROI/漏斗 provenance,只读)。

    让 Agent 建议能引用来源(来自哪条视频 / 哪个项目),而非只看实时 query。零触 viltrox_fit_score。
    """
    from app.domains.memory import provenance

    return provenance.get_kol_provenance(int(kol_pool_id), staff=staff)
