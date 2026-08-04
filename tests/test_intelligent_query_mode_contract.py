from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from app.domains.intelligent_query import QueryScopeDenied, QueryValidationError, execute_query
from app.domains.intelligent_query import service


NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
MANAGER = {"id": 7, "staff_id": 7, "role": "manager", "organization_id": 1}
MEMBER = {"id": 8, "staff_id": 8, "role": "employee", "organization_id": 1}


class BombConnection:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("this mode must not access the database")


def _overview_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE vkpi_kol_pool "
        "(id INTEGER PRIMARY KEY, duplicate_of_id INTEGER, updated_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool VALUES (?,?,?)",
        [
            (1, None, "2026-08-04T10:00:00Z"),
            (2, None, "2026-08-04T09:00:00Z"),
            (3, 1, "2026-08-01T09:00:00Z"),
        ],
    )
    return conn


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_search_mode_fails_closed_without_resolver_sql_or_fallback(
    monkeypatch: pytest.MonkeyPatch,
    locale: str,
) -> None:
    def bomb_resolver(*_args, **_kwargs):
        raise AssertionError("unsupported search mode must stop before intent routing")

    monkeypatch.setattr(service, "resolve_intent", bomb_resolver)
    result = execute_query(
        {
            "query": "目前 KOL 数量",
            "locale": locale,
            "mode": "search",
            "filters": {"intent": "kol.pool.overview"},
        },
        staff=MANAGER,
        conn=BombConnection(),
        now=NOW,
    )

    assert result["status"] == "needs_clarification"
    assert result["intent"] == "unknown"
    assert result["degraded_reason"] == "search_mode_not_implemented"
    assert result["facts"] == []
    assert result["evidence"] == []
    assert result["coverage"]["status"] == "unknown"
    assert result["trace"]["requested_mode"] == "search"
    assert result["trace"]["execution_mode"] == "search_unavailable"
    assert result["trace"]["deterministic"] is False
    assert result["trace"]["search_executed"] is False
    assert result["missing_fields"][0]["field"] == "mode.search"


@pytest.mark.parametrize(
    "mode,selected_by",
    [("auto", "intent_router"), ("deterministic", "caller")],
)
def test_supported_modes_report_the_actual_deterministic_lane(
    mode: str,
    selected_by: str,
) -> None:
    result = execute_query(
        {"query": "目前 KOL 数量", "mode": mode},
        staff=MANAGER,
        conn=_overview_db(),
        now=NOW,
    )

    assert result["intent"] == "kol.pool.overview"
    assert result["trace"]["requested_mode"] == mode
    assert result["trace"]["execution_mode"] == "deterministic"
    assert result["trace"]["mode_selected_by"] == selected_by
    assert result["trace"]["deterministic"] is True
    assert result["trace"]["search_executed"] is False
    facts = {item["key"]: item["value"] for item in result["facts"]}
    assert facts["kol.total"] == 2


@pytest.mark.parametrize("mode", ["auto", "deterministic"])
def test_unknown_supported_mode_clarifies_without_silently_searching(mode: str) -> None:
    result = execute_query(
        {"query": "这个事情你怎么看？", "mode": mode},
        staff=MANAGER,
        conn=BombConnection(),
        now=NOW,
    )

    assert result["status"] == "needs_clarification"
    assert result["degraded_reason"] == "intent_not_resolved"
    assert result["trace"]["requested_mode"] == mode
    assert result["trace"]["execution_mode"] == "clarification"
    assert result["trace"]["search_executed"] is False


def test_search_mode_still_enforces_scope_before_any_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "resolve_intent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scope denial must happen before routing")
        ),
    )
    with pytest.raises(QueryScopeDenied):
        execute_query(
            {
                "query": "find KOLs",
                "mode": "search",
                "scope": {"mode": "own", "staff_id": 99},
            },
            staff=MEMBER,
            conn=BombConnection(),
            now=NOW,
        )


def test_unknown_mode_is_rejected_during_request_normalization() -> None:
    with pytest.raises(QueryValidationError, match="mode must be"):
        execute_query(
            {"query": "目前 KOL 数量", "mode": "agent"},
            staff=MANAGER,
            conn=BombConnection(),
            now=NOW,
        )
