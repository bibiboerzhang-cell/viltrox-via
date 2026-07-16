from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.domains.commerce import dealer_scrape
from app.domains.commerce.dealer_identity import (
    propose_stable_location_key,
    propose_stable_org_key,
)
from app.domains.events import radar_import, radar_quality


AS_OF = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)
CHECKED_AT = "2026-07-13T18:00:00Z"


def _dealer() -> dict:
    org_key = propose_stable_org_key(
        "Example Camera",
        country_code="US",
        official_domain="dealer.example",
    )
    return {
        "source_id": "dealer_source_example_midtown",
        "organization_name": "Example Camera",
        "name": "Example Camera · Midtown",
        "official_domain": "dealer.example",
        "stable_org_key": org_key,
        "stable_location_key": propose_stable_location_key(
            org_key,
            country_code="US",
            address="1 Main St",
            postal_code="10001",
        ),
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "US",
        "location_source_url": "https://dealer.example/stores/midtown",
        "brand_listing_url": "https://dealer.example/brands/viltrox",
        "source_checked_at": CHECKED_AT,
        "source_status": "public_listing_verified",
        "reviewer_id": "staff_7",
        "evidence_scope": "dealer_location_listing",
        "value_status": "observed",
        "authorization_status": "needs_viltrox_confirmation",
        "viltrox_product_evidence": {
            "status": "public_listing_observed",
            "source_url": "https://dealer.example/brands/viltrox",
            "checked_at": CHECKED_AT,
            "reviewer_id": "staff_7",
            "evidence_scope": "dealer_viltrox_product_page",
            "value_status": "observed",
        },
    }


def _event_catalog(*, opportunity_checked_at: str | None = CHECKED_AT) -> dict:
    opportunity = {
        "id": "opp_example_20260720",
        "canonical_key": "example|2026-07-20|new-york|example",
        "source_id": "event_source_example",
        "external_event_key": "example-2026-07-20",
        "lane": "dealer_event",
        "title": "Example Camera Event",
        "start_date": "2026-07-20",
        "end_date": "2026-07-20",
        "timezone": "America/New_York",
        "country_code": "US",
        "official_url": "https://events.example/example-2026-07-20",
        "event_status": "scheduled",
        "verification_status": "verified",
        "reviewer_id": "staff_7",
        "evidence_scope": "event_official_listing",
        "value_status": "observed",
        "dealer_stable_location_key": "dealer_loc_aaaaaaaa",
        "viltrox_presence_status": "unknown",
    }
    if opportunity_checked_at is not None:
        opportunity["source_checked_at"] = opportunity_checked_at
    return {
        "global_complete": False,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "checked_at": CHECKED_AT,
        "sources": [
            {
                "id": "event_source_example",
                "source_kind": "dealer_event",
                "canonical_url": "https://events.example/",
                "country_code": "US",
                "timezone": "America/New_York",
                "status": "active",
                "enabled": True,
                "source_checked_at": CHECKED_AT,
                "reviewer_id": "staff_7",
                "evidence_scope": "event_source_listing",
                "value_status": "observed",
            }
        ],
        "opportunities": [opportunity],
    }


def test_dealer_audit_keeps_one_truth_bounded_evidence_record_per_candidate():
    row = _dealer()
    unresolved = deepcopy(row)
    unresolved.update(
        source_id="dealer_source_example_unresolved",
        name="Example Camera · Unresolved",
        address="2 Main St",
        stable_org_key="",
        stable_location_key="",
        source_checked_at=None,
        source_status="unverified",
        reviewer_id="",
    )

    report = radar_quality.audit_dealer_candidates([row, unresolved], as_of=AS_OF)
    records = report["evidence_records"]

    assert records["grain"] == "one_record_per_input_candidate"
    assert records["association_policy"] == "stable_location_key_exact_only_no_fuzzy_merge"
    assert len(records["items"]) == 2
    reviewed, pending = records["items"]
    assert reviewed == {
        "candidate_index": 0,
        "source_id": "dealer_source_example_midtown",
        "declared_stable_location_key": row["stable_location_key"],
        "exact_stable_location_key": row["stable_location_key"],
        "source_url": "https://dealer.example/stores/midtown",
        "observed_at": "2026-07-13T18:00:00+00:00",
        "declared_review_status": "public_listing_verified",
        "review_status": "reviewed_current",
        "reviewer_id": "staff_7",
        "evidence_scope": "dealer_location_listing",
        "value_status": "observed",
        "association_status": "exact_reviewed_location",
        "import_eligible": True,
    }
    assert pending["source_url"] == "https://dealer.example/stores/midtown"
    assert pending["observed_at"] is None
    assert pending["declared_review_status"] == "unverified"
    assert pending["review_status"] == "not_reviewed"
    assert pending["declared_stable_location_key"] is None
    assert pending["exact_stable_location_key"] is None
    assert pending["association_status"] == "unlinked_no_fuzzy_match"
    assert pending["import_eligible"] is False
    assert report["coverage"]["global_location_coverage"]["denominator"] is None
    assert report["coverage"]["global_location_coverage"]["rate"] is None


def test_event_audit_keeps_source_and_opportunity_evidence_records():
    report = radar_quality.audit_event_catalog(_event_catalog(), as_of=AS_OF)
    records = report["evidence_records"]

    assert records["source_grain"] == "one_record_per_catalog_source"
    assert records["opportunity_grain"] == "one_record_per_catalog_opportunity"
    assert records["association_policy"] == "source_id_and_stable_location_key_exact_only"
    assert records["sources"][0] == {
        "source_index": 0,
        "source_id": "event_source_example",
        "source_url": "https://events.example/",
        "observed_at": "2026-07-13T18:00:00+00:00",
        "declared_review_status": None,
        "review_status": "reviewed_current",
        "reviewer_id": "staff_7",
        "evidence_scope": "event_source_listing",
        "value_status": "observed",
        "operational_status": "active",
        "import_eligible": True,
    }
    opportunity = records["opportunities"][0]
    assert opportunity["source_url"] == "https://events.example/example-2026-07-20"
    assert opportunity["observed_at"] == "2026-07-13T18:00:00+00:00"
    assert opportunity["declared_review_status"] == "verified"
    assert opportunity["review_status"] == "reviewed_current"
    assert opportunity["exact_stable_location_key"] is None
    assert opportunity["association_status"] == "declared_exact_key_unresolved"
    assert opportunity["import_eligible"] is True
    assert report["coverage"]["global_source_coverage"]["denominator"] is None
    assert report["coverage"]["global_source_coverage"]["rate"] is None


def test_opportunity_cannot_borrow_source_timestamp_as_its_own_observation():
    report = radar_quality.audit_event_catalog(
        _event_catalog(opportunity_checked_at=None),
        as_of=AS_OF,
    )

    record = report["evidence_records"]["opportunities"][0]
    assert record["source_url"] == "https://events.example/example-2026-07-20"
    assert record["observed_at"] is None
    assert record["review_status"] == "review_timestamp_unavailable"
    assert record["import_eligible"] is False
    assert report["import_gate"]["allowed"] is False
    assert "event.activity_observed_at_missing_or_stale" in {
        item["code"] for item in report["issues"]
    }


def test_evidence_record_grain_is_preserved_for_invalid_candidate_rows():
    dealer_report = radar_quality.audit_dealer_candidates(
        [_dealer(), "invalid"],  # type: ignore[list-item]
        as_of=AS_OF,
    )
    event_catalog = _event_catalog()
    event_catalog["sources"].append("invalid")
    event_catalog["opportunities"].append("invalid")
    event_report = radar_quality.audit_event_catalog(event_catalog, as_of=AS_OF)

    assert len(dealer_report["evidence_records"]["items"]) == 2
    assert dealer_report["evidence_records"]["items"][1]["review_status"] == (
        "invalid_candidate_row"
    )
    assert len(event_report["evidence_records"]["sources"]) == 2
    assert len(event_report["evidence_records"]["opportunities"]) == 2
    assert event_report["evidence_records"]["sources"][1]["import_eligible"] is False
    assert event_report["evidence_records"]["opportunities"][1]["import_eligible"] is False


def test_dealer_preview_preserves_evidence_truth_without_upgrading_review_status(monkeypatch):
    candidate = _dealer()
    candidate.update(
        source_status="unverified",
        source_checked_at=None,
        reviewer_id="",
    )
    monkeypatch.setattr(dealer_scrape, "_fetch_candidates", lambda _source, _limit: [candidate])
    monkeypatch.setattr(dealer_scrape, "_geocode", lambda _candidate: (None, None))

    preview = dealer_scrape.scrape_dealers_enqueue(record_only=True, limit=1)
    item = preview["plan"][0]

    assert item["source_status"] == "unverified"
    assert item["review_status"] == "unverified"
    assert item["source_url"] == candidate["location_source_url"]
    assert item["source_checked_at"] is None
    assert item["observed_at"] is None
    assert item["reviewer_id"] is None
    assert item["stable_location_key"] == candidate["stable_location_key"]


def test_observation_identity_binds_source_observed_at_and_review_status():
    base = {
        "opportunity_content_hash": "a" * 64,
        "source_url": "https://events.example/example-2026-07-20",
        "observed_at": CHECKED_AT,
        "review_status": "quality_contract_accepted",
        "reviewer_id": "staff_7",
        "evidence_scope": "event_official_listing",
        "value_status": "observed",
        "dealer_stable_location_key": "dealer_loc_aaaaaaaa",
    }
    first = radar_import.observation_identity_hash(**base)

    assert first == radar_import.observation_identity_hash(**base)
    for field, replacement in (
        ("source_url", "https://events.example/updated"),
        ("observed_at", "2026-07-13T19:00:00Z"),
        ("review_status", "rejected"),
        ("reviewer_id", "staff_8"),
        ("dealer_stable_location_key", "dealer_loc_bbbbbbbb"),
    ):
        changed = dict(base)
        changed[field] = replacement
        assert radar_import.observation_identity_hash(**changed) != first
