"""Agent 建议链 Durable Workflow(P2 落业务)—— 每日 Agent 提案周期可恢复化。

链:detect_gap+propose(生成今日 Inbox)→ summarize(待审汇总)→ record(入事件流/记忆)。
人审→执行→验收→学习 走既有 approve_action/executor 路径(红线:Agent 不自动执行)。
红线:只编排既有 service;零自动写库裁决,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

Step = tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]


def _require_fence() -> dict[str, Any]:
    from app.domains.platform import workflow_engine

    return workflow_engine.require_workflow_fence()


def build_agent_cycle_steps(staff: dict[str, Any] | None = None) -> list[Step]:
    def s_detect(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.actions import inbox

        execution = _require_fence()
        r = inbox.generate_daily_action_inbox(staff, persist=True)
        return {
            "generated": r.get("persisted") or r.get("total") or 0,
            "by_category_generated": r.get("by_category", {}),
            "inbox_side_effect_key": execution["side_effect_key"],
            "external_exactly_once": False,
        }

    def s_summarize(state: dict[str, Any]) -> dict[str, Any]:
        from collections import Counter

        from app.domains.actions import inbox

        items = (inbox.list_inbox(staff, status="suggested", limit=100) or {}).get("items", [])
        return {"pending": len(items), "by_category": dict(Counter(str(i.get("category") or "") for i in items))}

    def s_record(state: dict[str, Any]) -> dict[str, Any]:
        from app.domains.platform import event_ledger

        execution = _require_fence()
        event_ledger.emit(
            "agent_cycle_completed", entity_type="agent", entity_id="daily",
            actor_type="agent", source="agent_cycle",
            payload={
                "generated": state.get("generated", 0),
                "pending": state.get("pending", 0),
                "workflow_side_effect_key": execution["side_effect_key"],
                "external_exactly_once": False,
            },
        )
        return {
            "recorded": True,
            "event_side_effect_key": execution["side_effect_key"],
            "external_exactly_once": False,
        }

    return [("detect_and_propose", s_detect), ("summarize_pending", s_summarize), ("record_cycle", s_record)]


def start_agent_cycle(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    """起一轮 Agent 建议周期(建 run → 三步,可恢复)。执行仍需人审(红线)。"""
    from app.domains.platform import workflow_engine

    started = workflow_engine.start_run("agent_cycle", input={}, entity_type="agent", entity_id="daily")
    run_id = started.get("run_id")
    if not run_id:
        return {"status": "unavailable", "detail": started}
    res = workflow_engine.run(int(run_id), build_agent_cycle_steps(staff))
    return {"run_id": run_id, **res, "note": "建议已生成待人审;批准后由 executor 执行→验收→学习(既有路径)。"}


def resume_agent_cycle(
    run_id: int,
    staff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """续跑失败、暂停或 lease 过期的 Agent 周期。"""

    from app.domains.platform import workflow_engine

    return workflow_engine.run(int(run_id), build_agent_cycle_steps(staff))
