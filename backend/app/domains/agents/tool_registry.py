"""路线1 · Agent Tool Registry —— Agent 只能调白名单工具,绝不乱跑。

每个工具声明元数据:写库?烧 LLM?成本档?需审批?输入字段?对应既有 domain 端点。
注册表默认**只声明不执行**。只有显式 ``local_*_v1`` 的极小白名单可以在
Action Inbox 人审后复用既有本地 handler；其余工具继续 PLAN-ONLY。
红线:写库或烧 LLM 的工具一律 requires_approval=True(走 Action Inbox 人审);零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

# 第一批工具:全部映射到已存在的 domain 能力(端点仅作人话标注,编排器不在此调用)。
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "search_kol": {
        "name": "搜索 KOL", "writes_db": False, "uses_llm": True, "cost_tier": "low",
        "requires_approval": False, "inputs": ["query"],
        "endpoint": "POST /api/admin/vkpi/kol-smart-search",
    },
    "scan_profile": {
        "name": "抓取 KOL 主页(补全资料)", "writes_db": True, "uses_llm": False, "cost_tier": "medium",
        "requires_approval": True, "inputs": ["kol_pool_id"],
        "endpoint": "action: kol_profile / deep_missing executor",
    },
    "enqueue_video_analysis": {
        "name": "排队视频深析", "writes_db": True, "uses_llm": True, "cost_tier": "high",
        "requires_approval": True, "inputs": ["kol_pool_id"],
        "endpoint": "kol.video_analysis_enqueue",
    },
    "generate_outreach": {
        "name": "生成合作话术 + SOW 草案", "writes_db": False, "uses_llm": True, "cost_tier": "medium",
        "requires_approval": True, "inputs": ["session_id"],
        "endpoint": "POST /api/admin/vkpi/kol-search-sessions/{id}/generate-outreach",
    },
    "create_project_draft": {
        "name": "建项目草案(挂选中 KOL)", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": ["session_id"],
        "endpoint": "POST /api/admin/vkpi/kol-search-sessions/{id}/create-project-draft",
    },
    "check_project_observation": {
        "name": "为已签收派单补观察窗",
        "writes_db": True,
        "uses_llm": False,
        "cost_tier": "none",
        "estimated_cost_cents": 0,
        "requires_approval": True,
        "requires_manager": True,
        "inputs": ["project_id", "assignment_id"],
        "positive_int_inputs": ["project_id", "assignment_id"],
        "endpoint": "local-action:project_observation",
        "execution_policy": "local_state_change_v1",
        "handler_category": "project_observation",
        "entity_type": "project",
        "entity_id_input": "project_id",
        "affected_tables": ["vkpi_project_content_observation_windows"],
        "verification_plan": [
            "仅为锁定 project/assignment 的已签收派单创建缺失观察窗",
            "created window id 与表行数 delta 必须由服务端回执证明",
        ],
    },
    "retry_failed_analysis": {
        "name": "重试失败/受阻任务", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": ["job_id"],
        "endpoint": "action: failed_retry executor",
    },
    "sync_company_account": {
        "name": "同步公司官方账号", "writes_db": True, "uses_llm": False, "cost_tier": "low",
        "requires_approval": True, "inputs": [],
        "endpoint": "domains.sync.cron channels_sync",
    },
    # 两个 acknowledgement handler 只写 Action/Agent 审计账本；上面的
    # project_observation 是另行收口的精确本地 state-change allowlist。
    "ack_event_followup": {
        "name": "确认受理活动收尾提醒",
        "writes_db": False,
        "uses_llm": False,
        "cost_tier": "none",
        "estimated_cost_cents": 0,
        "requires_approval": True,
        "inputs": ["event_id"],
        "optional_inputs": ["missing"],
        "endpoint": "local-action:event_followup",
        "execution_policy": "local_ack_v1",
        "handler_category": "event_followup",
        "entity_type": "event",
        "entity_id_input": "event_id",
    },
    "ack_inventory_low": {
        "name": "确认受理库存预警",
        "writes_db": False,
        "uses_llm": False,
        "cost_tier": "none",
        "estimated_cost_cents": 0,
        "requires_approval": True,
        "inputs": ["inventory_id"],
        "optional_inputs": ["sku", "qty"],
        "endpoint": "local-action:inventory_low",
        "execution_policy": "local_ack_v1",
        "handler_category": "inventory_low",
        "entity_type": "inventory",
        "entity_id_input": "inventory_id",
    },
}


def list_tools() -> list[dict[str, Any]]:
    """工具清单(给前端/编排器看的 manifest)。"""
    return [{"tool_id": tid, **meta} for tid, meta in TOOL_REGISTRY.items()]


def get_tool(tool_id: str) -> dict[str, Any] | None:
    return TOOL_REGISTRY.get(str(tool_id or ""))


def validate_inputs(tool_id: str, inputs: dict[str, Any] | None) -> dict[str, Any]:
    """Strictly validate the server-bound input object for one tool.

    Required keys must be non-empty and executable tools reject unknown keys.
    This prevents a later Action payload from smuggling an alternate target or
    provider option into an otherwise safe plan step.
    """
    tool = get_tool(tool_id)
    if not tool:
        return {"ok": False, "reason": "unknown_tool"}
    if not isinstance(inputs, dict):
        return {"ok": False, "reason": "inputs_must_be_object", "missing": list(tool.get("inputs", []))}
    given = inputs
    required = list(tool.get("inputs", []))
    optional = list(tool.get("optional_inputs", []))
    missing = [k for k in required if given.get(k) in (None, "")]
    allowed = set(required) | set(optional)
    extra = sorted(str(k) for k in given if k not in allowed)
    strict = str(tool.get("execution_policy") or "").startswith("local_")
    invalid_ints: list[str] = []
    for key in tool.get("positive_int_inputs", []):
        value = given.get(str(key))
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 0
        if isinstance(value, bool) or parsed <= 0 or str(value).strip() != str(parsed):
            invalid_ints.append(str(key))
    ok = not missing and not invalid_ints and (not strict or not extra)
    result: dict[str, Any] = {"ok": ok, "missing": missing, "extra": extra}
    if not ok:
        result["reason"] = (
            "missing_inputs" if missing else "invalid_input" if invalid_ints else "unexpected_inputs"
        )
        if invalid_ints:
            result["invalid"] = invalid_ints
    return result


def is_locally_executable(tool_id: str) -> bool:
    """Return true only for the zero-provider, zero-cost v1 execution allowlist."""
    tool = get_tool(tool_id) or {}
    policy = str(tool.get("execution_policy") or "")
    handler = str(tool.get("handler_category") or "")
    common = bool(
        not bool(tool.get("uses_llm"))
        and int(tool.get("estimated_cost_cents") or 0) == 0
        and bool(tool.get("requires_approval"))
        and str(tool.get("endpoint") or "").startswith("local-action:")
    )
    if policy == "local_ack_v1":
        return common and not bool(tool.get("writes_db")) and handler in {
            "event_followup", "inventory_low",
        }
    if policy == "local_state_change_v1":
        return bool(
            common
            and bool(tool.get("writes_db"))
            and bool(tool.get("requires_manager"))
            and handler == "project_observation"
            and tool.get("affected_tables") == ["vkpi_project_content_observation_windows"]
        )
    return False
