"""Durable Agent tool-run receipts created by approved Action execution.

Tool identity is derived from the server-side Action category.  Client payload
may only link a plan that already exists; it cannot invent a tool id or create
an orphan foreign key.  The caller owns the transaction so the tool receipt,
execution ledger, and Action terminal state commit or roll back together.
"""
from __future__ import annotations

import json
from typing import Any

from app.db.connection import is_postgres_runtime, table_exists

_TOOL_RUN_LEDGER = "vkpi_agent_tool_run"

_ACKNOWLEDGEMENT_CATEGORIES = {"event_followup", "inventory_low"}
_QUEUED_CATEGORIES = {
    "deep_missing", "discovery_enroll", "failed_retry", "retrospective", "kol_profile",
}
_STATE_CHANGED_CATEGORIES = {"project_observation", "content_candidate"}


def execution_effect_for_action(
    category: str,
    outcome: str,
    detail: dict[str, Any] | None = None,
) -> str:
    if str(outcome) != "success":
        return "none"
    normalized = str(category or "").strip().lower()
    receipt = detail if isinstance(detail, dict) else {}
    if normalized in _ACKNOWLEDGEMENT_CATEGORIES:
        return "acknowledgement"
    if normalized in _QUEUED_CATEGORIES:
        return "queued"
    if normalized == "project_observation":
        return "state_changed" if bool(receipt.get("created_windows")) else "idempotent_noop"
    if normalized == "content_candidate":
        review = receipt.get("review") if isinstance(receipt.get("review"), dict) else {}
        return "state_changed" if review.get("state_changed") is True else "idempotent_noop"
    if normalized in _STATE_CHANGED_CATEGORIES:
        return "state_changed"
    return "none"


def _json_param() -> str:
    return "?::jsonb" if is_postgres_runtime() else "?"


def _sql_now() -> str:
    return "NOW()" if is_postgres_runtime() else "CURRENT_TIMESTAMP"


def insert_action_tool_run(
    conn: Any,
    *,
    action: dict[str, Any],
    action_id: int,
    outcome: str,
    ledger_id: int,
    error: str,
    detail: dict[str, Any] | None = None,
    orchestration_contract: dict[str, Any] | None = None,
) -> int:
    """Insert one canonical tool receipt without committing the transaction."""
    if not table_exists(_TOOL_RUN_LEDGER):
        raise RuntimeError("agent tool run ledger is unavailable")

    category = str(action.get("category") or "unknown").strip().lower()[:140]
    contract = orchestration_contract if isinstance(orchestration_contract, dict) else None
    if category == "orchestrated_step" and contract is None:
        raise RuntimeError("orchestrated tool receipt requires locked server contract")
    if contract is not None and category != "orchestrated_step":
        raise RuntimeError("ordinary Action cannot receive orchestration identity")

    if contract is None:
        plan_id = None
        step_index = 0
        tool_id = f"action:{category}"
        canonical_inputs: dict[str, Any] = {}
        contract_cost = 0
    else:
        plan_id = int(contract["plan_id"])
        step_index = int(contract["step_index"])
        tool_id = str(contract["tool_id"])
        canonical_inputs = dict(contract.get("inputs") or {})
        contract_cost = int(contract.get("cost_cents") or 0)
        if plan_id <= 0 or step_index < 0 or not tool_id:
            raise RuntimeError("invalid locked server contract")

    status = {"success": "executed", "failed": "failed", "skipped": "skipped"}.get(
        str(outcome), "failed"
    )
    inputs = {
        "action_id": int(action_id),
        "category": str(action.get("category") or ""),
        "entity_type": str(action.get("entity_type") or ""),
        "entity_id": str(action.get("entity_id") or ""),
        "plan_id": plan_id,
        "step_index": step_index,
        "execution_ledger_id": int(ledger_id),
        "execution_effect": execution_effect_for_action(
            str(contract.get("handler_category") or category) if contract else category,
            outcome,
            detail,
        ),
    }
    if contract is not None:
        inputs["step_inputs"] = canonical_inputs
        inputs["contract_sha256"] = str(contract.get("fingerprint") or "")
        inputs["affected_tables"] = list(contract.get("affected_tables") or [])
    cost_cents = contract_cost if status == "executed" else 0
    if contract is None and status == "executed" and bool(action.get("uses_llm")):
        try:
            cost_cents = max(0, int(action.get("estimated_cost_cents") or 0))
        except (TypeError, ValueError):
            cost_cents = 0

    row = conn.execute(
        f"""
        INSERT INTO {_TOOL_RUN_LEDGER}
          (plan_id, tool_id, step_index, inputs_json, output_ref, cost_cents,
           status, error, executed_at)
        VALUES (?,?,?,{_json_param()},?,?,?, ?, {_sql_now()})
        RETURNING id
        """,
        (
            plan_id,
            tool_id,
            step_index,
            json.dumps(inputs, ensure_ascii=False, default=str),
            f"action:{int(action_id)}",
            cost_cents,
            status,
            str(error or "")[:2000],
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("agent tool run insert returned no id")
    return int(dict(row)["id"])


__all__ = ["execution_effect_for_action", "insert_action_tool_run"]
