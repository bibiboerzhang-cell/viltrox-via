from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_dealers
from app.domains.commerce import dealer_directory_view, dealer_scrape


def _row(**overrides):
    row = {
        "id": 7,
        "name": "Example Camera",
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "country": "US",
        "lat": 40.7,
        "lng": -74.0,
        "source": "reviewed_source",
        "source_status": "public_listing_verified",
        "authorization_status": "needs_viltrox_confirmation",
        "source_checked_at": "2026-07-14T12:00:00Z",
        "verification_note": "retailer public pages reviewed",
        "brand_listing_url": "https://dealer.example/products/viltrox-lens",
        "location_source_url": "https://dealer.example/stores/new-york",
        "postal_code": "10001",
        "phone": "212-555-0100",
        "contact_email": "store@dealer.example",
        "store_hours": "Mon-Fri",
        "public_services": "Camera retail",
        "created_at": "2026-07-14T12:00:00Z",
    }
    row.update(overrides)
    return row


def test_reviewed_persistence_contract_requires_observed_migration_fields():
    missing = dealer_directory_view.reviewed_persistence_contract()
    assert missing["supported"] is False
    assert missing["status"] == "migration_required"
    assert "evidence_json" in missing["missing_durable_fields"]

    ready = dealer_directory_view.reviewed_persistence_contract(
        dealer_directory_view.REVIEWED_DEALER_DURABLE_FIELDS
    )
    assert ready["supported"] is True
    assert ready["status"] == "ready"
    assert ready["contract_version"] == 1
    assert ready["missing_durable_fields"] == []
    assert ready["automatic_promotion"] is False


def test_migration_visible_legacy_review_fails_closed_until_v1_receipt():
    legacy = dealer_directory_view.project_dealer(
        _row(review_contract_version=0),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    reviewed = dealer_directory_view.project_dealer(
        _row(
            source_id="dealer_source_example_midtown",
            stable_org_key="dealer_org_12345678",
            stable_location_key="dealer_loc_12345678",
            reviewer_id="staff_7",
            reviewed_at="2026-07-14T12:05:00Z",
            evidence_json={
                "claim_status": "descriptive_only",
                "source": {
                    "source_id": "dealer_source_example_midtown",
                    "source_url": "https://dealer.example/stores/new-york",
                    "reviewer_id": "staff_7",
                    "value_status": "observed",
                },
                "product": {
                    "source_url": "https://dealer.example/products/viltrox-lens",
                    "value_status": "observed",
                },
            },
            review_contract_version=1,
        ),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert legacy["truth_status"]["candidate"] is True
    assert legacy["truth_status"]["public_listing"] == "unverified"
    assert legacy["stored_source_status"] == "public_listing_verified"
    assert legacy["source_status"] == "unverified"
    assert legacy["review_contract"]["status"] == "legacy_unverified"
    assert reviewed["truth_status"]["candidate"] is False
    assert reviewed["truth_status"]["public_listing"] == "verified"
    assert reviewed["review_contract"]["status"] == "verified"
    assert reviewed["review_contract"]["evidence_receipt_present"] is True
    assert "evidence_json" not in reviewed
    assert reviewed["provenance"]["public_listing"]["reviewer_id"] == "staff_7"


def test_v1_review_without_exact_evidence_receipt_still_fails_closed():
    incomplete = dealer_directory_view.project_dealer(
        _row(
            source_id="dealer_source_example_midtown",
            stable_org_key="dealer_org_12345678",
            stable_location_key="dealer_loc_12345678",
            reviewer_id="staff_7",
            reviewed_at="2026-07-14T12:05:00Z",
            evidence_json=None,
            review_contract_version=1,
        ),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert incomplete["stored_source_status"] == "public_listing_verified"
    assert incomplete["source_status"] == "unverified"
    assert incomplete["truth_status"]["candidate"] is True
    assert incomplete["review_contract"] == {
        "schema_visible": True,
        "version": 1,
        "valid": False,
        "evidence_receipt_present": False,
        "status": "review_receipt_invalid",
        "automatic_promotion": False,
        "claim_status": "descriptive_only",
    }


def test_reviewed_upsert_persists_v1_identity_and_bounded_evidence(monkeypatch):
    from app.domains.events import radar_quality

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Conn:
        def __init__(self):
            self.sql = ""
            self.params = ()

        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith("SELECT 1 FROM vkpi_dealers"):
                return Result(None)
            self.sql = normalized
            self.params = tuple(params)
            return Result(
                {
                    "id": 19,
                    "source_status": "public_listing_verified",
                    "authorization_status": "needs_viltrox_confirmation",
                    "source_checked_at": "2026-07-14T12:00:00Z",
                    "verification_note": "reviewed",
                    "review_contract_version": 1,
                }
            )

        def commit(self):
            return None

    conn = Conn()
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)
    monkeypatch.setattr(
        dealer_scrape,
        "_dealer_table_columns",
        lambda: set(dealer_directory_view.REVIEWED_DEALER_DURABLE_FIELDS),
    )
    monkeypatch.setattr(
        radar_quality,
        "audit_dealer_candidates",
        lambda _rows: {"import_gate": {"allowed": True}},
    )
    payload = _row(
        source_id="dealer_source_example_midtown",
        stable_org_key="dealer_org_12345678",
        stable_location_key="dealer_loc_12345678",
        reviewer_id="staff_7",
        evidence_scope="dealer_location_listing",
        value_status="observed",
        viltrox_product_evidence={
            "status": "public_listing_observed",
            "source_url": "https://dealer.example/products/viltrox-lens",
            "checked_at": "2026-07-14T12:00:00Z",
            "reviewer_id": "staff_7",
            "evidence_scope": "dealer_viltrox_product_page",
            "value_status": "observed",
        },
    )

    result = dealer_scrape.upsert_dealer(
        payload,
        ingest_class="reviewed_public_listing",
    )

    assert result["review_contract_version"] == 1
    assert "source_id, stable_org_key, stable_location_key, reviewer_id" in conn.sql
    assert "evidence_json" in conn.sql
    assert "review_contract_version = excluded.review_contract_version" in conn.sql
    evidence = json.loads(next(value for value in conn.params if isinstance(value, str) and value.startswith('{"activity"')))
    assert evidence["claim_status"] == "descriptive_only"
    assert evidence["source"]["source_id"] == "dealer_source_example_midtown"
    assert evidence["product"]["value_status"] == "observed"


def test_projection_keeps_listing_product_authorization_and_inventory_separate():
    item = dealer_directory_view.project_dealer(
        _row(),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert item["website_url"] == "https://dealer.example"
    assert item["truth_status"] == {
        "candidate": False,
        "public_listing": "verified",
        "product_evidence": "current_public_url",
        "viltrox_authorization": "pending",
        "current_inventory": "unknown",
    }
    assert item["channel_evidence"]["offline_location"] == "public_listing_verified"
    assert item["channel_evidence"]["online_product_page"] == "current_public_url"
    assert item["channel_evidence"]["online_sales"] == "unknown"
    assert item["product_evidence"]["current_inventory"] == "unknown"
    assert item["authorization_evidence"]["official_viltrox_source_url"] is None
    assert item["social_links"] == []
    assert item["social_status"] == "not_collected"
    assert item["coverage_scope"]["scope"] == "registered_location_only"
    assert item["last_verified_at"] == "2026-07-14T12:00:00Z"
    assert item["freshness_status"] == "fresh"


def test_product_page_status_separates_declared_verified_and_current():
    current = dealer_directory_view.project_dealer(
        _row(source_checked_at="2026-07-14T12:00:00Z"),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    stale = dealer_directory_view.project_dealer(
        _row(source_checked_at="2026-01-01T12:00:00Z"),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    declared = dealer_directory_view.project_dealer(
        _row(source_status="unverified"),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert current["product_evidence"]["status"] == "current_public_url"
    assert stale["product_evidence"]["status"] == "verified_public_url"
    assert declared["product_evidence"]["status"] == "declared_public_url"


def test_authorization_requires_official_viltrox_url_and_verified_time():
    status_only = dealer_directory_view.project_dealer(
        _row(authorization_status="authorized_confirmed"),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )
    complete = dealer_directory_view.project_dealer(
        _row(
            authorization_status="authorized_confirmed",
            authorization_evidence={
                "official_viltrox_source_url": "https://www.viltrox.com/dealers/example-camera",
                "verified_at": "2026-07-14T14:00:00Z",
            },
        ),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert status_only["authorization_status"] == "needs_viltrox_confirmation"
    assert status_only["truth_status"]["viltrox_authorization"] == "pending"
    assert complete["authorization_status"] == "authorized_confirmed"
    assert complete["truth_status"]["viltrox_authorization"] == "confirmed"
    assert complete["authorization_evidence"]["verified_at"] == "2026-07-14T14:00:00Z"


def test_candidate_with_product_url_never_becomes_public_or_authorized():
    item = dealer_directory_view.project_dealer(
        _row(
            source_status="unverified",
            authorization_status="needs_viltrox_confirmation",
        ),
        as_of=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert item["truth_status"]["candidate"] is True
    assert item["truth_status"]["public_listing"] == "unverified"
    assert item["truth_status"]["product_evidence"] == "declared_public_url"
    assert item["truth_status"]["viltrox_authorization"] == "pending"
    assert item["last_verified_at"] is None
    assert item["freshness_status"] == "unverified"


def test_filters_cover_state_city_channels_and_independent_evidence():
    reviewed = dealer_directory_view.project_dealer(_row())
    candidate = dealer_directory_view.project_dealer(
        _row(id=8, source_status="unverified", brand_listing_url=None)
    )

    assert dealer_directory_view.dealer_matches(
        reviewed,
        state="ny",
        city="new york",
        channel="both",
        evidence_status="public_listing_verified",
        product_evidence="available",
        authorization="pending",
    )
    assert dealer_directory_view.dealer_matches(
        candidate,
        evidence_status="candidate",
        product_evidence="missing",
    )
    assert not dealer_directory_view.dealer_matches(
        reviewed,
        authorization="confirmed",
    )
    with pytest.raises(ValueError, match="channel must be one of"):
        dealer_directory_view.dealer_matches(reviewed, channel="pretend_store")


def test_map_pins_exclude_candidates_and_expose_contact_and_provenance(monkeypatch):
    rows = [_row(), _row(id=8, name="Candidate", source_status="unverified")]

    class Result:
        def fetchall(self):
            return rows

    class Conn:
        sql = ""

        def execute(self, sql, _params=()):
            self.sql = " ".join(str(sql).split())
            return Result()

    conn = Conn()
    monkeypatch.setattr(dealer_scrape, "table_exists", lambda name: name == "vkpi_dealers")
    monkeypatch.setattr(dealer_scrape, "get_conn", lambda: conn)

    pins = dealer_scrape.list_dealer_pins(product_evidence="available")

    assert "source_status = 'public_listing_verified'" in conn.sql
    assert [pin["name"] for pin in pins] == ["Example Camera"]
    assert pins[0]["website_url"] == "https://dealer.example"
    assert pins[0]["phone"] == "212-555-0100"
    assert pins[0]["truth_status"]["current_inventory"] == "unknown"
    assert pins[0]["provenance"]["product"]["source_url"].endswith("viltrox-lens")


def test_map_pins_page_past_5000_and_drop_invalid_coordinates(monkeypatch):
    rows = [
        _row(id=index, lat=30 + (index % 10), lng=-120 + (index % 10))
        for index in range(5002)
    ]
    rows.append(_row(id=5002, lat=95, lng=-74))
    offsets = []

    def fake_list(**kwargs):
        offsets.append(kwargs["offset"])
        start = kwargs["offset"]
        return rows[start : start + kwargs["limit"]]

    monkeypatch.setattr(dealer_scrape, "list_dealers", fake_list)

    pins = dealer_scrape.list_dealer_pins()

    assert offsets == [0, 5000]
    assert len(pins) == 5002
    assert {pin["id"] for pin in pins} == set(range(5002))


def test_router_forwards_filters_and_rejects_non_us_state(monkeypatch):
    captured = {"list": {}, "count": {}}

    def fake_list(**kwargs):
        captured["list"].update(kwargs)
        return []

    def fake_count(**kwargs):
        captured["count"].update(kwargs)
        return 502

    monkeypatch.setattr(vkpi_dealers.dealer_scrape, "list_dealers", fake_list)
    monkeypatch.setattr(vkpi_dealers.dealer_scrape, "count_dealers", fake_count)
    result = vkpi_dealers.list_dealers_route(
        limit=25,
        offset=5,
        state="ny",
        city="New York",
        channel="both",
        evidence_status="public_listing_verified",
        product_evidence="available",
        authorization="pending",
        staff={"organization_id": 1},
    )

    assert result["truth_boundaries"]["product_page_proves_current_inventory"] is False
    expected_filters = {
        "limit": 25,
        "offset": 5,
        "state": "NY",
        "city": "New York",
        "channel": "both",
        "evidence_status": "public_listing_verified",
        "product_evidence": "available",
        "authorization": "pending",
    }
    assert captured["list"] == expected_filters
    assert captured["count"] == {
        key: value for key, value in expected_filters.items() if key not in {"limit", "offset"}
    }
    assert result["total_count"] == 502
    assert result["page"] == {
        "limit": 25,
        "offset": 5,
        "returned": 0,
        "next_offset": 30,
        "has_more": True,
    }

    with pytest.raises(HTTPException) as exc_info:
        vkpi_dealers.list_dealers_route(
            limit=25,
            offset=0,
            state="ZZ",
            city=None,
            channel="all",
            evidence_status="all",
            product_evidence="all",
            authorization="all",
            staff={"organization_id": 1},
        )
    assert exc_info.value.status_code == 400


def test_locations_route_forwards_evidence_status(monkeypatch):
    captured = {}

    def fake_pins(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(vkpi_dealers.dealer_scrape, "list_dealer_pins", fake_pins)
    response = vkpi_dealers.dealer_locations_route(
        state="ny",
        city=None,
        channel="all",
        evidence_status="candidate",
        product_evidence="all",
        authorization="all",
        staff={"organization_id": 1},
    )

    assert captured["evidence_status"] == "candidate"
    assert response["evidence_status"] == "candidate"
    assert response["pins"] == []


def test_coverage_route_requires_authenticated_organization_scope(monkeypatch):
    captured = {}

    def fake_coverage(**kwargs):
        captured.update(kwargs)
        return {"status": "empty"}

    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "dealer_coverage_summary",
        fake_coverage,
    )

    response = vkpi_dealers.dealer_coverage_route(
        stale_after_days=30,
        staff={"organization_id": 7},
    )
    assert response == {"status": "empty"}
    assert captured == {"organization_id": 7, "stale_after_days": 30}

    with pytest.raises(HTTPException) as exc_info:
        vkpi_dealers.dealer_coverage_route(
            stale_after_days=30,
            staff={},
        )
    assert exc_info.value.status_code == 403
