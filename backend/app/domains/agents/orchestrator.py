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
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.agents import step_execution, tool_registry

logger = get_logger(__name__)

_TIER_COST = {"high": 300, "medium": 80, "low": 10, "none": 0}


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _bound_inputs(tool_id: str, context: dict[str, Any]) -> dict[str, Any]:
    """Bind only registry-declared fields into the immutable server plan row."""
    tool = tool_registry.get_tool(tool_id) or {}
    nested = context.get("tool_inputs") if isinstance(context.get("tool_inputs"), dict) else {}
    candidate = nested.get(tool_id) if isinstance(nested.get(tool_id), dict) else context
    allowed = list(tool.get("inputs", [])) + list(tool.get("optional_inputs", []))
    return {key: candidate[key] for key in allowed if key in candidate}


def _plan_steps(goal: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """确定性规则:据目标关键词选一条合理工具链(找人→抓档→[话术]→建草案)。

    绝不臆造执行;只排"该走哪几步"。写库/烧LLM步骤继承注册表的 requires_approval。
    """
    g = str(goal or "")
    gl = g.lower()
    ctx = context if isinstance(context, dict) else {}
    # Only these explicit entity-bound intents become locally executable plans.
    # Everything else retains the original PLAN-ONLY marketing tool chain.
    seq: list[str] = []
    if ctx.get("event_id") not in (None, "") and (
        any(k in g for k in ("活动", "收尾", "跟进"))
        or any(k in gl for k in ("event", "followup", "follow-up"))
    ):
        seq.append("ack_event_followup")
    if ctx.get("inventory_id") not in (None, "") and (
        any(k in g for k in ("库存", "补货", "预警"))
        or any(k in gl for k in ("inventory", "stock"))
    ):
        seq.append("ack_inventory_low")
    if ctx.get("project_id") not in (None, "") and ctx.get("assignment_id") not in (None, "") and (
        any(k in g for k in ("观察窗", "签收", "履约观察"))
        or any(k in gl for k in ("project observation", "observation window"))
    ):
        seq.append("check_project_observation")
    if not seq:
        seq = ["search_kol", "scan_profile"]
        if any(k in g for k in ("话术", "合作", "邀约")) or any(k in gl for k in ("outreach", "collab", "invite")):
            seq.append("generate_outreach")
        seq.append("create_project_draft")

    steps: list[dict[str, Any]] = []
    for i, tid in enumerate(seq):
        t = tool_registry.get_tool(tid) or {}
        estimated_cost_cents = int(
            t.get("estimated_cost_cents")
            if t.get("estimated_cost_cents") is not None
            else _TIER_COST.get(str(t.get("cost_tier") or "low"), 10)
        )
        steps.append({
            "step_index": i,
            "tool_id": tid,
            "name": t.get("name", ""),
            "writes_db": bool(t.get("writes_db")),
            "uses_llm": bool(t.get("uses_llm")),
            "cost_tier": t.get("cost_tier", "low"),
            "estimated_cost_cents": estimated_cost_cents,
            # 红线:写库/烧LLM 一律需人审
            "requires_approval": bool(t.get("requires_approval") or t.get("writes_db") or t.get("uses_llm")),
            "endpoint": t.get("endpoint", ""),
            "execution_policy": t.get("execution_policy", "plan_only"),
            "inputs": _bound_inputs(tid, ctx),
            "affected_tables": list(t.get("affected_tables") or []),
        })
    return steps


def plan_goal(goal: str, *, context: dict[str, Any] | None = None, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """一句话目标 → 分步计划(PLAN-ONLY)。留痕进 vkpi_agent_orchestration_plan,绝不执行。"""
    bound_context = context if isinstance(context, dict) else {}
    steps = _plan_steps(goal or "", bound_context)
    est = sum(int(s.get("estimated_cost_cents") or 0) for s in steps)

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
                    _dumps(bound_context),
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


def _materialization_actor(
    plan: dict[str, Any],
    *,
    staff: dict[str, Any] | None,
) -> tuple[int | None, int]:
    try:
        actor = int(scope.actor_staff_id(staff)) or None
    except Exception:
        actor = None
    try:
        plan_owner = int(plan.get("created_by_staff_id") or 0)
    except (TypeError, ValueError):
        plan_owner = 0
    return actor, plan_owner


def _validated_plan_step(
    plan_id: int,
    plan_owner: int,
    steps: list[Any],
    position: int,
    step: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    error_base = {"plan_id": plan_id, "step_index": position}
    if not isinstance(step, dict):
        return None, {"status": "invalid_plan_step", **error_base}
    try:
        idx = int(step.get("step_index"))
    except (TypeError, ValueError):
        return None, {"status": "invalid_plan_step", **error_base}
    if idx != position:
        return None, {"status": "invalid_plan_step", **error_base}
    tid = str(step.get("tool_id") or "")
    tool = tool_registry.get_tool(tid)
    if not tool:
        return None, {
            "status": "unknown_plan_tool",
            "plan_id": plan_id,
            "step_index": idx,
        }
    registry_mismatch = (
        str(step.get("endpoint") or "") != str(tool.get("endpoint") or "")
        or bool(step.get("writes_db")) != bool(tool.get("writes_db"))
        or bool(step.get("uses_llm")) != bool(tool.get("uses_llm"))
        or bool(step.get("requires_approval"))
        != bool(
            tool.get("requires_approval")
            or tool.get("writes_db")
            or tool.get("uses_llm")
        )
        or list(step.get("affected_tables") or [])
        != list(tool.get("affected_tables") or [])
    )
    if registry_mismatch:
        return None, {
            "status": "plan_registry_mismatch",
            "plan_id": plan_id,
            "step_index": idx,
        }
    inputs = step.get("inputs") if isinstance(step.get("inputs"), dict) else {}
    locally_executable = tool_registry.is_locally_executable(tid)
    input_check = tool_registry.validate_inputs(tid, inputs)
    if locally_executable and not input_check.get("ok"):
        return None, {
            "status": "plan_inputs_invalid",
            "plan_id": plan_id,
            "step_index": idx,
            "reason": input_check.get("reason"),
        }
    estimated_cost = int(
        tool.get("estimated_cost_cents")
        if tool.get("estimated_cost_cents") is not None
        else _TIER_COST.get(str(tool.get("cost_tier") or "low"), 10)
    )
    if int(step.get("estimated_cost_cents") or 0) != estimated_cost:
        return None, {
            "status": "plan_cost_mismatch",
            "plan_id": plan_id,
            "step_index": idx,
        }
    entity_type = ""
    entity_id = ""
    contract_sha256 = ""
    if locally_executable:
        entity_type = str(tool.get("entity_type") or "")
        entity_id = str(inputs.get(str(tool.get("entity_id_input") or "")) or "")
        try:
            contract_sha256 = str(
                step_execution.contract_for_plan_step(
                    int(plan_id),
                    plan_owner,
                    steps,
                    idx,
                )["fingerprint"]
            )
        except step_execution.StepExecutionRejected as exc:
            return None, {
                "status": "plan_contract_invalid",
                "plan_id": plan_id,
                "step_index": idx,
                "reason": exc.reason,
            }
    return {
        "step": step,
        "idx": idx,
        "tid": tid,
        "tool": tool,
        "estimated_cost": estimated_cost,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "contract_sha256": contract_sha256,
    }, None


def _step_suggestion(
    *,
    plan: dict[str, Any],
    plan_id: int,
    actor: int,
    validated: dict[str, Any],
    make_suggestion: Any,
) -> dict[str, Any]:
    step = validated["step"]
    idx = validated["idx"]
    tid = validated["tid"]
    tool = validated["tool"]
    contract_sha256 = validated["contract_sha256"]
    return make_suggestion(
        category="orchestrated_step",
        dedupe_key=f"plan:{plan_id}:step:{idx}",
        title=f"计划步骤 {idx + 1}:{step.get('name') or tid}",
        detail=f"目标「{plan.get('goal') or ''}」的第 {idx + 1} 步(工具 {tid})",
        reason=f"编排计划 #{plan_id} 的步骤;经此审批后由对应能力执行",
        priority="medium",
        entity_type=validated["entity_type"],
        entity_id=validated["entity_id"],
        suggested_endpoint=str(tool.get("endpoint") or ""),
        estimated_cost_cents=validated["estimated_cost"],
        writes_business_data=bool(tool.get("writes_db")),
        uses_llm=bool(tool.get("uses_llm")),
        requires_approval=bool(
            tool.get("requires_approval")
            or tool.get("writes_db")
            or tool.get("uses_llm")
        ),
        owner_staff_id=actor,
        payload={
            "plan_id": plan_id,
            "step_index": idx,
            "tool_id": tid,
            **({"contract_sha256": contract_sha256} if contract_sha256 else {}),
        },
        verification_plan=list(tool.get("verification_plan") or []),
        affected_tables=list(tool.get("affected_tables") or []),
    )


def _transition_plan_ready(plan_id: int, actor: int) -> bool:
    try:
        conn = get_conn()
        cursor = conn.execute(
            "UPDATE vkpi_agent_orchestration_plan SET status='ready', updated_at="
            + ("NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP")
            + " WHERE id=? AND status IN ('planned','ready') AND created_by_staff_id=?",
            (int(plan_id), int(actor or 0)),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except Exception:
        try:
            get_conn().rollback()
        except Exception:
            logger.debug("orchestrator.materialize_rollback_failed", exc_info=True)
        logger.warning(
            "orchestrator.materialize_ready_failed",
            extra={"plan_id": plan_id},
            exc_info=True,
        )
        return False


def materialize_plan_to_inbox(plan_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """H5 · plan→action 接通:把计划的每一步物化成 Action Inbox 可审批项(零自动执行)。

    红线:仅 CREATE status='suggested' 的建议;写库/烧 LLM 步骤 requires_approval=True,
    真执行仍走人审→executor。绝不在此执行任何步骤;零触 viltrox_fit_score。
    """
    plan = get_plan(plan_id, staff=staff)
    if not plan:
        return {"status": "not_found", "plan_id": plan_id}
    if str(plan.get("status") or "") not in {"planned", "ready"}:
        return {"status": "plan_not_materializable", "plan_id": plan_id}
    steps = plan.get("plan_json") or []
    if not isinstance(steps, list) or not steps:
        return {"status": "no_steps", "plan_id": plan_id}
    from app.domains.actions import inbox, producers

    actor, plan_owner = _materialization_actor(plan, staff=staff)
    if not actor or plan_owner != actor:
        return {"status": "plan_owner_mismatch", "plan_id": plan_id}
    suggestions = []
    for position, step in enumerate(steps):
        validated, error = _validated_plan_step(
            int(plan_id),
            plan_owner,
            steps,
            position,
            step,
        )
        if error is not None:
            return error
        suggestions.append(
            _step_suggestion(
                plan=plan,
                plan_id=int(plan_id),
                actor=actor,
                validated=validated or {},
                make_suggestion=producers.make_suggestion,
            )
        )
    persisted = inbox.persist_suggestions(suggestions)
    if persisted != len(suggestions):
        return {
            "status": "materialization_incomplete",
            "plan_id": plan_id,
            "steps_materialized": persisted,
            "expected_steps": len(suggestions),
        }
    if not _transition_plan_ready(int(plan_id), actor):
        return {"status": "plan_ready_transition_failed", "plan_id": plan_id}
    return {
        "status": "ok",
        "plan_id": plan_id,
        "steps_materialized": persisted,
        "note": "计划已物化为 Action Inbox 可审批项;每步需人审后才执行;零触 viltrox_fit_score。",
    }


def get_plan(plan_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """读单条计划留痕；成员仅可读本人计划，管理层可审计全部。"""
    if not table_exists("vkpi_agent_orchestration_plan"):
        return None
    actor_staff_id = int(scope.actor_staff_id(staff))
    if not actor_staff_id:
        return None
    sql = (
        "SELECT id, goal, input_context_json, plan_json, status, estimated_cost_cents, "
        "created_by_staff_id, created_at, updated_at "
        "FROM vkpi_agent_orchestration_plan WHERE id = ?"
    )
    params: tuple[Any, ...] = (int(plan_id),)
    if not scope.can_view_all(staff):
        sql += " AND created_by_staff_id = ?"
        params = (int(plan_id), actor_staff_id)
    row = get_conn().execute(sql, params).fetchone()
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
