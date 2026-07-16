from __future__ import annotations

from datetime import datetime, timezone

from app.domains.commerce import dealer_scrape


class _Rows:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return list(self.rows)


class _CoverageConnection:
    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        if "FROM vkpi_dealers" in normalized:
            return _Rows(
                [
                    {
                        "id": 1, "state": "NY", "country": "US", "lat": 40.7,
                        "lng": -73.9, "brand_listing_url": "https://dealer.example/viltrox",
                        "source_status": "public_listing_verified",
                        "authorization_status": "needs_viltrox_confirmation",
                        "source_checked_at": "2026-07-13T18:00:00Z", "phone": "1",
                        "contact_email": None, "store_hours": "10-6", "public_services": "retail",
                    },
                    {
                        "id": 2, "state": "CA", "country": "US", "lat": 34.1,
                        "lng": -118.2, "brand_listing_url": None, "source_status": "unverified",
                        "authorization_status": "needs_viltrox_confirmation",
                        "source_checked_at": None, "phone": None, "contact_email": "x@example.test",
                        "store_hours": None, "public_services": None,
                    },
                ]
            )
        if "FROM vkpi_dealer_identity_aliases" in normalized:
            return _Rows(
                [{"dealer_id": 1, "stable_location_key": "dealer_loc_example", "verified_at": "2026-07-13T18:00:00Z"}]
            )
        if "FROM vkpi_source_passports" in normalized:
            return _Rows(
                [{"dealer_id": 1, "verification_status": "verified", "freshness_status_at_write": "fresh", "verified_at": "2026-07-13T18:00:00Z", "stale_after_days": 30}]
            )
        raise AssertionError(normalized)


class _InvalidCoordinateCoverageConnection:
    def execute(self, sql, _params=()):
        normalized = " ".join(str(sql).split())
        if "FROM vkpi_dealers" in normalized:
            return _Rows(
                [
                    {
                        "id": 10, "state": "TX", "country": "US", "lat": 999,
                        "lng": -96.8, "brand_listing_url": None,
                        "source_status": "public_listing_verified",
                        "authorization_status": "needs_viltrox_confirmation",
                        "source_checked_at": None, "phone": None,
                        "contact_email": None, "store_hours": None,
                        "public_services": None,
                    },
                    {
                        "id": 11, "state": "CA", "country": "US", "lat": 34.1,
                        "lng": -118.2, "brand_listing_url": None,
                        "source_status": "unverified",
                        "authorization_status": "needs_viltrox_confirmation",
                        "source_checked_at": None, "phone": None,
                        "contact_email": None, "store_hours": None,
                        "public_services": None,
                    },
                ]
            )
        if "FROM vkpi_dealer_identity_aliases" in normalized:
            return _Rows([])
        if "FROM vkpi_source_passports" in normalized:
            return _Rows([])
        raise AssertionError(normalized)


def test_dealer_record_only_preview_performs_no_database_write_or_read(monkeypatch):
    calls = {"audit": 0, "upsert": 0}

    def forbidden_db(*_args, **_kwargs):
        raise AssertionError("record_only preview touched the database")

    def audit(*_args, **_kwargs):
        calls["audit"] += 1

    def upsert(*_args, **_kwargs):
        calls["upsert"] += 1
        raise AssertionError("record_only preview attempted Dealer upsert")

    monkeypatch.setattr(dealer_scrape, "get_conn", forbidden_db)
    monkeypatch.setattr(dealer_scrape, "table_exists", forbidden_db)
    monkeypatch.setattr(dealer_scrape, "_record_scrape_audit", audit)
    monkeypatch.setattr(dealer_scrape, "upsert_dealer", upsert)

    result = dealer_scrape.scrape_dealers_enqueue(record_only=True, limit=20)

    assert result["ok"] is True
    assert result["record_only"] is True
    assert result["inserted"] == 0
    assert result["requested"] == 5
    assert calls == {"audit": 0, "upsert": 0}
    assert all(item["authorization_status"] == "needs_viltrox_confirmation" for item in result["plan"])


def test_dealer_coverage_separates_registered_rows_identity_and_authorization(monkeypatch):
    monkeypatch.setattr(
        dealer_scrape,
        "table_exists",
        lambda name: name in {
            "vkpi_dealers", "vkpi_dealer_identity_aliases", "vkpi_source_passports",
        },
    )
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: _CoverageConnection())

    result = dealer_scrape.dealer_coverage_summary(
        organization_id=1,
        as_of=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )

    assert result["total"] == 2
    assert result["public_listing_verified"] == 1
    assert result["authorized_confirmed"] == 0
    assert result["authorization_pending"] == 2
    assert result["freshness"] == {"fresh": 1, "stale": 0, "unavailable": 1}
    assert result["identity"] == {"reviewed_alias_dealers": 1, "exact_location_dealers": 1}
    assert result["passports"] == {"dealer_locations": 1, "verified_fresh": 1}
    assert result["global_denominator"] is None
    assert result["global_coverage_rate"] is None
    assert result["claim_boundaries"]["public_listing_proves_authorization"] is False
    assert result["us_jurisdiction_matrix"]["dealer_counts_by_state_dc"] == {
        "CA": 1,
        "NY": 1,
    }
    assert result["us_jurisdiction_matrix"][
        "public_listing_verified_counts_by_state_dc"
    ] == {"NY": 1}
    assert result["us_jurisdiction_matrix"]["located_counts_by_state_dc"] == {
        "NY": 1,
    }
    assert result["us_jurisdiction_matrix"]["coordinate_present_counts_by_state_dc"] == {
        "CA": 1,
        "NY": 1,
    }
    assert result["us_jurisdiction_matrix"]["map_eligible_counts_by_state_dc"] == {
        "NY": 1,
    }
    assert result["located"] == 1
    assert result["coordinate_present"] == 2
    assert result["us_jurisdiction_matrix"]["dealer_entity_count"] == 2
    assert result["us_jurisdiction_matrix"]["map_precision"] == (
        "registered_state_dc_aggregate_not_store_coordinates"
    )


def test_dealer_coverage_rejects_invalid_coordinates_and_unverified_map_rows(monkeypatch):
    monkeypatch.setattr(
        dealer_scrape,
        "table_exists",
        lambda name: name in {
            "vkpi_dealers", "vkpi_dealer_identity_aliases", "vkpi_source_passports",
        },
    )
    monkeypatch.setattr(
        dealer_scrape,
        "get_conn",
        lambda: _InvalidCoordinateCoverageConnection(),
    )

    result = dealer_scrape.dealer_coverage_summary(organization_id=1)

    matrix = result["us_jurisdiction_matrix"]
    assert matrix["coordinate_present_counts_by_state_dc"] == {"CA": 1}
    assert matrix["map_eligible_counts_by_state_dc"] == {}
    assert matrix["located_counts_by_state_dc"] == {}
    assert result["coordinate_present"] == 1
    assert result["located"] == 0
