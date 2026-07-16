from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OPS_SCRIPTS = REPO_ROOT / "scripts" / "ops"
if str(OPS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(OPS_SCRIPTS))

from app.domains.source_passport_quality import (
    build_source_passport_quality_audit,
    canonical_source_url,
)
from event_radar_audit_common import load_reviewed_dealer_candidates


AS_OF = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
VERIFIED_AT = "2026-07-14T10:00:00Z"
CATALOG_PATH = REPO_ROOT / "backend/app/domains/events/radar_seed_catalog.json"
DEALER_SOURCE_PATH = REPO_ROOT / "backend/app/domains/commerce/dealer_scrape.py"


def _evidence(scope: str, url: str, tier: str) -> dict:
    return {
        "status": "verified",
        "publisher_tier": tier,
        "source_url": url,
        "verified_at": VERIFIED_AT,
        "reviewer_id": "staff_7",
        "evidence_scope": scope,
        "value_status": "observed",
    }


def _dealer(**overrides) -> dict:
    location_url = "https://dealer.example/stores/midtown"
    product_url = "https://dealer.example/brands/viltrox"
    row = {
        "source_id": "dealer_source_example_midtown",
        "stable_org_key": "dealer_org_aaaaaaaa",
        "stable_location_key": "dealer_loc_aaaaaaaa",
        "name": "Example Camera · Midtown",
        "address": "1 Main St",
        "country": "US",
        "location_source_url": location_url,
        "brand_listing_url": product_url,
        "publisher_tier": "retailer_owned",
        "publisher_identity_evidence": _evidence(
            "publisher_identity", location_url, "retailer_owned"
        ),
        "verified_at": VERIFIED_AT,
        "phone": "+1 212 555 0100",
        "contact_evidence": {
            "phone": _evidence("dealer_contact_field", location_url, "retailer_owned")
        },
        "social_evidence": {
            "instagram": _evidence(
                "dealer_social_profile",
                "https://www.instagram.com/examplecamera/",
                "platform_hosted_profile",
            )
        },
        "viltrox_product_evidence": _evidence(
            "dealer_viltrox_product_page", product_url, "retailer_owned"
        ),
        "activity_evidence": _evidence(
            "dealer_activity_page",
            "https://dealer.example/events",
            "retailer_owned",
        ),
    }
    row.update(overrides)
    return row


def _catalog(**source_overrides) -> dict:
    source_url = "https://events.example/calendar"
    source = {
        "id": "event_source_example",
        "name": "Example Events",
        "source_kind": "dealer_event",
        "country_code": "US",
        "timezone": "America/New_York",
        "region": "North America",
        "status": "active",
        "canonical_url": source_url,
        "publisher_tier": "organizer_owned",
        "publisher_identity_evidence": _evidence(
            "publisher_identity", source_url, "organizer_owned"
        ),
        "verified_at": VERIFIED_AT,
    }
    source.update(source_overrides)
    opportunity_url = "https://events.example/calendar/example-event"
    return {
        "global_complete": False,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "sources": [source],
        "opportunities": [
            {
                "id": "opp_example_20260720",
                "canonical_key": "example|2026-07-20|new-york|example",
                "source_id": "event_source_example",
                "external_event_key": "example-2026-07-20",
                "lane": "dealer_event",
                "title": "Example Event",
                "start_date": "2026-07-20",
                "end_date": "2026-07-20",
                "country_code": "US",
                "timezone": "America/New_York",
                "official_url": opportunity_url,
                "event_status": "scheduled",
                "verification_status": "verified",
                "dealer_stable_location_key": "dealer_loc_aaaaaaaa",
                "activity_evidence": _evidence(
                    "event_official_listing", opportunity_url, "organizer_owned"
                ),
                "viltrox_presence_status": "unknown",
            }
        ],
    }


def _codes(report: dict) -> set[str]:
    return {str(item["code"]) for item in report["issues"]}


def test_canonical_source_url_is_stable_and_rejects_unsafe_identity_inputs():
    assert canonical_source_url(
        "HTTPS://Example.COM:443/path/?utm_source=mail&b=2&a=1"
    ) == "https://example.com/path?a=1&b=2"
    assert canonical_source_url("https://example.com/path?fbclid=x&a=1") == (
        "https://example.com/path?a=1"
    )
    assert canonical_source_url("https://user:secret@example.com/path") == ""
    assert canonical_source_url("https://example.com/a/../b") == ""
    assert canonical_source_url("https://example.com/path#fragment") == ""
    assert canonical_source_url("http://example.com/path") == ""


def test_official_looking_host_does_not_infer_publisher_tier_or_verification():
    catalog = _catalog()
    source = catalog["sources"][0]
    source.pop("publisher_tier")
    source.pop("publisher_identity_evidence")

    report = build_source_passport_quality_audit(
        catalog,
        [_dealer()],
        as_of=AS_OF,
    )

    counts = report["event_sources"]["counts"]
    assert counts["valid_canonical_urls"] == 1
    assert counts["publisher_tier_declared"] == 0
    assert counts["publisher_identity_verified"] == 0
    assert "source.publisher_tier_missing" in _codes(report)
    assert report["claim_status"] == "descriptive_only"


def test_complete_local_source_passports_are_ready_but_never_global_complete():
    report = build_source_passport_quality_audit(
        _catalog(),
        [_dealer()],
        as_of=AS_OF,
    )

    assert report["ok"] is True
    assert report["quality_status"] == "source_passports_ready_descriptive"
    assert report["local_import_readiness"] == {
        "ready": True,
        "does_not_prove_global_completeness": True,
    }
    assert report["event_sources"]["coverage"]["publisher_identity_verification"][
        "rate"
    ] == 1.0
    assert report["event_opportunities"]["coverage"]["activity_evidence"][
        "rate"
    ] == 1.0
    assert report["event_opportunities"]["coverage"][
        "exact_dealer_location_linkage"
    ]["rate"] == 1.0
    dealer = report["dealer_locations"]
    assert dealer["coverage"]["contact_evidence"]["denominator"] == 4
    assert dealer["coverage"]["contact_evidence"]["rate"] == 0.25
    assert dealer["coverage"]["social_evidence"]["denominator"] == 5
    assert dealer["coverage"]["social_evidence"]["rate"] == 0.2
    assert dealer["coverage"]["viltrox_product_evidence"]["rate"] == 1.0
    for key in (
        "global_event_source_coverage",
        "global_dealer_location_coverage",
        "global_country_coverage",
    ):
        assert report["coverage_truth"][key]["denominator"] is None
        assert report["coverage_truth"][key]["rate"] is None
    assert report["coverage_truth"]["global_full_coverage_claim_allowed"] is False
    assert report["claim_boundaries"][
        "retailer_owned_means_viltrox_authorized"
    ] is False


def test_publisher_identity_requires_matching_tier_freshness_reviewer_and_scope():
    invalid_cases = [
        {"publisher_tier": "third_party_listing"},
        {"verified_at": "2025-01-01T00:00:00Z"},
        {"reviewer_id": "anonymous"},
        {"evidence_scope": "dealer_location_listing"},
    ]
    for override in invalid_cases:
        catalog = _catalog()
        evidence = catalog["sources"][0]["publisher_identity_evidence"]
        evidence.update(override)
        report = build_source_passport_quality_audit(
            catalog,
            [_dealer()],
            as_of=AS_OF,
        )
        assert report["event_sources"]["counts"][
            "publisher_identity_verified"
        ] == 0
        assert "source.publisher_identity_unverified" in _codes(report)


def test_evidence_slots_count_only_current_structured_evidence_not_present_values():
    dealer = _dealer()
    dealer["contact_evidence"]["phone"].pop("publisher_tier")
    dealer["social_evidence"]["instagram"]["verified_at"] = "2025-01-01T00:00:00Z"
    dealer["viltrox_product_evidence"]["source_url"] = "http://unsafe.example/viltrox"

    report = build_source_passport_quality_audit(
        _catalog(),
        [dealer],
        as_of=AS_OF,
    )
    counts = report["dealer_locations"]["counts"]
    assert counts["populated_contact_fields"] == 1
    assert counts["current_contact_evidence"] == 0
    assert counts["declared_social_profiles"] == 1
    assert counts["current_social_evidence"] == 0
    assert counts["valid_declared_viltrox_product_urls"] == 1
    assert counts["current_viltrox_product_evidence"] == 0
    assert report["local_import_readiness"]["ready"] is False


def test_exact_deduplication_canonicalizes_tracking_urls_and_natural_keys():
    catalog = _catalog()
    duplicate_source = deepcopy(catalog["sources"][0])
    duplicate_source["id"] = "event_source_duplicate"
    duplicate_source["canonical_url"] += "?utm_source=copy"
    catalog["sources"].append(duplicate_source)
    second_dealer = _dealer(
        source_id="dealer_source_example_duplicate",
        stable_location_key="dealer_loc_bbbbbbbb",
    )

    report = build_source_passport_quality_audit(
        catalog,
        [_dealer(), second_dealer],
        as_of=AS_OF,
    )

    assert "source.url_identity_duplicate" in _codes(report)
    assert "dealer.natural_key_duplicate" in _codes(report)
    assert report["ok"] is False


def test_shared_dealer_listing_page_is_visible_but_not_an_automatic_duplicate():
    second = _dealer(
        source_id="dealer_source_example_uptown",
        stable_location_key="dealer_loc_bbbbbbbb",
        name="Example Camera · Uptown",
        address="2 Main St",
    )
    report = build_source_passport_quality_audit(
        _catalog(),
        [_dealer(), second],
        as_of=AS_OF,
    )

    dedupe = report["dealer_locations"]["deduplication"]
    assert dedupe["duplicate_source_ids"] == []
    assert dedupe["duplicate_stable_location_keys"] == []
    assert dedupe["duplicate_natural_keys"] == []
    assert dedupe["shared_location_source_urls"] == {
        "https://dealer.example/stores/midtown": 2
    }
    assert "dealer.natural_key_duplicate" not in _codes(report)


def test_dealer_activity_link_must_resolve_not_merely_match_key_shape():
    catalog = _catalog()
    catalog["opportunities"][0][
        "dealer_stable_location_key"
    ] = "dealer_loc_bbbbbbbb"

    report = build_source_passport_quality_audit(
        catalog,
        [_dealer()],
        as_of=AS_OF,
    )

    linkage = report["event_opportunities"]["coverage"][
        "exact_dealer_location_linkage"
    ]
    assert linkage == {
        "covered": 0,
        "denominator": 1,
        "rate": 0.0,
        "status": "measured",
        "reason": "",
    }
    assert "opportunity.dealer_location_key_unresolved" in _codes(report)


def test_content_addressed_snapshot_reports_identity_drift_without_raw_values():
    first = build_source_passport_quality_audit(
        _catalog(),
        [_dealer()],
        as_of=AS_OF,
    )
    changed_catalog = _catalog(canonical_url="https://events.example/new-calendar")
    changed = build_source_passport_quality_audit(
        changed_catalog,
        [_dealer(phone="+1 212 555 0199")],
        as_of=AS_OF,
        previous_snapshot=first["snapshot"],
    )

    comparison = changed["change_detection"]
    assert comparison["status"] == "compared"
    assert comparison["identity_drift"] == [
        {
            "scope": "event_sources",
            "entity_key": "event_source_example",
            "changed_fields": ["canonical_url"],
        }
    ]
    dealer_changes = comparison["scopes"]["dealer_locations"]["changed"]
    assert dealer_changes == [
        {
            "entity_key": "dealer_loc_aaaaaaaa",
            "changed_fields": ["contact_values"],
        }
    ]
    rendered_snapshot = json.dumps(changed["snapshot"], sort_keys=True)
    assert "https://events.example/new-calendar" not in rendered_snapshot
    assert "+1 212 555 0199" not in rendered_snapshot
    assert "snapshot.identity_drift_detected" in _codes(changed)


def test_invalid_previous_snapshot_fails_closed_without_fake_change_counts():
    report = build_source_passport_quality_audit(
        _catalog(),
        [_dealer()],
        as_of=AS_OF,
        previous_snapshot={"snapshot_version": 999, "records": {}},
    )

    assert report["change_detection"] == {
        "status": "invalid_previous_snapshot",
        "previous_snapshot_sha256": None,
        "current_snapshot_sha256": report["snapshot"]["snapshot_sha256"],
        "scopes": {},
        "identity_drift": [],
    }


def test_current_bundled_catalog_reports_exact_observed_denominators_and_zero_upgrades():
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    candidates = load_reviewed_dealer_candidates(DEALER_SOURCE_PATH)
    report = build_source_passport_quality_audit(
        catalog,
        candidates,
        as_of=AS_OF,
    )

    assert report["event_sources"]["counts"] == {
        "rows": 72,
        "valid_canonical_urls": 72,
        "publisher_tier_declared": 0,
        "publisher_identity_verified": 0,
        "verified_primary_publishers": 0,
        "verified_secondary_publishers": 0,
        "fresh_source_rows": 0,
    }
    assert report["event_opportunities"]["counts"] == {
        "rows": 25,
        "exact_source_links": 25,
        "valid_official_urls": 25,
        "fresh_activity_evidence": 0,
        "dealer_or_local_rows": 12,
        "exact_dealer_location_links": 0,
    }
    dealer_counts = report["dealer_locations"]["counts"]
    assert dealer_counts["rows"] == 5
    assert dealer_counts["explicit_source_ids"] == 0
    assert dealer_counts["stable_location_keys"] == 0
    assert dealer_counts["valid_location_urls"] == 5
    assert dealer_counts["valid_declared_viltrox_product_urls"] == 5
    assert dealer_counts["publisher_identity_verified"] == 0
    assert dealer_counts["current_contact_evidence"] == 0
    assert dealer_counts["current_social_evidence"] == 0
    assert dealer_counts["current_viltrox_product_evidence"] == 0
    assert report["quality_status"] == "source_passports_incomplete"
    assert report["coverage_truth"]["global_event_source_coverage"]["rate"] is None
    assert report["coverage_truth"]["global_dealer_location_coverage"]["rate"] is None


@pytest.mark.parametrize("stale_after_days", [0, -1, True])
def test_stale_after_days_must_be_positive_integer(stale_after_days):
    with pytest.raises(ValueError, match="positive integer"):
        build_source_passport_quality_audit(
            _catalog(),
            [_dealer()],
            as_of=AS_OF,
            stale_after_days=stale_after_days,
        )
