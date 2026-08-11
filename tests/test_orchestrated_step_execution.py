"""Hermetic acceptance tests for the minimal plan-linked execution loop."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from app.domains.actions import executors, orchestrated_steps, tool_runs, validators
from app.domains.agents import orchestrator, step_execution, tool_registry

STAFF = {"id": 7, "role": "manager"}


def _step(tool_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    tool = tool_registry.get_tool(tool_id) or {}
    return {
        "step_index": 0,
        "tool_id": tool_id,
        "name": tool.get("name"),
        "writes_db": bool(tool.get("writes_db")),
        "uses_llm": bool(tool.get("uses_llm")),
        "cost_tier": tool.get("cost_tier"),
        "estimated_cost_cents": int(tool.get("estimated_cost_cents") or 0),
        "requires_approval": bool(tool.get("requires_approval")),
        "endpoint": tool.get("endpoint"),
        "execution_policy": tool.get("execution_policy", "plan_only"),
        "inputs": inputs,
        "affected_tables": list(tool.get("affected_tables") or []),
    }


def _action(
    *, tool_id: str = "ack_event_followup", inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entity_type = "event" if tool_id == "ack_event_followup" else "inventory"
    entity_id = "evt-7" if entity_type == "event" else "inv-7"
    endpoint = str((tool_registry.get_tool(tool_id) or {}).get("endpoint") or "")
    bound_inputs = inputs or ({"event_id": "evt-7"} if entity_type == "event" else {"inventory_id": "inv-7"})
    contract_sha256 = ""
    if tool_registry.is_locally_executable(tool_id):
        contract_sha256 = str(
            step_execution.contract_for_plan_step(11, 7, [_step(tool_id, bound_inputs)], 0)[
                "fingerprint"
            ]
        )
    return {
        "id": 41,
        "dedupe_key": "plan:11:step:0",
        "category": "orchestrated_step",
        "title": "controlled acknowledgement",
        "detail": "",
        "priority": "medium",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "suggested_endpoint": endpoint,
        "estimated_cost_cents": 0,
        "writes_business_data": False,
        "uses_llm": False,
        "requires_approval": True,
        "owner_staff_id": 7,
        "payload_json": {
            "plan_id": 11, "step_index": 0, "tool_id": tool_id,
            "contract_sha256": contract_sha256,
        },
        "approval_reason": "Reviewed target and local-only execution policy",
        "affected_tables_json": [],
        "status": "approved",
        "touches_v6_fit": False,
    }


def _db(step: dict[str, Any]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_agent_orchestration_plan (
          id INTEGER PRIMARY KEY,
          goal TEXT NOT NULL DEFAULT '',
          plan_json TEXT NOT NULL,
          status TEXT NOT NULL,
          estimated_cost_cents INTEGER NOT NULL DEFAULT 0,
          created_by_staff_id INTEGER,
          created_at TEXT,
          updated_at TEXT
        );
        CREATE TABLE vkpi_agent_tool_run (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          plan_id INTEGER,
          tool_id TEXT NOT NULL,
          step_index INTEGER NOT NULL,
          inputs_json TEXT NOT NULL,
          output_ref TEXT NOT NULL,
          cost_cents INTEGER NOT NULL,
          status TEXT NOT NULL,
          error TEXT NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          executed_at TEXT
        );
        CREATE TABLE vkpi_action_inbox (
          id INTEGER PRIMARY KEY,
          status TEXT NOT NULL,
          result_checklist_json TEXT,
          updated_at TEXT
        );
        CREATE TABLE vkpi_action_execution_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          action_id INTEGER,
          category TEXT,
          dedupe_key TEXT,
          actor_staff_id INTEGER,
          mode TEXT,
          outcome TEXT,
          endpoint TEXT,
          cost_cents INTEGER,
          error TEXT,
          detail_json TEXT,
          created_at TEXT
        );
        CREATE TABLE vkpi_events (id TEXT PRIMARY KEY);
        CREATE TABLE vkpi_inventory (id TEXT PRIMARY KEY);
        INSERT INTO vkpi_action_inbox(id,status) VALUES (41,'approved');
        INSERT INTO vkpi_events(id) VALUES ('evt-7');
        INSERT INTO vkpi_inventory(id) VALUES ('inv-7');
        """
    )
    conn.execute(
        "INSERT INTO vkpi_agent_orchestration_plan "
        "(id,goal,plan_json,status,created_by_staff_id,created_at,updated_at) "
        "VALUES (11,'safe acknowledgement',?,'ready',7,'now','now')",
        (json.dumps([step]),),
    )
    conn.commit()
    return conn


def _patch_db(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    for module in (executors, orchestrated_steps, validators, step_execution):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    monkeypatch.setattr(executors, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(orchestrated_steps, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(step_execution, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(tool_runs, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(executors, "table_exists", lambda _name: True)
    monkeypatch.setattr(orchestrated_steps, "table_exists", lambda _name: True)
    monkeypatch.setattr(step_execution, "table_exists", lambda _name: True)
    monkeypatch.setattr(tool_runs, "table_exists", lambda _name: True)


def test_safe_plan_is_server_bound_and_zero_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "table_exists", lambda _name: False)
    planned = orchestrator.plan_goal(
        "请跟进活动收尾",
        context={"event_id": "evt-7", "tool_id": "evil.shell", "estimated_cost_cents": 999},
        staff=STAFF,
    )
    assert planned["estimated_cost_cents"] == 0
    assert planned["steps"] == [_step("ack_event_followup", {"event_id": "evt-7"})]


def test_materialization_uses_plan_contract_and_marks_plan_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step = _step("ack_event_followup", {"event_id": "evt-7", "missing": ["roi"]})
    conn = _db(step)
    conn.execute("UPDATE vkpi_agent_orchestration_plan SET status='planned' WHERE id=11")
    conn.commit()
    plan = {
        "id": 11,
        "goal": "请跟进活动收尾",
        "plan_json": [step],
        "status": "planned",
        "created_by_staff_id": 7,
    }
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(orchestrator, "get_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(orchestrator, "get_conn", lambda: conn)
    monkeypatch.setattr(orchestrator, "is_postgres_runtime", lambda: False)

    from app.domains.actions import inbox

    def persist(rows: list[dict[str, Any]]) -> int:
        captured.extend(rows)
        return len(rows)

    monkeypatch.setattr(inbox, "persist_suggestions", persist)
    result = orchestrator.materialize_plan_to_inbox(11, staff=STAFF)
    assert result["status"] == "ok"
    assert len(captured) == 1
    suggestion = captured[0]
    assert suggestion["dedupe_key"] == "plan:11:step:0"
    assert suggestion["payload"] == {
        "plan_id": 11,
        "step_index": 0,
        "tool_id": "ack_event_followup",
        "contract_sha256": step_execution.contract_for_plan_step(11, 7, [step], 0)[
            "fingerprint"
        ],
    }
    assert suggestion["entity_type"] == "event"
    assert suggestion["entity_id"] == "evt-7"
    assert suggestion["estimated_cost_cents"] == 0
    assert suggestion["requires_approval"] is True
    assert conn.execute("SELECT status FROM vkpi_agent_orchestration_plan WHERE id=11").fetchone()[0] == "ready"


def test_resolver_rejects_payload_selected_tool_and_unsafe_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _db(_step("ack_event_followup", {"event_id": "evt-7"}))
    _patch_db(monkeypatch, conn)
    forged = _action()
    forged["payload_json"] = {"plan_id": 11, "step_index": 0, "tool_id": "ack_inventory_low"}
    with pytest.raises(step_execution.StepExecutionRejected, match="action_pointer_mismatch"):
        step_execution.resolve_action_contract(forged, STAFF)

    conn.execute(
        "UPDATE vkpi_agent_orchestration_plan SET plan_json=? WHERE id=11",
        (json.dumps([{
            "step_index": 0,
            "tool_id": "search_kol",
            "writes_db": False,
            "uses_llm": True,
            "requires_approval": False,
            "endpoint": "POST /api/admin/vkpi/kol-smart-search",
            "estimated_cost_cents": 10,
            "inputs": {"query": "lens"},
        }]),),
    )
    conn.commit()
    unsafe = _action(tool_id="search_kol")
    unsafe.update({"uses_llm": True, "estimated_cost_cents": 10})
    with pytest.raises(step_execution.StepExecutionRejected, match="tool_not_locally_executable"):
        step_execution.resolve_action_contract(unsafe, STAFF)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda a: a.update(estimated_cost_cents=1), "action_cost_policy_mismatch"),
        (lambda a: a.update(requires_approval=False), "approval_gate_missing"),
        (lambda a: a.update(owner_staff_id=8), "plan_owner_mismatch"),
        (lambda a: a.update(dedupe_key="plan:11:step:00"), "dedupe_key_invalid"),
    ],
)
def test_resolver_fails_closed_on_action_or_approval_tamper(
    monkeypatch: pytest.MonkeyPatch,
    mutate: Any,
    reason: str,
) -> None:
    conn = _db(_step("ack_event_followup", {"event_id": "evt-7"}))
    _patch_db(monkeypatch, conn)
    action = _action()
    mutate(action)
    with pytest.raises(step_execution.StepExecutionRejected, match=reason):
        step_execution.resolve_action_contract(action, STAFF)


def test_execute_writes_exact_plan_receipt_and_only_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db(_step("ack_event_followup", {"event_id": "evt-7", "missing": ["roi"]}))
    _patch_db(monkeypatch, conn)
    action = _action(inputs={"event_id": "evt-7", "missing": ["roi"]})
    # This extra client payload can never reach the handler; identity fields
    # are only redundant assertions and server plan inputs are canonical.
    action["payload_json"]["secret"] = "do-not-forward"
    monkeypatch.setattr(executors.inbox, "get_action", lambda *_args, **_kwargs: dict(action))

    def claim(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        cur = conn.execute(
            "UPDATE vkpi_action_inbox SET status='executing' WHERE id=41 AND status='approved'"
        )
        conn.commit()
        return {"ok": int(cur.rowcount or 0) == 1, "status": "executing"}

    monkeypatch.setattr(executors.inbox, "claim_action_execution", claim)
    result = executors.execute_action(41, STAFF)

    assert result["ok"] is True
    assert result["detail"]["acknowledged"] is True
    receipt = dict(conn.execute("SELECT * FROM vkpi_agent_tool_run").fetchone())
    inputs = json.loads(receipt["inputs_json"])
    assert receipt["plan_id"] == 11
    assert receipt["step_index"] == 0
    assert receipt["tool_id"] == "ack_event_followup"
    assert receipt["cost_cents"] == 0
    assert inputs["step_inputs"] == {"event_id": "evt-7", "missing": ["roi"]}
    assert "secret" not in json.dumps(inputs)
    assert inputs["execution_effect"] == "acknowledgement"
    assert conn.execute("SELECT status FROM vkpi_action_inbox WHERE id=41").fetchone()[0] == "executed"
    assert conn.execute("SELECT status FROM vkpi_agent_orchestration_plan WHERE id=11").fetchone()[0] == "success"


def test_claim_failure_never_starts_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _db(_step("ack_event_followup", {"event_id": "evt-7"}))
    _patch_db(monkeypatch, conn)
    monkeypatch.setattr(executors.inbox, "get_action", lambda *_args, **_kwargs: _action())
    monkeypatch.setattr(
        executors.inbox,
        "claim_action_execution",
        lambda *_args, **_kwargs: {"ok": False, "reason": "execution_already_claimed", "status": "executing"},
    )
    called = 0

    def bomb(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called += 1
        raise AssertionError("handler must not start")

    monkeypatch.setitem(executors._DISPATCH, "event_followup", bomb)
    result = executors.execute_action(41, STAFF)
    assert result["reason"] == "execution_already_claimed"
    assert called == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_agent_tool_run").fetchone()[0] == 0


def test_locked_plan_change_releases_claim_without_starting_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db(_step("ack_event_followup", {"event_id": "evt-7"}))
    _patch_db(monkeypatch, conn)
    action = _action()
    monkeypatch.setattr(executors.inbox, "get_action", lambda *_args, **_kwargs: dict(action))

    def claim(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        conn.execute("UPDATE vkpi_action_inbox SET status='executing' WHERE id=41")
        conn.commit()
        return {"ok": True, "status": "executing"}

    monkeypatch.setattr(executors.inbox, "claim_action_execution", claim)
    original_resolve = step_execution.resolve_action_contract
    calls = 0

    def changed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        contract = original_resolve(*args, **kwargs)
        if kwargs.get("lock_plan"):
            contract["fingerprint"] = "0" * 64
        return contract

    monkeypatch.setattr(step_execution, "resolve_action_contract", changed)
    handler_calls = 0

    def bomb(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal handler_calls
        handler_calls += 1
        raise AssertionError("handler must not start")

    monkeypatch.setitem(executors._DISPATCH, "event_followup", bomb)
    result = executors.execute_action(41, STAFF)
    assert calls == 2
    assert handler_calls == 0
    assert result["outcome"] == "skipped"
    assert result["reason"] == "plan_contract_changed"
    assert conn.execute("SELECT status FROM vkpi_action_inbox WHERE id=41").fetchone()[0] == "approved"
    assert conn.execute("SELECT COUNT(*) FROM vkpi_agent_tool_run").fetchone()[0] == 0
