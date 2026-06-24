"""路线1 · Orchestrator(PLAN-ONLY)—— 一句话目标 → 分步计划,绝不直接执行。

红线(强约束):
- PLAN-ONLY:只产计划(steps),从不执行任何步骤。写库 / 烧 LLM 的步骤标 requires_approval=True,
  真正落地仍走 Action Inbox 人审 + executor。
- 零触 viltrox_fit_score / rule_v0;不自动花钱;计划只 INSERT 自身留痕表,绝不碰业务表。
- v1 用确定性规则planner(零 LLM、零成本);后续可换 LLM planner(走 llm_gateway 预算闸)。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, table_exists
from app.domains.access import scope
from app.domains.agents import tool_registry

logger = get_logger(__name__)

_TIER_COST = {"high": 300, "medium": 80, "low": 10}


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _plan_steps(goal: str) -> list[dict[str, Any]]:
    """确定性规则:据目标关键词选一条合理工具链(找人→抓档→[话术]→建草案)。

    绝不臆造执行;只排"该走哪几步"。写库/烧LLM步骤继承注册表的 requires_approval。
    """
    g = str(goal or "")
    gl = g.lower()
    seq: list[str] = ["search_kol", "scan_profile"]
    if any(k in g for k in ("话术", "合作", "邀约")) or any(k in gl for k in ("outreach", "collab", "invite")):
        seq.append("generate_outreach")
    seq.append("create_project_draft")

    steps: list[dict[str, Any]] = []
    for i, tid in enumerate(seq):
        t = tool_registry.get_tool(tid) or {}
        steps.append({
            "step_index": i,
            "tool_id": tid,
            "name": t.get("name", ""),
            "writes_db": bool(t.get("writes_db")),
            "uses_llm": bool(t.get("uses_llm")),
            "cost_tier": t.get("cost_tier", "low"),
            # 红线:写库/烧LLM 一律需人审
            "requires_approval": bool(t.get("requires_approval") or t.get("writes_db") or t.get("uses_llm")),
            "endpoint": t.get("endpoint", ""),
        })
    return steps


def plan_goal(goal: str, *, context: dict[str, Any] | None = None, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """一句话目标 → 分步计划(PLAN-ONLY)。留痕进 vkpi_agent_orchestration_plan,绝不执行。"""
    steps = _plan_steps(goal or "")
    est = sum(_TIER_COST.get(s["cost_tier"], 10) for s in steps)

    plan_id: int | None = None
    if table_exists("vkpi_agent_orchestration_plan"):
        try:
            conn = get_conn()
            row = conn.execute(
                """
                INSERT INTO vkpi_agent_orchestration_plan
                  (goal, input_context_json, plan_json, status, estimated_cost_cents, created_by_staff_id)
                VALUES (?, ?::jsonb, ?::jsonb, 'planned', ?, ?)
                RETURNING id
                """,
                (
                    str(goal or "")[:2000],
                    _dumps(context or {}),
                    _dumps(steps),
                    int(est),
                    int(scope.actor_staff_id(staff)) or None,
                ),
            ).fetchone()
            conn.commit()
            plan_id = int(dict(row)["id"]) if row else None
        except Exception:
            logger.warning("orchestrator.plan_persist_failed", exc_info=True)

    return {
        "plan_id": plan_id,
        "goal": goal,
        "steps": steps,
        "estimated_cost_cents": est,
        "status": "planned",
        "requires_approval_steps": [s["step_index"] for s in steps if s["requires_approval"]],
        "note": "PLAN-ONLY:本计划不执行任何步骤;写库 / 烧 LLM 的步骤需经 Action Inbox 人审后才跑;零触 viltrox_fit_score。",
    }


def materialize_plan_to_inbox(plan_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """H5 · plan→action 接通:把计划的每一步物化成 Action Inbox 可审批项(零自动执行)。

    红线:仅 CREATE status='suggested' 的建议;写库/烧 LLM 步骤 requires_approval=True,
    真执行仍走人审→executor。绝不在此执行任何步骤;零触 viltrox_fit_score。
    """
    plan = get_plan(plan_id, staff=staff)
    if not plan:
        return {"status": "not_found", "plan_id": plan_id}
    steps = plan.get("plan_json") or []
    if not isinstance(steps, list) or not steps:
        return {"status": "no_steps", "plan_id": plan_id}
    from app.domains.actions import inbox, producers

    actor = None
    try:
        actor = int(scope.actor_staff_id(staff)) or None
    except Exception:
        actor = None
    suggestions = []
    for s in steps:
        idx = int(s.get("step_index", 0))
        tid = str(s.get("tool_id") or "")
        suggestions.append(producers.make_suggestion(
            category="orchestrated_step",
            dedupe_key=f"plan:{plan_id}:step:{idx}",
            title=f"计划步骤 {idx + 1}:{s.get('name') or tid}",
            detail=f"目标「{plan.get('goal') or ''}」的第 {idx + 1} 步(工具 {tid})",
            reason=f"编排计划 #{plan_id} 的步骤;经此审批后由对应能力执行",
            priority="medium",
            suggested_endpoint=str(s.get("endpoint") or ""),
            writes_business_data=bool(s.get("writes_db")),
            uses_llm=bool(s.get("uses_llm")),
            requires_approval=bool(s.get("requires_approval", True)),
            owner_staff_id=actor,
            payload={"plan_id": plan_id, "step_index": idx, "tool_id": tid},
        ))
    persisted = inbox.persist_suggestions(suggestions)
    return {
        "status": "ok",
        "plan_id": plan_id,
        "steps_materialized": persisted,
        "note": "计划已物化为 Action Inbox 可审批项;每步需人审后才执行;零触 viltrox_fit_score。",
    }


def get_plan(plan_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """读单条计划(留痕)。缺表/不存在 → None。"""
    del staff
    if not table_exists("vkpi_agent_orchestration_plan"):
        return None
    row = get_conn().execute(
        "SELECT id, goal, input_context_json, plan_json, status, estimated_cost_cents, created_by_staff_id, created_at, updated_at "
        "FROM vkpi_agent_orchestration_plan WHERE id = ?",
        (int(plan_id),),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    for key in ("input_context_json", "plan_json"):
        val = item.get(key)
        if isinstance(val, str):
            try:
                item[key] = json.loads(val)
            except Exception:
                item[key] = {} if key == "input_context_json" else []
    return item
