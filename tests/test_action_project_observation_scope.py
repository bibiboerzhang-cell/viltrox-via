"""Project Observation Action 必须保持单项目边界。"""
from __future__ import annotations

from typing import Any


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _ObservationConn:
    def __init__(self):
        self.shipment_sql = ""
        self.shipment_params: tuple[Any, ...] = ()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        if "WHERE 1=0" in sql:
            return _Cursor()
        if "FROM vkpi_shipments s" in sql:
            self.shipment_sql = sql
            self.shipment_params = params
            return _Cursor()
        raise AssertionError(sql)


def test_scan_delivered_can_be_restricted_to_one_project(monkeypatch):
    from app.domains.projects import observation_windows

    conn = _ObservationConn()
    monkeypatch.setattr(observation_windows, "get_conn", lambda: conn)
    monkeypatch.setattr(observation_windows.scope, "project_filter", lambda alias, staff: ("", ()))
    monkeypatch.setattr(
        observation_windows.scope,
        "scope_context",
        lambda staff: {"scope_mode": "owner"},
    )

    result = observation_windows.scan_delivered_into_windows(
        staff={"id": 1},
        project_id=42,
    )

    assert result["status"] == "ok"
    assert result["project_id"] == 42
    assert "AND s.project_id = ?" in conn.shipment_sql
    assert conn.shipment_params[1] == 42


def test_action_executor_passes_approved_project_boundary(monkeypatch):
    from app.domains.actions import executors
    from app.domains.projects import observation_windows

    captured: dict[str, Any] = {}
    audits: list[dict[str, Any]] = []

    def _scan(**kw: Any) -> dict[str, Any]:
        captured.update(kw)
        return {"status": "ok", "project_id": kw["project_id"], "created": [91]}

    monkeypatch.setattr(observation_windows, "scan_delivered_into_windows", _scan)
    monkeypatch.setattr(
        executors.automation_audit,
        "record_audit",
        lambda **kw: audits.append(kw),
    )

    result = executors._exec_project_observation(
        {"entity_id": "42"},
        {"id": 7},
    )

    assert result["outcome"] == "success"
    assert captured["project_id"] == 42
    assert audits == [
        {
            "project_id": 42,
            "action": "window_open",
            "window_id": 91,
            "reason": "action_execute:project_observation",
        }
    ]


def test_action_executor_rejects_missing_project_id(monkeypatch):
    from app.domains.actions import executors
    from app.domains.projects import observation_windows

    monkeypatch.setattr(
        observation_windows,
        "scan_delivered_into_windows",
        lambda **kw: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    result = executors._exec_project_observation({"entity_id": ""}, {"id": 7})
    assert result["outcome"] == "skipped"
    assert result["reason"] == "project_observation_no_project_id"
