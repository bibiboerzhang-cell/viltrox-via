"""Hermetic acceptance for the exact plan-linked observation state change."""
from __future__ import annotations

import json
import inspect
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from app.domains.actions import executors, orchestrated_steps, tool_runs, validators
from app.domains.agents import orchestrator, project_observation_step, step_execution, tool_registry
from app.domains.projects import observation_window_open

MANAGER = {
    "id": 7, "role": "manager", "organization_id": 1,
    "organization_scope_status": "resolved",
}
EMPLOYEE = {
    "id": 7, "role": "employee", "organization_id": 1,
    "organization_scope_status": "resolved",
}
ORG2_MANAGER = {
    "id": 7, "role": "manager", "organization_id": 2,
    "organization_scope_status": "resolved",
}
TOOL_ID = "check_project_observation"


def _step() -> dict[str, Any]:
    tool = tool_registry.get_tool(TOOL_ID) or {}
    return {
        "step_index": 0,
        "tool_id": TOOL_ID,
        "name": tool.get("name"),
        "writes_db": True,
        "uses_llm": False,
        "cost_tier": "none",
        "estimated_cost_cents": 0,
        "requires_approval": True,
        "endpoint": tool.get("endpoint"),
        "execution_policy": tool.get("execution_policy"),
        "inputs": {"project_id": 71, "assignment_id": 81},
        "affected_tables": ["vkpi_project_content_observation_windows"],
    }


def _action(*, action_id: int = 41, plan_id: int = 11) -> dict[str, Any]:
    contract_sha256 = step_execution.contract_for_plan_step(plan_id, 7, [_step()], 0)[
        "fingerprint"
    ]
    return {
        "id": action_id,
        "dedupe_key": f"plan:{plan_id}:step:0",
        "category": "orchestrated_step",
        "title": "open exact observation window",
        "detail": "",
        "priority": "medium",
        "entity_type": "project",
        "entity_id": "71",
        "suggested_endpoint": "local-action:project_observation",
        "estimated_cost_cents": 0,
        "writes_business_data": True,
        "uses_llm": False,
        "requires_approval": True,
        "owner_staff_id": 7,
        "payload_json": {
            "plan_id": plan_id, "step_index": 0, "tool_id": TOOL_ID,
            "contract_sha256": contract_sha256,
        },
        "approval_reason": "Manager checked exact project and assignment",
        "verification_plan_json": ["server delta required"],
        "affected_tables_json": ["vkpi_project_content_observation_windows"],
        "status": "approved",
        "touches_v6_fit": False,
    }


def _db(*, action_id: int = 41, plan_id: int = 11) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_agent_orchestration_plan (
          id INTEGER PRIMARY KEY, plan_json TEXT NOT NULL, status TEXT NOT NULL,
          created_by_staff_id INTEGER, updated_at TEXT
        );
        CREATE TABLE vkpi_agent_tool_run (
          id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER, tool_id TEXT,
          step_index INTEGER, inputs_json TEXT, output_ref TEXT, cost_cents INTEGER,
          status TEXT, error TEXT, executed_at TEXT
        );
        CREATE TABLE vkpi_action_inbox (
          id INTEGER PRIMARY KEY, status TEXT NOT NULL, result_checklist_json TEXT,
          updated_at TEXT
        );
        CREATE TABLE vkpi_action_execution_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT, action_id INTEGER, category TEXT,
          dedupe_key TEXT, actor_staff_id INTEGER, mode TEXT, outcome TEXT,
          endpoint TEXT, cost_cents INTEGER, error TEXT, detail_json TEXT, created_at TEXT
        );
        CREATE TABLE vkpi_projects (id INTEGER PRIMARY KEY);
        CREATE TABLE vkpi_project_kol_assignments (
          id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER
        );
        CREATE TABLE vkpi_shipments (
          id INTEGER PRIMARY KEY, project_id INTEGER, assignment_id INTEGER, delivered_at TEXT
        );
        CREATE TABLE vkpi_project_content_observation_windows (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, assignment_id INTEGER,
          kol_pool_id INTEGER, starts_at TEXT, ends_at TEXT, status TEXT,
          scan_count INTEGER DEFAULT 0, last_scan_at TEXT, matched_content_post_id INTEGER,
          metadata_json TEXT, source_shipment_id INTEGER UNIQUE,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO vkpi_projects(id) VALUES (71);
        INSERT INTO vkpi_project_kol_assignments(id,project_id,kol_pool_id) VALUES (81,71,91);
        INSERT INTO vkpi_shipments(id,project_id,assignment_id,delivered_at)
          VALUES (61,71,81,'2026-01-01T00:00:00+00:00');
        """
    )
    conn.execute("INSERT INTO vkpi_action_inbox(id,status) VALUES (?,'approved')", (action_id,))
    conn.execute(
        "INSERT INTO vkpi_agent_orchestration_plan(id,plan_json,status,created_by_staff_id) "
        "VALUES (?,?,'ready',7)",
        (plan_id, json.dumps([_step()])),
    )
    conn.commit()
    return conn


def _patch(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection, action: dict[str, Any]) -> None:
    for module in (executors, orchestrated_steps, validators, step_execution):
        monkeypatch.setattr(module, "get_conn", lambda: conn)
    monkeypatch.setattr(observation_window_open, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(project_observation_step, "is_postgres_runtime", lambda: False)
    for module in (executors, orchestrated_steps, step_execution, tool_runs):
        monkeypatch.setattr(module, "is_postgres_runtime", lambda: False)
    for module in (executors, orchestrated_steps, step_execution, tool_runs):
        monkeypatch.setattr(module, "table_exists", lambda _name: True)
    monkeypatch.setattr(executors.inbox, "get_action", lambda *_args, **_kwargs: dict(action))

    def claim(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        cursor = conn.execute(
            "UPDATE vkpi_action_inbox SET status='executing' WHERE id=? AND status='approved'",
            (int(action["id"]),),
        )
        conn.commit()
        return {
            "ok": int(cursor.rowcount or 0) == 1,
            "reason": "execution_already_claimed",
            "status": "executing",
        }

    monkeypatch.setattr(executors.inbox, "claim_action_execution", claim)


def _receipt(conn: sqlite3.Connection) -> dict[str, Any]:
    row = dict(conn.execute("SELECT * FROM vkpi_agent_tool_run ORDER BY id DESC").fetchone())
    row["inputs_json"] = json.loads(row["inputs_json"])
    return row


def test_plan_binds_exact_zero_provider_state_change() -> None:
    planned = orchestrator._plan_steps(
        "为签收派单补观察窗",
        {"project_id": 71, "assignment_id": 81, "handler": "evil", "api_key": "secret"},
    )
    assert planned == [_step()]
    tool = tool_registry.get_tool(TOOL_ID) or {}
    assert tool["uses_llm"] is False
    assert tool["estimated_cost_cents"] == 0
    assert tool["endpoint"] == "local-action:project_observation"


def test_postgres_lock_order_is_project_then_assignment_then_shipment() -> None:
    source = inspect.getsource(project_observation_step.execute)
    project_lock = source.index("SELECT id FROM vkpi_projects WHERE id=?")
    assignment_lock = source.index("SELECT id, kol_pool_id FROM vkpi_project_kol_assignments")
    shipment_lock = source.index("SELECT id, delivered_at FROM vkpi_shipments")
    assert project_lock < assignment_lock < shipment_lock
    assert source.count("{lock}") >= 3


def test_created_window_is_atomic_state_changed_with_server_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    action = _action()
    _patch(monkeypatch, conn, action)

    result = executors.execute_action(41, MANAGER)

    assert result["ok"] is True
    assert result["detail"]["state_delta"]["rows_created"] == 1
    assert result["detail"]["result_checklist"]["before_after"] == [{
        "table": "vkpi_project_content_observation_windows",
        "before": 0,
        "after": 1,
        "delta": 1,
    }]
    receipt = _receipt(conn)
    assert receipt["inputs_json"]["execution_effect"] == "state_changed"
    assert receipt["inputs_json"]["step_inputs"] == {"project_id": 71, "assignment_id": 81}
    assert receipt["inputs_json"]["affected_tables"] == [
        "vkpi_project_content_observation_windows"
    ]
    assert conn.execute("SELECT status FROM vkpi_action_inbox WHERE id=41").fetchone()[0] == "executed"
    assert conn.execute("SELECT status FROM vkpi_agent_orchestration_plan WHERE id=11").fetchone()[0] == "success"


def test_existing_window_is_exact_noop_and_cannot_count_as_state_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO vkpi_project_content_observation_windows "
        "(project_id,assignment_id,kol_pool_id,starts_at,ends_at,status,metadata_json) "
        "VALUES (71,81,91,'2026-01-08','2026-02-15','pending','{}')"
    )
    conn.commit()
    _patch(monkeypatch, conn, _action())

    result = executors.execute_action(41, MANAGER)

    assert result["ok"] is True
    assert result["detail"]["idempotent"] is True
    assert result["detail"]["state_delta"]["rows_created"] == 0
    assert result["detail"]["result_checklist"]["wrote_business_data"] is False
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 1
    assert _receipt(conn)["inputs_json"]["execution_effect"] == "idempotent_noop"


def test_terminal_window_replay_is_permanent_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn, _action())
    assert executors.execute_action(41, MANAGER)["ok"] is True
    conn.execute("UPDATE vkpi_project_content_observation_windows SET status='closed'")
    conn.execute("INSERT INTO vkpi_action_inbox(id,status) VALUES (42,'approved')")
    conn.execute(
        "INSERT INTO vkpi_agent_orchestration_plan(id,plan_json,status,created_by_staff_id) "
        "VALUES (12,?,'ready',7)",
        (json.dumps([_step()]),),
    )
    conn.commit()
    _patch(monkeypatch, conn, _action(action_id=42, plan_id=12))

    replay = executors.execute_action(42, MANAGER)

    assert replay["ok"] is True
    assert replay["detail"]["idempotent"] is True
    assert replay["detail"]["state_delta"]["rows_created"] == 0
    assert replay["detail"]["state_delta"]["before"] == "closed"
    assert replay["detail"]["state_delta"]["after"] == "closed"
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT source_shipment_id FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 61
    effects = [
        json.loads(row[0])["execution_effect"]
        for row in conn.execute("SELECT inputs_json FROM vkpi_agent_tool_run ORDER BY id")
    ]
    assert effects == ["state_changed", "idempotent_noop"]


def test_legacy_terminal_exact_window_without_source_is_permanent_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    conn.execute(
        "INSERT INTO vkpi_project_content_observation_windows "
        "(project_id,assignment_id,kol_pool_id,starts_at,ends_at,status,metadata_json) "
        "VALUES (71,81,91,'2026-01-08 00:00:00+00:00',"
        "'2026-02-15 00:00:00+00:00','expired','{}')"
    )
    conn.commit()
    _patch(monkeypatch, conn, _action())

    replay = executors.execute_action(41, MANAGER)

    assert replay["ok"] is True and replay["detail"]["idempotent"] is True
    assert replay["detail"]["state_delta"] == {
        "target": "project:71:assignment:81",
        "before": "expired",
        "after": "expired",
        "rows_created": 0,
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 1
    assert _receipt(conn)["inputs_json"]["execution_effect"] == "idempotent_noop"


def test_concurrent_plans_create_one_window_and_one_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    source = _db()
    source.execute("INSERT INTO vkpi_action_inbox(id,status) VALUES (42,'approved')")
    source.execute(
        "INSERT INTO vkpi_agent_orchestration_plan(id,plan_json,status,created_by_staff_id) "
        "VALUES (12,?,'ready',7)",
        (json.dumps([_step()]),),
    )
    source.commit()
    db_path = tmp_path / "state-change.sqlite"
    target = sqlite3.connect(db_path)
    source.backup(target)
    target.close()
    source.close()

    local = threading.local()
    opened: list[sqlite3.Connection] = []
    opened_lock = threading.Lock()
    actions = {41: _action(), 42: _action(action_id=42, plan_id=12)}

    def get_thread_conn() -> sqlite3.Connection:
        conn = getattr(local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            local.conn = conn
            with opened_lock:
                opened.append(conn)
        return conn

    for module in (executors, orchestrated_steps, validators, step_execution):
        monkeypatch.setattr(module, "get_conn", get_thread_conn)
    for module in (executors, orchestrated_steps, step_execution, tool_runs):
        monkeypatch.setattr(module, "is_postgres_runtime", lambda: False)
        monkeypatch.setattr(module, "table_exists", lambda _name: True)
    monkeypatch.setattr(observation_window_open, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(project_observation_step, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(
        executors.inbox, "get_action", lambda action_id, *_args, **_kwargs: dict(actions[action_id]),
    )

    def claim(action_id: int, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        conn = get_thread_conn()
        row = conn.execute(
            "UPDATE vkpi_action_inbox SET status='executing' "
            "WHERE id=? AND status='approved' RETURNING id",
            (action_id,),
        ).fetchone()
        conn.commit()
        return {
            "ok": row is not None,
            "reason": "execution_already_claimed",
            "status": "executing",
        }

    monkeypatch.setattr(executors.inbox, "claim_action_execution", claim)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda action_id: executors.execute_action(action_id, MANAGER), (41, 42),
            ))
    finally:
        for conn in opened:
            conn.close()

    assert all(result["ok"] for result in results)
    check = sqlite3.connect(db_path)
    assert check.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 1
    effects = sorted(
        json.loads(row[0])["execution_effect"]
        for row in check.execute("SELECT inputs_json FROM vkpi_agent_tool_run")
    )
    assert effects == ["idempotent_noop", "state_changed"]
    check.close()


def test_forged_target_and_non_manager_are_rejected_before_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    action = _action()
    _patch(monkeypatch, conn, action)
    forged = dict(action)
    forged["affected_tables_json"] = ["vkpi_projects"]
    with pytest.raises(step_execution.StepExecutionRejected, match="action_affected_tables_mismatch"):
        step_execution.resolve_action_contract(forged, MANAGER)
    with pytest.raises(step_execution.StepExecutionRejected, match="manager_execution_required"):
        step_execution.resolve_action_contract(action, EMPLOYEE)
    denied = project_observation_step.execute(conn, action, ORG2_MANAGER)
    assert denied["reason"] == "manager_execution_required"
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 0


def test_approved_action_rejects_plan_target_tamper_before_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    action = _action()
    _patch(monkeypatch, conn, action)
    changed = _step()
    changed["inputs"] = {"project_id": 71, "assignment_id": 82}
    conn.execute("INSERT INTO vkpi_project_kol_assignments VALUES (82,71,92)")
    conn.execute(
        "INSERT INTO vkpi_shipments VALUES (62,71,82,'2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "UPDATE vkpi_agent_orchestration_plan SET plan_json=? WHERE id=11",
        (json.dumps([changed]),),
    )
    conn.commit()
    handler_calls = 0

    def bomb(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal handler_calls
        handler_calls += 1
        raise AssertionError("tampered approved plan must not start handler")

    monkeypatch.setitem(executors._DISPATCH, "project_observation", bomb)
    result = executors.execute_action(41, MANAGER)

    assert result["outcome"] == "skipped"
    assert result["reason"] == "approved_plan_contract_mismatch"
    assert handler_calls == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 0


def test_finalize_failure_rolls_back_window_ledger_receipt_and_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn, _action())

    def fail_receipt(*_args: Any, **_kwargs: Any) -> int:
        raise RuntimeError("receipt unavailable")

    monkeypatch.setattr(tool_runs, "insert_action_tool_run", fail_receipt)
    result = executors.execute_action(41, MANAGER)

    assert result["ok"] is False
    assert result["reason"] == "execution_finalize_failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_action_execution_ledger").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vkpi_agent_tool_run").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM vkpi_agent_orchestration_plan WHERE id=11").fetchone()[0] == "ready"
    assert conn.execute("SELECT status FROM vkpi_action_inbox WHERE id=41").fetchone()[0] == "executing"


def test_handler_failure_discards_partial_window_before_failed_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _db()
    _patch(monkeypatch, conn, _action())

    def partial_then_fail(action: dict[str, Any], _staff: dict[str, Any]) -> dict[str, Any]:
        action["_transaction_conn"].execute(
            "INSERT INTO vkpi_project_content_observation_windows "
            "(project_id,assignment_id,kol_pool_id,starts_at,ends_at,status,metadata_json) "
            "VALUES (71,81,91,'2026-01-08','2026-02-15','pending','{}')"
        )
        raise RuntimeError("forced after partial write")

    monkeypatch.setitem(executors._DISPATCH, "project_observation", partial_then_fail)
    result = executors.execute_action(41, MANAGER)

    assert result["ok"] is False and result["outcome"] == "failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM vkpi_project_content_observation_windows"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT outcome FROM vkpi_action_execution_ledger").fetchone()[0] == "failed"
    assert conn.execute("SELECT status FROM vkpi_agent_tool_run").fetchone()[0] == "failed"
    assert conn.execute("SELECT status FROM vkpi_agent_orchestration_plan").fetchone()[0] == "failed"
    assert conn.execute("SELECT status FROM vkpi_action_inbox").fetchone()[0] == "failed"
