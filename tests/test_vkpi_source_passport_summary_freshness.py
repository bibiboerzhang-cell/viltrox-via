from __future__ import annotations

from datetime import datetime, timezone

from app.domains.commerce import dealer_scrape
from app.domains.events import radar


AS_OF = datetime(2026, 7, 14, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _DealerCoverageConnection:
    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        if "FROM vkpi_dealers" in normalized:
            return _Rows(
                [
                    {
                        "id": dealer_id,
                        "state": "NY",
                        "country": "US",
                        "lat": None,
                        "lng": None,
                        "brand_listing_url": None,
                        "source_status": "unverified",
                        "authorization_status": "needs_viltrox_confirmation",
                        "source_checked_at": None,
                        "phone": None,
                        "contact_email": None,
                        "store_hours": None,
                        "public_services": None,
                    }
                    for dealer_id in (1, 2, 3, 4)
                ]
            )
        if "FROM vkpi_dealer_identity_aliases" in normalized:
            return _Rows([])
        if "FROM vkpi_source_passports" in normalized:
            return _Rows(
                [
                    _passport("dealer_location", "2026-07-13T00:00:00Z", 30, dealer_id=1),
                    _passport("dealer_location", "2026-06-01T00:00:00Z", 30, dealer_id=2),
                    _passport("dealer_location", "2026-07-15T00:00:00Z", 30, dealer_id=3),
                    _passport(
                        "dealer_location",
                        "2026-07-13T00:00:00Z",
                        30,
                        dealer_id=4,
                        freshness_status_at_write="stale",
                    ),
                    _passport("dealer_location", "2026-07-13T00:00:00Z", 30, dealer_id=999),
                ]
            )
        raise AssertionError(normalized)


class _EventSummaryConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        self.statements.append(normalized)
        if normalized.startswith("SELECT source_kind, status, country_code"):
            return _Rows(
                [
                    {
                        "source_kind": "major_expo",
                        "status": "active",
                        "country_code": "US",
                        "evidence_grade": "A1",
                        "n": 3,
                    }
                ]
            )
        if normalized.startswith("SELECT o.lane, o.decision_status"):
            return _Rows(
                [
                    {
                        "lane": "major_expo",
                        "decision_status": "new",
                        "verification_status": "verified",
                        "evidence_grade": "A1",
                        "event_status": "scheduled",
                        "country_code": "US",
                        "region": "NY",
                        "n": 2,
                    }
                ]
            )
        if normalized.startswith("SELECT o.last_verified_at,o.source_checked_at"):
            return _Rows([])
        if normalized.startswith("SELECT last_success_at,last_checked_at"):
            return _Rows([])
        if normalized.startswith("SELECT p.entity_type,p.verification_status"):
            return _Rows(
                [
                    _passport("event_source", "2026-07-13T00:00:00Z", 30),
                    _passport("event_source", "2026-06-01T00:00:00Z", 30),
                    _passport("event_source", "2026-07-15T00:00:00Z", 30),
                    _passport("event_opportunity", "2026-07-13T00:00:00Z", 30),
                    _passport("event_opportunity", "2026-07-01T00:00:00Z", 7),
                ]
            )
        if normalized.startswith("SELECT COUNT(*) AS n"):
            return _Rows([{"n": 0}])
        if normalized.startswith("SELECT run_key,status"):
            return _Rows([])
        raise AssertionError(normalized)


def _passport(
    entity_type: str,
    verified_at: str,
    stale_after_days: int,
    *,
    dealer_id: int | None = None,
    verification_status: str = "verified",
    freshness_status_at_write: str = "fresh",
) -> dict[str, object]:
    return {
        "entity_type": entity_type,
        "dealer_id": dealer_id,
        "verification_status": verification_status,
        "freshness_status_at_write": freshness_status_at_write,
        "verified_at": verified_at,
        "stale_after_days": stale_after_days,
    }


def test_dealer_summary_recomputes_passport_ttl_and_excludes_orphans(monkeypatch):
    monkeypatch.setattr(
        dealer_scrape,
        "table_exists",
        lambda name: name
        in {"vkpi_dealers", "vkpi_dealer_identity_aliases", "vkpi_source_passports"},
    )
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: _DealerCoverageConnection())

    result = dealer_scrape.dealer_coverage_summary(organization_id=1, as_of=AS_OF)

    assert result["passports"] == {"dealer_locations": 4, "verified_fresh": 1}
    assert result["as_of"] == "2026-07-14T00:00:00+00:00"


def test_event_summary_recomputes_passport_ttl_and_queries_only_active_entities(monkeypatch):
    conn = _EventSummaryConnection()
    monkeypatch.setattr(radar, "_require_organization_schema", lambda _conn=None: conn)
    monkeypatch.setattr(radar, "table_exists", lambda _name: True)

    result = radar.summary(organization_id=1, as_of=AS_OF)

    assert result["sources"]["verified_fresh_passports"] == 1
    assert result["opportunities"]["verified_fresh_passports"] == 1
    assert result["us_jurisdiction_matrix"]["covered_states"] == ["NY"]
    assert result["us_jurisdiction_matrix"]["coverage_rate"] is None
    assert result["as_of"] == "2026-07-14T00:00:00+00:00"
    passport_query = next(
        sql
        for sql in conn.statements
        if sql.startswith("SELECT p.entity_type,p.verification_status")
    )
    assert passport_query.count("s.status='active'") == 2
    assert "JOIN vkpi_event_opportunities o" in passport_query
