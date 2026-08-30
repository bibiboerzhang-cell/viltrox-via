from __future__ import annotations

import json
from typing import Any

import pytest

from app.db.repositories import via_control_stats as stats


NOW = "2026-08-29T07:00:00Z"


def _sql(sql: str) -> str:
    return " ".join(str(sql).split())


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row


class _FakeConnection:
    def __init__(
        self,
        events: list[tuple[Any, ...]],
        *,
        select_rows: list[Any],
        insert_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.select_rows = list(select_rows)
        self.insert_error = insert_error
        self.insert_attempts = 0

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        normalized = _sql(sql)
        self.events.append(("execute", normalized, params))
        if normalized.startswith("SELECT"):
            return _Result(self.select_rows.pop(0))
        if normalized.startswith("INSERT"):
            self.insert_attempts += 1
            if self.insert_error is not None and self.insert_attempts == 1:
                raise self.insert_error
        return _Result()

    def commit(self) -> None:
        self.events.append(("commit",))


def _row(*, current: bool, marker: str = "row") -> dict[str, Any]:
    shared = {
        "marker": marker,
        "memory_kind": "old-kind",
        "memory_tier": "old-tier",
        "target_type": "old-target",
        "target_id": "old-id",
        "status": "old-status",
        "confirmed_hits": 4,
        "reinforcement_count": 5,
        "cumulative_reward": 6.5,
        "last_hit_at": "2026-08-01T00:00:00Z",
        "last_promoted_at": "2026-08-02T00:00:00Z",
        "metrics_json": '{"base": 1}',
    }
    if current:
        shared.update(
            {
                "user_id": 7,
                "session_key": "old-session",
                "fact_key": "old-fact",
                "source_ref": "old-source",
                "decay_state": "aging",
            }
        )
    return shared


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: bool,
    select_rows: list[Any],
    insert_error: Exception | None = None,
    event_sink: list[tuple[Any, ...]] | None = None,
    **overrides: Any,
) -> tuple[Any, list[tuple[Any, ...]], list[tuple[str, tuple[Any, ...]]]]:
    events: list[tuple[Any, ...]] = event_sink if event_sink is not None else []
    conn = _FakeConnection(events, select_rows=select_rows, insert_error=insert_error)

    def get_conn() -> _FakeConnection:
        events.append(("get_conn",))
        return conn

    def utcnow() -> str:
        events.append(("utcnow",))
        return NOW

    def table_columns(actual_conn: Any, table_name: str) -> set[str]:
        assert actual_conn is conn
        assert table_name == "via_memory_retention_stats"
        events.append(("columns",))
        return {"retention_key"} if current else {"memory_key"}

    def mapper(row: Any) -> dict[str, Any]:
        events.append(("map", row["marker"]))
        return {"mapped": row["marker"]}

    monkeypatch.setattr(stats, "get_conn", get_conn)
    monkeypatch.setattr(stats, "_utcnow", utcnow)
    monkeypatch.setattr(stats, "_table_columns", table_columns)
    monkeypatch.setattr(stats, "_memory_retention_from_row", mapper)
    kwargs: dict[str, Any] = {
        "retention_key": " key ",
        "user_id": 0,
        "session_key": "new-session",
        "memory_tier": "new-tier",
        "memory_kind": "new-kind",
        "fact_key": "new-fact",
        "target_type": " video ",
        "target_id": " 42 ",
        "confirmed_hit_increment": 2,
        "reinforcement_increment": 3,
        "reward_delta": 1.25,
        "metrics": {"new": 2},
    }
    kwargs.update(overrides)
    result = stats.upsert_via_memory_retention_stat(**kwargs)
    sql_trace = [
        (event[1], event[2])
        for event in events
        if event[0] == "execute"
    ]
    return result, events, sql_trace


def _event_order(events: list[tuple[Any, ...]]) -> list[str]:
    return [event[0] for event in events]


def test_current_schema_existing_update_freezes_sql_params_and_transaction_order(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _row(current=True)
    final = {"marker": "current-final"}
    result, events, trace = _invoke(
        monkeypatch,
        current=True,
        select_rows=[existing, final],
    )

    assert result == {"mapped": "current-final"}
    assert _event_order(events) == [
        "get_conn", "utcnow", "columns", "execute", "columns", "execute",
        "columns", "execute", "commit", "map",
    ]
    assert [sql.split()[0] for sql, _params in trace] == ["SELECT", "UPDATE", "SELECT"]
    assert "WHERE retention_key=?" in trace[0][0]
    assert trace[0][1] == ("key",)
    assert trace[1][1] == (
        7,
        "new-session",
        "new-tier",
        "new-kind",
        "new-fact",
        "video:42",
        6,
        8,
        7.75,
        "2026-08-01T00:00:00Z",
        "2026-08-02T00:00:00Z",
        "aging",
        "old-status",
        '{"base": 1, "new": 2, "target_type": "video", "target_id": "42"}',
        NOW,
        "key",
    )
    assert trace[2][1] == ("key",)


def test_legacy_schema_existing_update_preserves_old_where_key(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = _row(current=False)
    result, events, trace = _invoke(
        monkeypatch,
        current=False,
        select_rows=[existing, {"marker": "legacy-final"}],
    )

    assert result == {"mapped": "legacy-final"}
    assert _event_order(events) == [
        "get_conn", "utcnow", "columns", "execute", "columns", "execute",
        "columns", "execute", "commit", "map",
    ]
    assert [sql.split()[0] for sql, _params in trace] == ["SELECT", "UPDATE", "SELECT"]
    assert trace[0][1] == ("key", "video", "42")
    assert trace[1][1] == (
        "new-kind",
        "new-tier",
        " video ",
        " 42 ",
        "old-status",
        6,
        8,
        7.75,
        "2026-08-01T00:00:00Z",
        "2026-08-02T00:00:00Z",
        '{"base": 1, "new": 2, "target_type": "video", "target_id": "42"}',
        NOW,
        "key",
        "old-target",
        "old-id",
    )
    assert trace[2][1] == ("key", "video", "42")


@pytest.mark.parametrize("current", [True, False])
def test_new_row_insert_paths_freeze_defaults_and_commit_after_final_select(
    monkeypatch: pytest.MonkeyPatch,
    current: bool,
) -> None:
    result, events, trace = _invoke(
        monkeypatch,
        current=current,
        select_rows=[None, {"marker": "inserted"}],
        retention_key="",
        memory_key=" memory ",
        source_ref=" explicit ",
        last_hit_at="",
        last_promoted_at="",
        decay_state="",
        status="",
    )

    assert result == {"mapped": "inserted"}
    assert _event_order(events) == [
        "get_conn", "utcnow", "columns", "execute", "columns", "execute",
        "columns", "execute", "commit", "map",
    ]
    assert [sql.split()[0] for sql, _params in trace] == ["SELECT", "INSERT", "SELECT"]
    if current:
        assert trace[1][1] == (
            "memory", 0, "new-session", "new-tier", "new-kind", "new-fact",
            "explicit", 2, 3, 1.25, None, NOW, "fresh", "active",
            '{"new": 2, "target_type": "video", "target_id": "42"}', NOW,
        )
        assert trace[2][1] == ("memory",)
    else:
        assert trace[1][1] == (
            "memory", "new-kind", "new-tier", "video", "42", "active",
            2, 3, 1.25, None, NOW,
            '{"new": 2, "target_type": "video", "target_id": "42"}', NOW,
        )
        assert trace[2][1] == ("memory", "video", "42")


def test_current_insert_unique_collision_reselects_merges_and_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    collision = _row(current=True)
    collision["last_promoted_at"] = ""
    result, events, trace = _invoke(
        monkeypatch,
        current=True,
        select_rows=[None, collision, {"marker": "collision-final"}],
        insert_error=RuntimeError("UNIQUE constraint failed: retention_key"),
    )

    assert result == {"mapped": "collision-final"}
    assert _event_order(events) == [
        "get_conn", "utcnow", "columns", "execute", "columns", "execute",
        "execute", "execute", "columns", "execute", "commit", "map",
    ]
    assert [sql.split()[0] for sql, _params in trace] == [
        "SELECT", "INSERT", "SELECT", "UPDATE", "SELECT",
    ]
    assert trace[2][1] == ("key",)
    assert trace[3][1][10] == NOW
    assert json.loads(trace[3][1][13]) == {
        "base": 1,
        "new": 2,
        "target_type": "video",
        "target_id": "42",
    }


@pytest.mark.parametrize(
    ("error", "expected_order"),
    [
        (
            RuntimeError("database locked"),
            ["get_conn", "utcnow", "columns", "execute", "columns", "execute"],
        ),
        (
            RuntimeError("duplicate key value violates unique constraint"),
            [
                "get_conn", "utcnow", "columns", "execute", "columns", "execute",
                "execute",
            ],
        ),
    ],
)
def test_current_insert_failure_re_raises_without_commit_or_mapping(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_order: list[str],
) -> None:
    events: list[tuple[Any, ...]] = []
    rows = [None]
    if "duplicate key" in str(error).lower():
        rows.append(None)
    with pytest.raises(RuntimeError, match=str(error)):
        _invoke(
            monkeypatch,
            current=True,
            select_rows=rows,
            insert_error=error,
            event_sink=events,
        )
    assert _event_order(events) == expected_order
    assert "commit" not in _event_order(events)
    assert "map" not in _event_order(events)
