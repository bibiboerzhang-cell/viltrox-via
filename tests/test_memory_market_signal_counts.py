"""Market-signal readiness aggregation keeps PostgreSQL and SQLite semantics."""
from __future__ import annotations

from typing import Any

from app.domains.memory import common


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.sql = ""

    def execute(self, sql: str, _params: tuple[Any, ...] = ()) -> _Rows:
        self.sql = sql
        return _Rows(self.rows)


def test_market_signal_counts_aggregates_inside_postgres(monkeypatch) -> None:
    connection = _Connection(
        [
            {"signal_type": "launch_plan", "n": 3},
            {"signal_type": "official_content", "n": 12},
            {"signal_type": "", "n": 1},
        ]
    )
    monkeypatch.setattr("app.db.connection.is_postgres_runtime", lambda: True)
    monkeypatch.setattr(common, "get_conn", lambda: connection)
    monkeypatch.setattr(
        common,
        "_load_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("PostgreSQL path must not parse every fact in Python")
        ),
    )

    assert common._market_signal_counts() == {
        "launch_plan": 3,
        "official_content": 12,
    }
    assert "fact_json IS JSON OBJECT" in connection.sql
    assert "BTRIM(SPLIT_PART" in connection.sql
    assert "GROUP BY 1" in connection.sql


def test_market_signal_counts_keeps_portable_json_and_fact_key_fallback(monkeypatch) -> None:
    connection = _Connection(
        [
            {"fact_key": "fallback_kind:item-1", "fact_json": "not-json"},
            {"fact_key": "ignored:item-2", "fact_json": '{"signal_type":"launch_plan"}'},
            {"fact_key": "fallback_kind:item-3", "fact_json": "{}"},
        ]
    )
    monkeypatch.setattr("app.db.connection.is_postgres_runtime", lambda: False)
    monkeypatch.setattr(common, "get_conn", lambda: connection)

    assert common._market_signal_counts() == {
        "fallback_kind": 2,
        "launch_plan": 1,
    }
