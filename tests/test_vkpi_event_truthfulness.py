from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domains.events import radar
from app.domains.events import service as events_service


class _Result:
    def __init__(self, row: Any = None, *, rows: list[Any] | None = None, rowcount: int = 0):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _RadarPromotionConnection:
    def __init__(self):
        self.event_insert_sql = ""
        self.event_insert_params: tuple[Any, ...] = ()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized == "PRAGMA table_info(vkpi_events)":
            return _Result(rows=[{"name": "id"}, {"name": "organization_id"}])
        if normalized.startswith("UPDATE vkpi_event_opportunities SET decision_status='promoted'"):
            return _Result(None, rowcount=1)
        if "FROM vkpi_event_opportunities" in normalized:
            return _Result(
                {
                    "id": "opp_truth",
                    "organization_id": 1,
                    "source_status": "active",
                    "source_enabled": True,
                    "decision_status": "approved",
                    "verification_status": "verified",
                    "event_status": "scheduled",
                    "lane": "dealer_event",
                    "title": "Dealer Demo Day",
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-01",
                    "last_verified_at": datetime.now(timezone.utc).isoformat(),
                    "official_url": "https://dealer.example/events/demo",
                }
            )
        if "FROM vkpi_event_opportunity_promotions" in normalized:
            return _Result(None)
        if "SELECT 1 FROM staff" in normalized:
            return _Result({"present": 1})
        if "SELECT 1 FROM organization_members" in normalized:
            return _Result({"present": 1})
        if "SELECT organization_id FROM organization_members" in normalized:
            return _Result({"organization_id": 1})
        if normalized.startswith("INSERT INTO vkpi_events"):
            self.event_insert_sql = normalized
            self.event_insert_params = tuple(params)
        return _Result(None)

    def commit(self):
        return None

    def rollback(self):
        return None


class _EventCreateConnection:
    def __init__(self):
        self.insert_params: tuple[Any, ...] = ()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        if normalized == "PRAGMA table_info(vkpi_events)":
            return _Result(rows=[{"name": "id"}, {"name": "organization_id"}])
        if "SELECT organization_id FROM organization_members" in normalized:
            return _Result(rows=[])
        if "SELECT 1 FROM staff" in normalized:
            return _Result({"present": 1})
        if normalized.startswith("INSERT INTO vkpi_events"):
            self.insert_params = tuple(params)
            return _Result(None)
        if normalized.startswith("SELECT * FROM vkpi_events"):
            return _Result(
                {
                    "id": "evt_truth",
                    "budget_json": {},
                    "team_ids": [],
                    "related_project_ids": [],
                    "invited_kols_json": [],
                }
            )
        return _Result(None)

    def commit(self):
        return None


def test_radar_promotion_starts_with_unknown_health_not_a_hardcoded_perfect_score(monkeypatch):
    conn = _RadarPromotionConnection()
    monkeypatch.setattr(radar, "get_conn", lambda: conn)

    result = radar.promote("opp_truth", staff={"id": 1, "organization_id": 1})

    assert result["ok"] is True
    assert "'planning',NULL" in conn.event_insert_sql
    assert 100 not in conn.event_insert_params


def test_manual_event_without_health_does_not_become_perfect_by_default(monkeypatch):
    conn = _EventCreateConnection()
    monkeypatch.setattr(events_service, "get_conn", lambda: conn)

    events_service.create_event(
        {"id": "evt_truth", "title": "Truth", "start_date": "2026-08-01", "end_date": "2026-08-01"},
        staff={"id": 1},
    )

    assert conn.insert_params[5] is None


def test_explicit_zero_health_is_preserved_instead_of_replaced_with_100(monkeypatch):
    conn = _EventCreateConnection()
    monkeypatch.setattr(events_service, "get_conn", lambda: conn)

    events_service.create_event(
        {
            "id": "evt_truth",
            "title": "Truth",
            "health_score": 0,
            "start_date": "2026-08-01",
            "end_date": "2026-08-01",
        },
        staff={"id": 1},
    )

    assert conn.insert_params[5] == 0
