"""Hermetic contracts for Event list pagination and UTC date boundaries."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.api.routers import vkpi_events
from app.domains.events import service


class _Result:
    def __init__(self, *, one: Any = None, rows: list[Any] | None = None):
        self._one = one
        self._rows = list(rows or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, *, total: int = 5):
        self.total = total
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params=()):
        normalized = " ".join(str(sql).split())
        args = tuple(params)
        self.calls.append((normalized, args))
        if normalized.startswith("SELECT COUNT(*) AS n FROM vkpi_events"):
            return _Result(one={"n": self.total})
        if normalized.startswith("SELECT * FROM vkpi_events"):
            return _Result(
                rows=[
                    {
                        "id": "evt_page",
                        "title": "Scoped event",
                        "type_key": "expo",
                        "status": "planning",
                        "health_score": 80,
                        "start_date": date(2026, 8, 1),
                        "end_date": date(2026, 8, 2),
                        "location_name": "Venue",
                        "location_city": "New York",
                        "location_country": "US",
                        "location_lat": 40.7,
                        "location_lng": -74.0,
                        "budget_total": 100,
                        "budget_json": {},
                        "team_ids": [],
                        "related_project_ids": [],
                        "invited_kols_json": [],
                    }
                ]
            )
        if "FROM vkpi_event_kol_invites" in normalized:
            return _Result(rows=[])
        raise AssertionError(f"unexpected SQL: {normalized}")


def _install_manager_scope(monkeypatch, conn: _Conn) -> None:
    monkeypatch.setattr(service, "get_conn", lambda: conn)
    monkeypatch.setattr(
        service.scope,
        "event_organization_context",
        lambda _staff, _conn: (7, True),
    )
    monkeypatch.setattr(service, "_can_view_all", lambda _staff: True)


def test_list_events_filters_and_count_share_scope_and_page_contract(monkeypatch):
    conn = _Conn(total=5)
    _install_manager_scope(monkeypatch, conn)

    result = service.list_events(
        {"id": 3, "organization_id": 7},
        limit=2,
        offset=2,
        status="PLANNING",
        owner_id=9,
    )

    assert result["count"] == 1
    assert result["total_count"] == 5
    assert result["offset"] == 2
    assert result["limit"] == 2
    assert result["page"] == {
        "limit": 2,
        "offset": 2,
        "returned": 1,
        "next_offset": 3,
        "has_more": True,
    }
    count_sql, count_params = conn.calls[0]
    page_sql, page_params = conn.calls[1]
    expected_filters = (
        "WHERE organization_id = ? AND LOWER(COALESCE(status, '')) = ? "
        "AND owner_id = ?"
    )
    assert expected_filters in count_sql
    assert expected_filters in page_sql
    assert count_params == (7, "planning", 9)
    assert page_params == (7, "planning", 9, 2, 2)
    assert "LIMIT ? OFFSET ?" in page_sql


def test_upcoming_uses_bound_utc_date_and_excludes_terminal_statuses(monkeypatch):
    conn = _Conn()
    _install_manager_scope(monkeypatch, conn)
    monkeypatch.setattr(
        service,
        "_now",
        lambda: datetime(2026, 7, 16, 23, 59, tzinfo=timezone.utc),
    )

    result = service.list_upcoming_events(
        {"id": 3, "organization_id": 7},
        limit=2,
    )

    sql, params = conn.calls[0]
    assert "CURRENT_DATE" not in sql
    assert "end_date >= ?" in sql
    assert "LOWER(COALESCE(status, '')) NOT IN (?,?,?,?,?)" in sql
    assert params == (
        7,
        "2026-07-16",
        "done",
        "ended",
        "cancelled",
        "canceled",
        "closed",
        2,
    )
    assert result["as_of_date"] == "2026-07-16"
    assert result["count"] == 1


def test_event_routes_forward_pagination_filters_and_explicit_date(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_list(_staff, **kwargs):
        captured["list"] = kwargs
        return {"items": [], "count": 0, "total_count": 0}

    def fake_upcoming(_staff, **kwargs):
        captured["upcoming"] = kwargs
        return {"items": [], "count": 0}

    monkeypatch.setattr(service, "list_events", fake_list)
    monkeypatch.setattr(service, "list_upcoming_events", fake_upcoming)
    staff = {"id": 3, "organization_id": 7}

    vkpi_events.list_events(
        limit=25,
        offset=50,
        status="planning",
        owner_id=9,
        staff=staff,
    )
    vkpi_events.upcoming_events(
        limit=10,
        as_of_date=date(2026, 7, 16),
        staff=staff,
    )

    assert captured == {
        "list": {
            "limit": 25,
            "offset": 50,
            "status": "planning",
            "owner_id": 9,
        },
        "upcoming": {"limit": 10, "as_of_date": date(2026, 7, 16)},
    }
