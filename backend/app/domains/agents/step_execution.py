"""Fail-closed resolution for plan-linked ``orchestrated_step`` Actions.

The Action row is only a pointer.  Tool identity, endpoint, inputs, cost, and
target entity are resolved from the locked server-side plan row and checked
against the registry.  The allowlist contains local acknowledgement handlers
and one exact, zero-provider observation-window state change; every other
business-write, LLM, provider, or external-network tool remains PLAN-ONLY.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any

from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.access import scope
from app.domains.agents import tool_registry

_PLAN_TABLE = "vkpi_agent_orchestration_plan"
_TOOL_RUN_TABLE = "vkpi_agent_tool_run"
_DEDUPE_RE = re.compile(r"^plan:([1-9][0-9]*):step:(0|[1-9][0-9]*)$")
_RUNNABLE_PLAN_STATUSES = frozenset({"ready", "executing"})


class StepExecutionRejected(ValueError):
    """A stable fail-closed rejection with a machine-readable reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason)


def _reject(reason: str) -> None:
    raise StepExecutionRejected(reason)


def _loads_list(value: Any) -> list[dict[str, Any]]:
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            _reject("plan_json_invalid")
    if not isinstance(raw, list) or not raw:
        _reject("plan_steps_missing")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            _reject("plan_step_invalid")
        try:
            declared_index = int(item.get("step_index"))
        except (TypeError, ValueError):
            _reject("plan_step_index_invalid")
        if declared_index != index:
            _reject("plan_step_index_invalid")
        rows.append(dict(item))
    return rows


def _exact_int(value: Any, reason: str) -> int:
    if isinstance(value, bool):
        _reject(reason)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        _reject(reason)
    if str(value).strip() != str(parsed):
        _reject(reason)
    return parsed


def _fingerprint(contract: dict[str, Any]) -> str:
    sealed = {
        "plan_id": contract["plan_id"],
        "step_index": contract["step_index"],
        "tool_id": contract["tool_id"],
        "endpoint": contract["endpoint"],
        "inputs": contract["inputs"],
        "handler_category": contract["handler_category"],
        "entity_type": contract["entity_type"],
        "entity_id": contract["entity_id"],
        "cost_cents": contract["cost_cents"],
        "writes_business_data": contract["writes_business_data"],
        "affected_tables": contract["affected_tables"],
        "plan_owner_staff_id": contract["plan_owner_staff_id"],
        "total_steps": contract["total_steps"],
        "plan_tool_ids": contract["plan_tool_ids"],
    }
    raw = json.dumps(sealed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_registry_contract(step: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    tool_id = str(step.get("tool_id") or "").strip()
    tool = tool_registry.get_tool(tool_id)
    if not tool:
        _reject("unknown_tool")
    if not tool_registry.is_locally_executable(tool_id):
        _reject("tool_not_locally_executable")

    endpoint = str(tool.get("endpoint") or "")
    if str(step.get("endpoint") or "") != endpoint:
        _reject("plan_endpoint_mismatch")
    if bool(step.get("writes_db")) != bool(tool.get("writes_db")):
        _reject("plan_write_policy_mismatch")
    if bool(step.get("uses_llm")) != bool(tool.get("uses_llm")):
        _reject("plan_llm_policy_mismatch")
    if bool(step.get("requires_approval")) != bool(tool.get("requires_approval")):
        _reject("plan_approval_policy_mismatch")
    if list(step.get("affected_tables") or []) != list(tool.get("affected_tables") or []):
        _reject("plan_affected_tables_mismatch")
    try:
        planned_cost = int(step.get("estimated_cost_cents"))
    except (TypeError, ValueError):
        _reject("plan_cost_invalid")
    if planned_cost != int(tool.get("estimated_cost_cents") or 0) or planned_cost != 0:
        _reject("plan_cost_policy_mismatch")

    inputs = step.get("inputs")
    check = tool_registry.validate_inputs(tool_id, inputs if isinstance(inputs, dict) else None)
    if not check.get("ok"):
        _reject(str(check.get("reason") or "plan_inputs_invalid"))
    return tool_id, dict(tool), dict(inputs)


def contract_for_plan_step(
    plan_id: int,
    owner_staff_id: int,
    steps: list[dict[str, Any]],
    step_index: int,
) -> dict[str, Any]:
    """Build the exact registry-backed contract persisted into an Action pointer."""
    if step_index < 0 or step_index >= len(steps):
        _reject("step_not_found")
    step = steps[step_index]
    tool_id, tool, inputs = _validate_registry_contract(step)
    entity_type = str(tool.get("entity_type") or "")
    entity_id = str(inputs.get(str(tool.get("entity_id_input") or "")) or "").strip()
    if not entity_type or not entity_id:
        _reject("tool_entity_binding_invalid")
    contract: dict[str, Any] = {
        "plan_id": int(plan_id),
        "step_index": int(step_index),
        "tool_id": tool_id,
        "endpoint": str(tool["endpoint"]),
        "inputs": inputs,
        "handler_category": str(tool["handler_category"]),
        "entity_type": entity_type,
        "entity_id": entity_id,
        "cost_cents": int(tool.get("estimated_cost_cents") or 0),
        "writes_business_data": bool(tool.get("writes_db")),
        "affected_tables": list(tool.get("affected_tables") or []),
        "plan_owner_staff_id": int(owner_staff_id),
        "total_steps": len(steps),
        "plan_tool_ids": [str(item.get("tool_id") or "") for item in steps],
    }
    contract["fingerprint"] = _fingerprint(contract)
    return contract


def resolve_action_contract(
    action: dict[str, Any],
    staff: dict[str, Any] | None,
    *,
    conn: Any | None = None,
    lock_plan: bool = False,
) -> dict[str, Any]:
    """Resolve one Action against its authoritative plan step.

    ``lock_plan=True`` adds ``FOR UPDATE`` on Postgres and is called only after
    the Action CAS claim.  The caller keeps that transaction open through the
    local handler and receipt finalization.
    """
    if str(action.get("category") or "").strip().lower() != "orchestrated_step":
        _reject("not_orchestrated_step")
    if str(action.get("status") or "") != "approved":
        _reject("not_approved")
    if not bool(action.get("requires_approval")):
        _reject("approval_gate_missing")
    if bool(action.get("touches_v6_fit")):
        _reject("touches_v6_fit_violation")
    if not table_exists(_PLAN_TABLE) or not table_exists(_TOOL_RUN_TABLE):
        _reject("orchestration_ledger_unavailable")

    match = _DEDUPE_RE.fullmatch(str(action.get("dedupe_key") or ""))
    if match is None:
        _reject("dedupe_key_invalid")
    plan_id = int(match.group(1))
    step_index = int(match.group(2))

    actor = int(scope.actor_staff_id(staff))
    if actor <= 0:
        _reject("actor_required")
    db = conn or get_conn()
    lock_clause = " FOR UPDATE" if lock_plan and is_postgres_runtime() else ""
    row = db.execute(
        "SELECT id, plan_json, status, created_by_staff_id "
        f"FROM {_PLAN_TABLE} WHERE id = ?{lock_clause}",
        (plan_id,),
    ).fetchone()
    if row is None:
        _reject("plan_not_found")
    plan = dict(row)
    if str(plan.get("status") or "") not in _RUNNABLE_PLAN_STATUSES:
        _reject("plan_not_runnable")
    try:
        owner = int(plan.get("created_by_staff_id") or 0)
        action_owner = int(action.get("owner_staff_id") or 0)
    except (TypeError, ValueError):
        _reject("plan_owner_invalid")
    # v1 deliberately requires the creator, owner, approver/executor actor to
    # be the same concrete staff identity.  Cross-manager delegation needs an
    # explicit future delegation ledger, never an implicit can_view_all bypass.
    if owner <= 0 or action_owner != owner or actor != owner:
        _reject("plan_owner_mismatch")

    steps = _loads_list(plan.get("plan_json"))
    contract = contract_for_plan_step(plan_id, owner, steps, step_index)
    tool_id = str(contract["tool_id"])
    tool = tool_registry.get_tool(tool_id) or {}
    inputs = dict(contract["inputs"])
    if bool(tool.get("requires_manager")) and not scope.can_view_all(staff):
        _reject("manager_execution_required")

    payload = action.get("payload_json")
    if not isinstance(payload, dict):
        _reject("action_pointer_invalid")
    if _exact_int(payload.get("plan_id"), "action_pointer_invalid") != plan_id:
        _reject("action_pointer_mismatch")
    if _exact_int(payload.get("step_index"), "action_pointer_invalid") != step_index:
        _reject("action_pointer_mismatch")
    # The payload tool id is only a redundant integrity assertion.  It never
    # selects the handler and is compared to the locked plan value exactly.
    if str(payload.get("tool_id") or "") != tool_id:
        _reject("action_pointer_mismatch")
    if not hmac.compare_digest(
        str(payload.get("contract_sha256") or ""), str(contract["fingerprint"]),
    ):
        _reject("approved_plan_contract_mismatch")

    endpoint = str(tool["endpoint"])
    if str(action.get("suggested_endpoint") or "") != endpoint:
        _reject("action_endpoint_mismatch")
    if bool(action.get("writes_business_data")) != bool(tool.get("writes_db")):
        _reject("action_policy_mismatch")
    if bool(action.get("uses_llm")) != bool(tool.get("uses_llm")):
        _reject("action_policy_mismatch")
    try:
        action_cost = int(action.get("estimated_cost_cents"))
    except (TypeError, ValueError):
        _reject("action_cost_invalid")
    if action_cost != 0:
        _reject("action_cost_policy_mismatch")

    entity_type = str(contract["entity_type"])
    entity_id = str(contract["entity_id"])
    if str(action.get("entity_type") or "") != entity_type or str(action.get("entity_id") or "") != entity_id:
        _reject("action_entity_mismatch")
    affected_tables = list(contract["affected_tables"])
    if list(action.get("affected_tables_json") or []) != affected_tables:
        _reject("action_affected_tables_mismatch")

    return contract


def canonical_action(action: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    """Return handler/validator input authored only from the resolved contract."""
    return {
        **action,
        "category": "orchestrated_step",
        "entity_type": contract["entity_type"],
        "entity_id": contract["entity_id"],
        "suggested_endpoint": contract["endpoint"],
        "estimated_cost_cents": contract["cost_cents"],
        "writes_business_data": bool(contract["writes_business_data"]),
        "uses_llm": False,
        "requires_approval": True,
        "affected_tables_json": list(contract["affected_tables"]),
        "payload_json": dict(contract["inputs"]),
    }


def mark_plan_executing(conn: Any, contract: dict[str, Any]) -> None:
    cursor = conn.execute(
        f"UPDATE {_PLAN_TABLE} SET status='executing', updated_at="
        f"{'NOW()' if is_postgres_runtime() else 'CURRENT_TIMESTAMP'} "
        "WHERE id=? AND status IN ('ready','executing')",
        (int(contract["plan_id"]),),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        _reject("plan_state_changed")


def finalize_plan_status(conn: Any, contract: dict[str, Any], receipt_status: str) -> None:
    """Advance the plan in the same transaction as its canonical receipt."""
    plan_id = int(contract["plan_id"])
    status = str(receipt_status)
    if status == "failed":
        target = "failed"
    elif status == "skipped":
        target = "ready"
    elif status == "executed":
        rows = conn.execute(
            f"SELECT step_index, tool_id FROM {_TOOL_RUN_TABLE} "
            "WHERE plan_id=? AND status='executed'",
            (plan_id,),
        ).fetchall()
        completed = {
            (int(dict(row).get("step_index") or 0), str(dict(row).get("tool_id") or ""))
            for row in rows
        }
        expected = set(enumerate(contract["plan_tool_ids"]))
        target = "success" if expected and expected.issubset(completed) else "executing"
    else:
        _reject("receipt_status_invalid")
    conn.execute(
        f"UPDATE {_PLAN_TABLE} SET status=?, updated_at="
        f"{'NOW()' if is_postgres_runtime() else 'CURRENT_TIMESTAMP'} WHERE id=?",
        (target, plan_id),
    )


__all__ = [
    "StepExecutionRejected",
    "canonical_action",
    "contract_for_plan_step",
    "finalize_plan_status",
    "mark_plan_executing",
    "resolve_action_contract",
]
