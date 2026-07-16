from __future__ import annotations

from datetime import date

import pytest

from app.api.routers import vkpi_dealers
from app.domains.events import dealer_activity_view


class _Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(
        self,
        *,
        promoted: bool = False,
        decision_status: str | None = None,
    ):
        self.calls: list[tuple[str, tuple]] = []
        self.promoted = promoted
        self.decision_status = decision_status

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT id,name FROM vkpi_dealers"):
            return _Result(one={"id": 5, "name": "Samy's Camera · Pasadena"})
        if normalized.startswith("SELECT COUNT(*) AS n"):
            return _Result(one={"linked_n": 1, "visible_n": 1})
        if normalized.startswith("SELECT COUNT(*) AS linked_n"):
            return _Result(one={"linked_n": 1, "visible_n": 1})
        return _Result(
            rows=[
                {
                    "id": "opp_1",
                    "title": "Dealer workshop",
                    "lane": "dealer_event",
                    "start_date": date(2026, 8, 1),
                    "source_name": "Dealer official events",
                    "source_kind": "dealer_event",
                    "official_url": "https://dealer.example/events/1",
                    "decision_status": (
                        self.decision_status
                        or ("approved" if self.promoted else "new")
                    ),
                    "verification_status": "verified" if self.promoted else "unverified",
                    "converted_event_id": "evt_approved" if self.promoted else None,
                    "promotion_promoted_at": (
                        "2026-07-15T12:00:00Z" if self.promoted else None
                    ),
                    "promotion_promoted_by": 8 if self.promoted else None,
                }
            ]
        )


def test_dealer_activity_view_is_org_scoped_exact_and_read_only():
    conn = _Conn()
    result = dealer_activity_view.list_dealer_activities(
        5,
        organization_id=17,
        as_of_date=date(2026, 7, 16),
        connection=conn,
    )

    assert result["status"] == "ready"
    assert result["count"] == 1
    assert result["linked_count"] == 1
    assert result["suppressed_count"] == 0
    assert result["automatic_sync"] is False
    assert result["candidate_sync_capability"] == "separate_review_only_pipeline"
    assert result["business_rows_written"] == 0
    assert result["as_of_date"] == "2026-07-16"
    assert result["formal_event_rule"] == "promotion_receipt_required"
    assert result["activities"][0]["association"] == "exact_dealer_id"
    assert result["activities"][0]["record_type"] == "external_opportunity_candidate"
    assert result["activities"][0]["is_internal_event"] is False
    assert result["activities"][0]["promotion_receipt"] == {
        "present": False,
        "event_id": None,
        "promoted_at": None,
        "promoted_by": None,
    }
    activity_sql, activity_params = conn.calls[-1]
    assert "od.organization_id=?" in activity_sql
    assert "od.dealer_id=?" in activity_sql
    assert "od.relation_type='host'" in activity_sql
    assert "s.organization_id" not in activity_sql
    assert "s.status='active'" in activity_sql
    assert "COALESCE(s.enabled,FALSE)=TRUE" in activity_sql
    assert "CURRENT_DATE" not in activity_sql
    assert "LEFT JOIN vkpi_event_opportunity_promotions" in activity_sql
    assert activity_params == (
        17,
        5,
        "2026-07-16",
        "done",
        "ended",
        "cancelled",
        "canceled",
        "closed",
        20,
    )
    assert all(not sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql, _ in conn.calls)


def test_dealer_activity_requires_promotion_receipt_before_claiming_internal_event():
    result = dealer_activity_view.list_dealer_activities(
        5,
        organization_id=17,
        as_of_date="2026-07-16",
        connection=_Conn(promoted=True),
    )

    item = result["activities"][0]
    assert item["converted_event_id"] == "evt_approved"
    assert item["record_type"] == "internal_event"
    assert item["is_internal_event"] is True
    assert item["promotion_receipt"] == {
        "present": True,
        "event_id": "evt_approved",
        "promoted_at": "2026-07-15T12:00:00Z",
        "promoted_by": 8,
    }


def test_dealer_activity_does_not_trust_promoted_label_without_receipt():
    result = dealer_activity_view.list_dealer_activities(
        5,
        organization_id=17,
        as_of_date="2026-07-16",
        connection=_Conn(decision_status="promoted"),
    )

    item = result["activities"][0]
    assert item["decision_status"] == "promoted"
    assert item["converted_event_id"] is None
    assert item["is_internal_event"] is False
    assert item["record_type"] == "external_opportunity_candidate"
    assert item["promotion_receipt"]["present"] is False


class _DisabledSourceConn(_Conn):
    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("SELECT id,name FROM vkpi_dealers"):
            return _Result(one={"id": 5, "name": "Samy's Camera · Pasadena"})
        if normalized.startswith("SELECT COUNT(*) AS linked_n"):
            return _Result(one={"linked_n": 4, "visible_n": 0})
        return _Result(rows=[])


def test_dealer_activity_view_reports_suppressed_exact_links_without_exposing_rows():
    result = dealer_activity_view.list_dealer_activities(
        5, organization_id=17, connection=_DisabledSourceConn()
    )

    assert result["status"] == "pending_source_activation"
    assert result["count"] == 0
    assert result["linked_count"] == 4
    assert result["suppressed_count"] == 4
    assert result["activities"] == []
    assert result["suppression_reason"] == "source_not_active_or_enabled"


def test_dealer_activity_view_rejects_unscoped_or_invalid_identity():
    with pytest.raises(ValueError, match="organization_id"):
        dealer_activity_view.list_dealer_activities(5, organization_id=0, connection=_Conn())
    with pytest.raises(ValueError, match="dealer_id"):
        dealer_activity_view.list_dealer_activities("bad", organization_id=17, connection=_Conn())


def test_dealer_activity_route_forwards_authenticated_org(monkeypatch):
    captured = {}

    def fake(dealer_id, **kwargs):
        captured.update({"dealer_id": dealer_id, **kwargs})
        return {"status": "empty", "activities": [], "count": 0}

    monkeypatch.setattr(dealer_activity_view, "list_dealer_activities", fake)
    response = vkpi_dealers.dealer_activities_route(
        9,
        limit=7,
        include_past=False,
        as_of_date=date(2026, 7, 16),
        staff={"id": 3, "organization_id": 44},
    )
    assert response["status"] == "empty"
    assert captured == {
        "dealer_id": 9,
        "organization_id": 44,
        "limit": 7,
        "include_past": False,
        "as_of_date": date(2026, 7, 16),
    }
