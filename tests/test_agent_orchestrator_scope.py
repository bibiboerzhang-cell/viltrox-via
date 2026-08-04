"""Agent goal plans are scoped before read or materialization."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_agent_orchestration_plan (
          id INTEGER PRIMARY KEY,
          goal TEXT,
          input_context_json TEXT,
          plan_json TEXT,
          status TEXT,
          estimated_cost_cents INTEGER,
          created_by_staff_id INTEGER,
          created_at TEXT,
          updated_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO vkpi_agent_orchestration_plan VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (1, "owner goal", "{}", "[]", "planned", 10, 7, "now", "now"),
            (2, "other goal", "{}", "[]", "planned", 10, 8, "now", "now"),
        ],
    )
    return conn


def test_member_only_reads_own_goal_plan(monkeypatch):
    from app.domains.agents import orchestrator

    conn = _conn()
    monkeypatch.setattr(orchestrator, "table_exists", lambda _name: True)
    monkeypatch.setattr(orchestrator, "get_conn", lambda: conn)

    assert orchestrator.get_plan(1, staff={"id": 7, "role": "staff"})["goal"] == "owner goal"
    assert orchestrator.get_plan(2, staff={"id": 7, "role": "staff"}) is None
    assert orchestrator.get_plan(1, staff=None) is None


def test_manager_can_audit_any_goal_plan(monkeypatch):
    from app.domains.agents import orchestrator

    conn = _conn()
    monkeypatch.setattr(orchestrator, "table_exists", lambda _name: True)
    monkeypatch.setattr(orchestrator, "get_conn", lambda: conn)

    item = orchestrator.get_plan(2, staff={"id": 99, "role": "manager"})
    assert item is not None
    assert item["created_by_staff_id"] == 8
