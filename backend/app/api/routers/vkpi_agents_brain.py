"""Marketing Brain 只读/编排路由簇(行为不变搬迁,治 fan-out)。

从 vkpi_agents.py 整组 move 来的 marketing-brain 日报/刷新 + skills 编排三端点 +
data-catalog(子 router 无 prefix,挂回父 router 的 /api/admin/vkpi/agents 前缀下);
父文件在原位置 include_router 兜住,路由路径与响应契约逐字节不变。

`_require_legacy_agent_scope` 帮手随簇下沉到本叶子(父文件 import 回去复用,
方向恒父→子,零环)。红线:PLAN-ONLY / dry_run 默认 true;零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.dependencies.legacy_scope import legacy_system_admin_scope_guard
from app.api.dependencies.manager_guard import require_manager_tab
from app.api.dependencies.perms import require_tab

router = APIRouter()


def _require_legacy_agent_scope(staff: dict[str, Any], *, surface: str) -> None:
    unavailable = legacy_system_admin_scope_guard(staff, surface=surface)
    if unavailable is not None:
        raise HTTPException(status_code=403, detail=unavailable)


@router.get("/data-catalog")
def data_catalog(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """Data Catalog:每个指标自报来源/真假(real|awaiting_source)/新鲜度——数字可追溯真假。"""
    from app.domains.lineage import catalog

    return catalog.build_data_catalog()


@router.get("/marketing-brain/daily")
def marketing_brain_daily(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """cut1 · Market Brain v1 日报:每日合成产品热/上升渠道/竞品动/机会窗/今日建议(只读)。"""
    from app.domains.market import market_brain

    return market_brain.build_daily_brief(staff, sweep_expired=False)


@router.post("/marketing-brain/refresh")
def marketing_brain_refresh(
    staff=Depends(require_manager_tab("vkpi", "write")),
) -> dict[str, Any]:
    """cut1 · 活体化:治理过期信号 + 重出日报(运营手动刷;调度器每日自动跑)。"""
    from app.domains.market import market_brain

    expired = market_brain.mark_expired_signals()
    brief = market_brain.build_daily_brief(staff, sweep_expired=False)
    return {"status": "ok", "expired_swept": expired, "brief": brief}


@router.post("/skills/orchestrate")
def skills_orchestrate(body: dict = Body(default_factory=dict), staff=Depends(require_manager_tab("vkpi", "write"))) -> dict[str, Any]:
    """编排器侧接线:据 goal+context 选 skill 并经 registry 真调用(非仅人工 HTTP)。
    body: {goal(必填), context?, dry_run?(默认 true)}。dry_run=true 走规则不烧 LLM;预算闸+gate 守门;零触 fit。"""
    _require_legacy_agent_scope(staff, surface="Agent skill orchestration")
    from app.domains.marketing_brain import skill_orchestrator
    goal = str((body or {}).get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal required")
    context = body.get("context") if isinstance(body.get("context"), dict) else {}
    dry_run = body.get("dry_run", True)
    dry_run = True if dry_run is None else bool(dry_run)
    return skill_orchestrator.orchestrate_skills(goal, context=context, dry_run=dry_run, record=True, staff=staff)


@router.get("/skills/plan")
def skills_plan(goal: str, staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """PLAN-ONLY 预览:编排器据 goal 会选哪些 skill + 各自 input(不执行)。"""
    from app.domains.marketing_brain import skill_orchestrator
    return skill_orchestrator.plan_skills(goal)


@router.get("/skills/evals")
def skills_evals(staff=Depends(require_tab("vkpi", "read"))) -> dict[str, Any]:
    """跑全部 5 个 skill 的 evaluate(),返回诚实 per-skill hit_rate + 汇总(creator_match 默认 fixture 模式)。"""
    from app.domains.marketing_brain import evals as mb_evals
    return mb_evals.run_skill_evals()
