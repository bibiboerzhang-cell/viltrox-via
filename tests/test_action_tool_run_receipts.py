"""Hermetic tests for server-authored Agent tool execution receipts."""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.actions import tool_runs


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_agent_orchestration_plan (id INTEGER PRIMARY KEY);
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
            executed_at TEXT
        );
        INSERT INTO vkpi_agent_orchestration_plan(id) VALUES (11);
        """
    )
    return conn


def _action(payload: dict) -> dict:
    return {
        "category": "Event_FollowUp",
        "entity_type": "event",
        "entity_id": "evt-7",
        "uses_llm": True,
        "estimated_cost_cents": 9,
        "payload_json": payload,
    }


def test_ordinary_action_tool_receipt_rejects_payload_plan_identity(monkeypatch):
    conn = _db()
    monkeypatch.setattr(tool_runs, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(tool_runs, "table_exists", lambda name: True)

    receipt_id = tool_runs.insert_action_tool_run(
        conn,
        action=_action({"plan_id": 11, "step_index": 3, "tool_id": "evil.shell", "secret": "x"}),
        action_id=7,
        outcome="success",
        ledger_id=101,
        error="",
    )

    row = dict(conn.execute("SELECT * FROM vkpi_agent_tool_run WHERE id=?", (receipt_id,)).fetchone())
    inputs = json.loads(row["inputs_json"])
    assert row["tool_id"] == "action:event_followup"
    assert row["plan_id"] is None
    assert row["step_index"] == 0
    assert row["status"] == "executed"
    assert row["cost_cents"] == 9
    assert inputs == {
        "action_id": 7,
        "category": "Event_FollowUp",
        "entity_type": "event",
        "entity_id": "evt-7",
        "plan_id": None,
        "step_index": 0,
        "execution_ledger_id": 101,
        "execution_effect": "acknowledgement",
    }


def test_tool_receipt_drops_forged_plan_and_step(monkeypatch):
    conn = _db()
    monkeypatch.setattr(tool_runs, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(tool_runs, "table_exists", lambda name: True)

    receipt_id = tool_runs.insert_action_tool_run(
        conn,
        action=_action({"plan_id": 999, "step_index": 88}),
        action_id=8,
        outcome="failed",
        ledger_id=102,
        error="timeout",
    )

    row = dict(conn.execute("SELECT * FROM vkpi_agent_tool_run WHERE id=?", (receipt_id,)).fetchone())
    assert row["plan_id"] is None
    assert row["step_index"] == 0
    assert row["status"] == "failed"
    assert row["cost_cents"] == 0
    assert row["error"] == "timeout"


def test_tool_receipt_fails_closed_when_ledger_is_missing(monkeypatch):
    conn = _db()
    monkeypatch.setattr(tool_runs, "table_exists", lambda name: False)
    with pytest.raises(RuntimeError, match="ledger is unavailable"):
        tool_runs.insert_action_tool_run(
            conn,
            action=_action({}),
            action_id=9,
            outcome="success",
            ledger_id=103,
            error="",
        )


def test_execution_effect_requires_a_server_observed_state_change() -> None:
    assert tool_runs.execution_effect_for_action(
        "project_observation", "success", {"created_windows": []}
    ) == "idempotent_noop"
    assert tool_runs.execution_effect_for_action(
        "project_observation", "success", {"created_windows": [91]}
    ) == "state_changed"
    assert tool_runs.execution_effect_for_action(
        "content_candidate", "success", {"review": {"state_changed": False}}
    ) == "idempotent_noop"
    assert tool_runs.execution_effect_for_action(
        "content_candidate", "success", {"review": {"state_changed": True}}
    ) == "state_changed"
    assert tool_runs.execution_effect_for_action(
        "event_followup", "success", {"acknowledged": True}
    ) == "acknowledgement"
