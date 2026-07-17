from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.api.routers import vkpi_dealers, vkpi_event_radar
from app.domains.commerce import dealer_scrape
from app.domains.commerce.dealer_identity import (
    propose_stable_location_key,
    propose_stable_org_key,
)
from app.domains.events import radar, radar_quality
from app.domains.events.radar_quality_core import _canonical_source_url


AS_OF = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)


def _digest(value) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _manifest(scope: str, rows: list[dict]) -> dict:
    if scope == "event_sources":
        entity_ids = sorted(str(row["id"]).strip() for row in rows)
        source_inventory = [
            {
                "source_id": str(row["id"]).strip(),
                "canonical_url": _canonical_source_url(row["canonical_url"]),
            }
            for row in rows
        ]
    else:
        entity_ids = sorted(str(row["stable_location_key"]).strip() for row in rows)
        source_inventory = [
            {
                "source_id": str(row["source_id"]).strip(),
                "canonical_url": _canonical_source_url(row["location_source_url"]),
            }
            for row in rows
        ]
    source_inventory.sort(key=lambda item: (item["source_id"], item["canonical_url"]))

    return {
        "manifest_version": 1,
        "scope": scope,
        "denominator": len(entity_ids),
        "entity_ids": entity_ids,
        "source_inventory": source_inventory,
        "entity_ids_sha256": _digest(entity_ids),
        "source_inventory_sha256": _digest(source_inventory),
        "as_of": "2026-07-13T18:00:00Z",
        "methodology": "Hermetic exact-id fixture inventory.",
        "reviewer_id": "staff_7",
    }


def _valid_dealer(**overrides) -> dict:
    org_key = propose_stable_org_key(
        "Example Camera",
        country_code="US",
        official_domain="dealer.example",
    )
    location_key = propose_stable_location_key(
        org_key,
        country_code="US",
        address="1 Main St",
        postal_code="10001",
    )
    row = {
        "source_id": "dealer_source_example_midtown",
        "organization_name": "Example Camera",
        "name": "Example Camera · Midtown",
        "official_domain": "dealer.example",
        "stable_org_key": org_key,
        "stable_location_key": location_key,
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "US",
        "location_source_url": "https://dealer.example/stores/midtown",
        "brand_listing_url": "https://dealer.example/brands/viltrox",
        "source_checked_at": "2026-07-13T18:00:00Z",
        "source_status": "public_listing_verified",
        "reviewer_id": "staff_7",
        "evidence_scope": "dealer_location_listing",
        "value_status": "observed",
        "authorization_status": "needs_viltrox_confirmation",
        "phone": "+1 212 555 0100",
        "contact_evidence": {
            "phone": {
                "status": "verified",
                "source_url": "https://dealer.example/stores/midtown",
                "checked_at": "2026-07-13T18:00:00Z",
                "reviewer_id": "staff_7",
                "evidence_scope": "dealer_contact_field",
                "value_status": "observed",
            }
        },
        "social_evidence": {
            "instagram": {
                "status": "unknown",
                "source_url": "",
                "checked_at": None,
            },
            "youtube": {
                "status": "verified",
                "source_url": "https://youtube.com/@examplecamera",
                "checked_at": "2026-07-13T18:00:00Z",
                "reviewer_id": "staff_7",
                "evidence_scope": "dealer_social_profile",
                "value_status": "observed",
            },
        },
        "viltrox_product_evidence": {
            "status": "public_listing_observed",
            "source_url": "https://dealer.example/brands/viltrox",
            "checked_at": "2026-07-13T18:00:00Z",
            "reviewer_id": "staff_7",
            "evidence_scope": "dealer_viltrox_product_page",
            "value_status": "observed",
        },
    }
    row.update(overrides)
    return row


def _valid_event_catalog(
    *,
    lane: str = "major_expo",
    dealer_stable_location_key: str = "",
) -> dict:
    checked_at = "2026-07-13T18:00:00Z"
    source_kind = "dealer_event" if lane in {"dealer_event", "local_activity"} else "major_expo"
    opportunity = {
        "id": "opp_example_20260720",
        "canonical_key": "example|2026-07-20|new-york|example",
        "source_id": "event_source_example",
        "external_event_key": "example-2026-07-20",
        "lane": lane,
        "title": "Example Camera Event",
        "start_date": "2026-07-20",
        "end_date": "2026-07-20",
        "timezone": "America/New_York",
        "country_code": "US",
        "official_url": "https://events.example/example-2026-07-20",
        "event_status": "scheduled",
        "verification_status": "verified",
        "source_checked_at": checked_at,
        "reviewer_id": "staff_7",
        "evidence_scope": "event_official_listing",
        "value_status": "observed",
        "viltrox_presence_status": "unknown",
    }
    if dealer_stable_location_key:
        opportunity["dealer_stable_location_key"] = dealer_stable_location_key
    return {
        "global_complete": False,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "checked_at": checked_at,
        "sources": [
            {
                "id": "event_source_example",
                "source_kind": source_kind,
                "canonical_url": "https://events.example/",
                "country_code": "US",
                "timezone": "America/New_York",
                "status": "active",
                "enabled": True,
                "source_checked_at": checked_at,
                "reviewer_id": "staff_7",
                "evidence_scope": "event_source_listing",
                "value_status": "observed",
            }
        ],
        "opportunities": [opportunity],
    }


def _codes(report: dict) -> set[str]:
    return {str(item.get("code")) for item in report.get("issues", [])}


def test_complete_dealer_contract_is_importable_but_remains_descriptive_only():
    row = _valid_dealer()
    report = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=_manifest("dealer_locations", [row]),
    )

    assert report["ok"] is True
    assert report["claim_status"] == "descriptive_only"
    assert report["import_gate"] == {
        "allowed": True,
        "reason": "quality_contract_passed",
        "does_not_prove_global_coverage": True,
    }
    assert report["coverage"]["stable_identity"]["rate"] == 1.0
    assert report["coverage"]["source_evidence"]["rate"] == 1.0
    assert report["coverage"]["viltrox_product_page_evidence"]["rate"] == 1.0
    assert report["coverage"]["contact_fields"] == {
        "covered": 1,
        "denominator": 4,
        "rate": 0.25,
        "status": "measured",
        "reason": "",
    }
    # Instagram is explicitly unknown and therefore does not inflate coverage.
    assert report["coverage"]["social_profiles"] == {
        "covered": 1,
        "denominator": 5,
        "rate": 0.2,
        "status": "measured",
        "reason": "",
    }
    assert report["claim_boundaries"]["product_page_proves_authorization"] is False
    assert report["claim_boundaries"]["product_page_proves_current_inventory"] is False


def test_missing_denominator_never_becomes_a_global_coverage_percentage():
    report = radar_quality.audit_dealer_candidates([_valid_dealer()], as_of=AS_OF)

    global_coverage = report["coverage"]["global_location_coverage"]
    assert global_coverage["covered"] == 1
    assert global_coverage["denominator"] is None
    assert global_coverage["rate"] is None
    assert global_coverage["status"] == "unavailable"
    assert report["claim_boundaries"]["global_full_coverage_claim_allowed"] is False


def test_arbitrary_well_formed_manifest_hashes_are_rejected_without_global_rate():
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    manifest["entity_ids_sha256"] = "a" * 64
    manifest["source_inventory_sha256"] = "b" * 64

    report = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )

    coverage = report["coverage"]["global_location_coverage"]
    assert coverage["manifest_status"] == "invalid"
    assert coverage["denominator"] is None
    assert coverage["rate"] is None
    assert coverage["manifest"] is None
    assert report["claim_status"] == "descriptive_only"


def test_manifest_canonical_order_is_stable_and_output_redacts_raw_inventories():
    catalog = radar.load_reviewed_catalog()
    manifest = _manifest("event_sources", catalog["sources"])
    reordered = deepcopy(manifest)
    reordered["entity_ids"].reverse()
    reordered["source_inventory"].reverse()

    first = radar_quality.audit_event_catalog(
        catalog,
        as_of=AS_OF,
        known_source_universe_denominator=manifest,
    )["coverage"]["global_source_coverage"]
    second = radar_quality.audit_event_catalog(
        catalog,
        as_of=AS_OF,
        known_source_universe_denominator=reordered,
    )["coverage"]["global_source_coverage"]

    assert first["manifest_status"] == second["manifest_status"] == "accepted"
    assert first["manifest"] == second["manifest"]
    assert set(first["manifest"]) == {
        "manifest_version",
        "reviewer_id",
        "entity_count",
        "source_count",
        "entity_ids_sha256",
        "source_inventory_sha256",
    }
    assert first["manifest"]["entity_count"] == len(catalog["sources"])


def test_observed_source_change_rejects_previously_matching_manifest():
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    changed = deepcopy(row)
    changed["location_source_url"] = "https://dealer.example/stores/relocated"

    coverage = radar_quality.audit_dealer_candidates(
        [changed],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]

    assert coverage["manifest_status"] == "invalid"
    assert coverage["rate"] is None
    assert coverage["denominator"] is None


def test_manifest_inventory_change_with_old_hashes_is_rejected():
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    manifest["entity_ids"].append("dealer_loc_aaaaaaaa")
    manifest["source_inventory"].append(
        {
            "source_id": "dealer_source_extra_location",
            "canonical_url": "https://dealer.example/stores/extra",
        }
    )
    manifest["denominator"] = 2

    coverage = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]

    assert coverage["manifest_status"] == "invalid"
    assert coverage["rate"] is None


def test_manifest_denominator_must_equal_complete_entity_inventory_count():
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    manifest["denominator"] = 2

    coverage = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]

    assert coverage["manifest_status"] == "invalid"
    assert coverage["denominator"] is None
    assert coverage["rate"] is None


@pytest.mark.parametrize("duplicate_kind", ["entity_id", "source_id", "source_url"])
def test_duplicate_manifest_entities_or_sources_are_rejected(duplicate_kind: str):
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    extra_entity = "dealer_loc_aaaaaaaa"
    extra_source = {
        "source_id": "dealer_source_extra_location",
        "canonical_url": "https://dealer.example/stores/extra",
    }
    if duplicate_kind == "entity_id":
        manifest["entity_ids"].append(manifest["entity_ids"][0])
    else:
        manifest["entity_ids"].append(extra_entity)
    if duplicate_kind == "source_id":
        extra_source["source_id"] = manifest["source_inventory"][0]["source_id"]
    if duplicate_kind == "source_url":
        extra_source["canonical_url"] = manifest["source_inventory"][0]["canonical_url"]
    manifest["source_inventory"].append(extra_source)
    manifest["denominator"] = len(manifest["entity_ids"])
    manifest["entity_ids_sha256"] = _digest(sorted(manifest["entity_ids"]))
    canonical_sources = sorted(
        manifest["source_inventory"],
        key=lambda item: (item["source_id"], item["canonical_url"]),
    )
    manifest["source_inventory_sha256"] = _digest(canonical_sources)

    coverage = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]

    assert coverage["manifest_status"] == "invalid"
    assert coverage["rate"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: manifest.pop("source_inventory"),
        lambda manifest: manifest["source_inventory"][0].update(canonical_url=""),
        lambda manifest: manifest["source_inventory"][0].update(
            canonical_url="https://dealer.example/stores/midtown#volatile"
        ),
    ],
)
def test_missing_empty_or_unstable_source_inventory_is_rejected(mutation):
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    mutation(manifest)

    coverage = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]

    assert coverage["manifest_status"] == "invalid"
    assert coverage["rate"] is None


def test_matching_dealer_manifest_accepts_only_quality_validated_unique_entities():
    row = _valid_dealer()
    manifest = _manifest("dealer_locations", [row])
    accepted = radar_quality.audit_dealer_candidates(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]
    assert accepted["manifest_status"] == "accepted"
    assert accepted["covered"] == 1
    assert accepted["rate"] == 1.0

    unqualified = deepcopy(row)
    unqualified["viltrox_product_evidence"]["status"] = "unknown"
    uncovered = radar_quality.audit_dealer_candidates(
        [unqualified],
        as_of=AS_OF,
        known_location_universe_denominator=manifest,
    )["coverage"]["global_location_coverage"]
    assert uncovered["manifest_status"] == "accepted"
    assert uncovered["covered"] == 0
    assert uncovered["rate"] == 0.0


def test_duplicate_dealer_rows_do_not_inflate_global_coverage_numerator():
    row = _valid_dealer()
    report = radar_quality.audit_dealer_candidates(
        [row, deepcopy(row)],
        as_of=AS_OF,
    )

    assert report["coverage"]["global_location_coverage"]["covered"] == 1
    assert "dealer.stable_location_key_duplicate" in _codes(report)

    bound = radar_quality.audit_dealer_candidates(
        [row, deepcopy(row)],
        as_of=AS_OF,
        known_location_universe_denominator=_manifest("dealer_locations", [row]),
    )["coverage"]["global_location_coverage"]
    assert bound["manifest_status"] == "invalid"
    assert bound["rate"] is None


def test_invalid_event_source_identity_never_enters_global_coverage_numerator():
    catalog = {
        "global_complete": False,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "checked_at": "2026-07-13T18:00:00Z",
        "sources": [
            {
                "id": "!",
                "canonical_url": "http://events.example/listing",
                "status": "active",
                "enabled": True,
                "source_checked_at": "2026-07-13T18:00:00Z",
                "reviewer_id": "staff_7",
                "evidence_scope": "event_source_listing",
                "value_status": "observed",
            }
        ],
        "opportunities": [],
    }

    report = radar_quality.audit_event_catalog(catalog, as_of=AS_OF)

    assert report["coverage"]["global_source_coverage"]["covered"] == 0
    assert {"event.source_id_missing_or_invalid", "event.source_url_invalid"} <= _codes(
        report
    )


def test_identity_collision_and_unsupported_business_claim_fail_closed():
    first = _valid_dealer(in_stock=True)
    second = deepcopy(first)
    second.update(
        organization_name="Other Camera",
        name="Other Camera · Downtown",
        address="2 Main St",
    )

    report = radar_quality.audit_dealer_candidates([first, second], as_of=AS_OF)

    assert report["ok"] is False
    assert report["import_gate"]["allowed"] is False
    assert {
        "dealer.stable_org_key_mismatch",
        "dealer.stable_location_key_mismatch",
        "dealer.stable_location_key_duplicate",
        "dealer.unsupported_business_claim",
    } <= _codes(report)
    assert report["deduplication"]["mode"] == "exact_keys_only_no_fuzzy_auto_merge"


def test_current_catalog_counts_unknown_viltrox_and_name_hints_as_uncovered():
    report = radar_quality.audit_event_catalog(radar.load_reviewed_catalog(), as_of=AS_OF)

    assert report["ok"] is False
    assert report["claim_status"] == "descriptive_only"
    assert report["import_gate"]["allowed"] is False
    assert report["coverage"]["source_row_freshness"]["rate"] == 0.0
    assert report["coverage"]["activity_url_presence"]["rate"] == 1.0
    assert report["coverage"]["activity_evidence"]["rate"] == 0.0
    assert report["coverage"]["viltrox_presence_evidence"] == {
        "covered": 0,
        "denominator": 24,
        "rate": 0.0,
        "status": "measured",
        "reason": "",
    }
    assert report["coverage"]["exact_dealer_location_linkage"] == {
        "covered": 0,
        "denominator": 11,
        "rate": None,
        "status": "unavailable",
        "reason": "reviewed_dealer_universe_not_supplied",
    }
    assert report["counts"]["name_only_dealer_hints"] == 9
    assert report["coverage"]["global_source_coverage"]["rate"] is None
    assert report["claim_boundaries"]["unknown_viltrox_presence_counted_as_covered"] is False
    assert "event.source_freshness_incomplete" in _codes(report)


def test_runtime_event_lane_and_source_status_contract_fails_closed():
    accepted = radar_quality.audit_event_catalog(_valid_event_catalog(), as_of=AS_OF)
    assert accepted["import_gate"]["allowed"] is True
    assert accepted["coverage"]["activity_evidence"]["rate"] == 1.0

    bad_lane = _valid_event_catalog()
    bad_lane["opportunities"][0]["lane"] = "unknown_lane"
    rejected_lane = radar_quality.audit_event_catalog(bad_lane, as_of=AS_OF)
    assert rejected_lane["import_gate"]["allowed"] is False
    assert rejected_lane["coverage"]["activity_evidence"]["rate"] == 0.0
    assert "event.lane_invalid" in _codes(rejected_lane)

    mismatched_source = _valid_event_catalog()
    mismatched_source["sources"][0]["source_kind"] = "dealer_event"
    rejected_source = radar_quality.audit_event_catalog(mismatched_source, as_of=AS_OF)
    assert rejected_source["import_gate"]["allowed"] is False
    assert rejected_source["coverage"]["activity_evidence"]["rate"] == 0.0
    assert "event.lane_source_kind_mismatch" in _codes(rejected_source)

    disabled_source = _valid_event_catalog()
    disabled_source["sources"][0]["enabled"] = False
    rejected_disabled = radar_quality.audit_event_catalog(disabled_source, as_of=AS_OF)
    assert rejected_disabled["import_gate"]["allowed"] is False
    assert rejected_disabled["coverage"]["activity_evidence"]["rate"] == 0.0
    assert {
        "event.active_source_disabled",
        "event.nonactive_source_opportunity",
    } <= _codes(rejected_disabled)

    unknown_status = _valid_event_catalog()
    unknown_status["sources"][0]["status"] = "paused"
    rejected_status = radar_quality.audit_event_catalog(unknown_status, as_of=AS_OF)
    assert rejected_status["import_gate"]["allowed"] is False
    assert rejected_status["coverage"]["reviewed_active_sources"]["covered"] == 0
    assert rejected_status["coverage"]["activity_evidence"]["rate"] == 0.0
    assert {
        "event.source_status_invalid",
        "event.nonactive_source_opportunity",
    } <= _codes(rejected_status)

    non_boolean_enabled = _valid_event_catalog()
    non_boolean_enabled["sources"][0]["enabled"] = "true"
    rejected_enabled = radar_quality.audit_event_catalog(non_boolean_enabled, as_of=AS_OF)
    assert rejected_enabled["import_gate"]["allowed"] is False
    assert rejected_enabled["coverage"]["reviewed_active_sources"]["covered"] == 0
    assert "event.source_enabled_invalid" in _codes(rejected_enabled)


def test_production_event_import_rejects_invalid_geography_timezone_and_dates_before_db(
    monkeypatch,
):
    catalog = _valid_event_catalog()
    catalog["sources"][0]["country_code"] = "USA"
    catalog["sources"][0]["timezone"] = "Mars/Olympus"
    catalog["opportunities"][0].update(
        country_code="USA",
        timezone="Mars/Olympus",
        start_date="2026-99-99",
        end_date="2026-99-99",
    )

    report = radar_quality.audit_event_catalog(catalog, as_of=AS_OF)
    assert report["import_gate"]["allowed"] is False
    assert {
        "event.source_country_invalid",
        "event.source_timezone_invalid",
        "event.activity_country_invalid",
        "event.activity_timezone_invalid",
        "event.activity_start_date_invalid",
        "event.activity_end_date_invalid",
        "event.activity_verified_dates_required",
    } <= _codes(report)

    calls = {"get_conn": 0, "table_exists": 0}

    def forbidden_get_conn(*_args, **_kwargs):
        calls["get_conn"] += 1
        raise AssertionError("invalid Event catalog reached the database")

    def forbidden_table_exists(*_args, **_kwargs):
        calls["table_exists"] += 1
        raise AssertionError("invalid Event catalog probed schema")

    monkeypatch.setattr(radar, "load_reviewed_catalog", lambda: deepcopy(catalog))
    monkeypatch.setattr(radar, "get_conn", forbidden_get_conn)
    monkeypatch.setattr(radar, "table_exists", forbidden_table_exists)

    preview = radar.preview_reviewed_catalog()
    assert preview["ok"] is True  # structurally renderable, but not importable
    assert preview["import_allowed"] is False
    assert preview["quality_status"] == "blocked_for_import"
    with pytest.raises(ValueError, match="event radar catalog validation failed"):
        radar.import_reviewed_catalog(record_only=False, organization_id=1)
    assert calls == {"get_conn": 0, "table_exists": 0}


def test_event_activity_geography_must_match_its_registered_source():
    catalog = _valid_event_catalog()
    catalog["opportunities"][0].update(
        country_code="CA",
        timezone="America/Toronto",
    )

    report = radar_quality.audit_event_catalog(catalog, as_of=AS_OF)

    assert report["import_gate"]["allowed"] is False
    assert {
        "event.activity_source_country_mismatch",
        "event.activity_source_timezone_mismatch",
    } <= _codes(report)


def test_dealer_links_are_lane_scoped_and_require_valid_location_keys():
    invalid_key = _valid_event_catalog(
        lane="local_activity",
        dealer_stable_location_key="not-a-dealer-key",
    )
    invalid_report = radar_quality.audit_event_catalog(invalid_key, as_of=AS_OF)
    assert invalid_report["import_gate"]["allowed"] is False
    assert invalid_report["coverage"]["exact_dealer_location_linkage"]["covered"] == 0
    assert "event.dealer_location_key_invalid" in _codes(invalid_report)

    wrong_lane = _valid_event_catalog(
        lane="major_expo",
        dealer_stable_location_key="dealer_loc_aaaaaaaa",
    )
    wrong_lane_report = radar_quality.audit_event_catalog(wrong_lane, as_of=AS_OF)
    assert wrong_lane_report["import_gate"]["allowed"] is False
    assert "event.dealer_link_wrong_lane" in _codes(wrong_lane_report)


def test_exact_dealer_linkage_requires_resolution_against_reviewed_universe():
    key = "dealer_loc_aaaaaaaa"
    catalog = _valid_event_catalog(
        lane="local_activity",
        dealer_stable_location_key=key,
    )

    declared = radar_quality.audit_event_catalog(catalog, as_of=AS_OF)
    assert declared["coverage"]["exact_dealer_location_linkage"]["rate"] is None
    assert declared["counts"]["declared_dealer_location_keys"] == 1
    assert declared["counts"]["exact_dealer_location_links"] == 0
    assert declared["evidence_records"]["opportunities"][0]["exact_stable_location_key"] is None
    assert declared["evidence_records"]["opportunities"][0]["association_status"] == (
        "declared_exact_key_unresolved"
    )

    resolved = radar_quality.audit_event_catalog(
        catalog,
        as_of=AS_OF,
        reviewed_dealer_location_keys={key},
    )
    assert resolved["coverage"]["exact_dealer_location_linkage"]["rate"] == 1.0
    assert resolved["counts"]["exact_dealer_location_links"] == 1
    assert resolved["evidence_records"]["opportunities"][0]["exact_stable_location_key"] == key
    assert resolved["evidence_records"]["opportunities"][0]["association_status"] == (
        "exact_reviewed_location"
    )

    unresolved = radar_quality.audit_event_catalog(
        catalog,
        as_of=AS_OF,
        reviewed_dealer_location_keys={"dealer_loc_bbbbbbbb"},
    )
    assert unresolved["import_gate"]["allowed"] is False
    assert "event.dealer_location_key_unresolved" in _codes(unresolved)


def test_combined_gate_requires_exact_dealer_location_resolution():
    dealer = _valid_dealer()
    location_key = str(dealer["stable_location_key"])
    resolved = radar_quality.build_event_dealer_quality_audit(
        _valid_event_catalog(
            lane="local_activity",
            dealer_stable_location_key=location_key,
        ),
        [dealer],
        as_of=AS_OF,
    )
    assert resolved["import_gates"]["event_catalog"]["allowed"] is True
    assert resolved["import_gates"]["dealer_candidates"]["allowed"] is True
    assert resolved["import_gates"]["combined_ready"] is True
    assert resolved["dealer_event_linkage"]["ready"] is True
    assert resolved["dealer_event_linkage"]["coverage"]["rate"] == 1.0

    missing = radar_quality.build_event_dealer_quality_audit(
        _valid_event_catalog(lane="local_activity"),
        [dealer],
        as_of=AS_OF,
    )
    assert missing["import_gates"]["combined_ready"] is False
    assert missing["dealer_event_linkage"]["ready"] is False
    assert "event.dealer_location_key_missing" in _codes(missing)

    unresolved = radar_quality.build_event_dealer_quality_audit(
        _valid_event_catalog(
            lane="local_activity",
            dealer_stable_location_key="dealer_loc_aaaaaaaa",
        ),
        [dealer],
        as_of=AS_OF,
    )
    assert unresolved["import_gates"]["combined_ready"] is False
    assert unresolved["dealer_event_linkage"]["exact_location_keys_resolved"] == 0
    assert "event.dealer_location_key_unresolved" in _codes(unresolved)


def test_event_duplicate_positive_claim_and_bad_denominator_block_import():
    catalog = radar.load_reviewed_catalog()
    catalog["opportunities"].append(deepcopy(catalog["opportunities"][0]))
    catalog["opportunities"][0]["viltrox_presence_status"] = "confirmed_exhibitor"
    catalog["opportunities"][0]["viltrox_evidence_url"] = ""
    catalog["opportunities"][0]["roi"] = 3.2

    report = radar_quality.audit_event_catalog(
        catalog,
        as_of=AS_OF,
        known_source_universe_denominator=-1,
    )

    assert report["ok"] is False
    assert report["import_gate"]["allowed"] is False
    assert {
        "event.opportunity_id_duplicate",
        "event.canonical_key_duplicate",
        "event.external_key_duplicate",
        "event.viltrox_presence_without_evidence",
        "event.unsupported_business_claim",
        "event.global_source_coverage.manifest_required",
    } <= _codes(report)


def test_current_combined_runtime_audit_is_offline_and_blocks_unqualified_dealers():
    report = radar.quality_audit(as_of=AS_OF)

    assert report["contract"] == {
        "id": "vkpi.event_dealer.quality",
        "version": 2,
        "generated_at": "2026-07-13T20:00:00+00:00",
        "read_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
    }
    assert report["claim_status"] == "descriptive_only"
    assert report["coverage_truth"] == {
        "event_source_universe_manifest_status": "unavailable",
        "dealer_location_universe_manifest_status": "unavailable",
        "known_event_source_universe_denominator": None,
        "known_dealer_location_universe_denominator": None,
        "global_event_source_coverage_rate": None,
        "global_dealer_location_coverage_rate": None,
        "global_full_coverage_claim_allowed": False,
        "unknown_counted_as_covered": False,
    }
    assert report["import_gates"]["event_catalog"]["allowed"] is False
    assert report["import_gates"]["dealer_candidates"]["allowed"] is False
    assert report["import_gates"]["combined_ready"] is False
    assert report["dealer_event_linkage"]["coverage"]["rate"] == 0.0
    assert all(
        proposal["proposed_source_id"].startswith("dealer_source_")
        and proposal["proposed_stable_org_key"].startswith("dealer_org_")
        and proposal["proposed_stable_location_key"].startswith("dealer_loc_")
        and proposal["proposal_status"] == "candidate_only_not_persisted"
        for proposal in report["dealer"]["identity_proposals"]
    )
    assert {
        "event.source_freshness_incomplete",
        "dealer.source_id_missing_or_invalid",
        "dealer.source_freshness_unavailable",
        "dealer.stable_org_key_missing_or_invalid",
        "dealer.stable_location_key_missing_or_invalid",
    } <= _codes(report)


def test_event_persistent_import_requires_per_source_freshness_before_db(monkeypatch):
    calls = {"get_conn": 0, "table_exists": 0}

    def forbidden_get_conn(*_args, **_kwargs):
        calls["get_conn"] += 1
        raise AssertionError("freshness-blocked Event import reached the database")

    def forbidden_table_exists(*_args, **_kwargs):
        calls["table_exists"] += 1
        raise AssertionError("freshness-blocked Event import probed schema")

    monkeypatch.setattr(radar, "get_conn", forbidden_get_conn)
    monkeypatch.setattr(radar, "table_exists", forbidden_table_exists)

    # 2026-07-17 目录重核后打包目录已可导入;此测验证的是「时效不全→绝不触库」的
    # 闸机制本身,改用做旧副本(剥掉一个来源的 source_checked_at)保持前提成立。
    stale = radar.load_reviewed_catalog()
    stale["sources"][0].pop("source_checked_at", None)
    monkeypatch.setattr(radar, "load_reviewed_catalog", lambda: stale)

    preview = radar.import_reviewed_catalog(record_only=True)
    assert preview["ok"] is True  # still renderable as a descriptive preview
    assert preview["import_allowed"] is False
    assert preview["quality_status"] == "blocked_for_import"
    assert preview["claim_status"] == "descriptive_only"
    assert preview["quality_contract"]["import_gate"]["allowed"] is False
    with pytest.raises(ValueError, match="event radar catalog validation failed"):
        radar.import_reviewed_catalog(record_only=False, organization_id=1)

    assert calls == {"get_conn": 0, "table_exists": 0}


def test_dealer_persistent_import_stops_before_any_database_call(monkeypatch):
    calls = {"upsert": 0, "audit": 0}

    def forbidden_upsert(*_args, **_kwargs):
        calls["upsert"] += 1
        raise AssertionError("quality-blocked import reached upsert")

    def forbidden_audit(*_args, **_kwargs):
        calls["audit"] += 1
        raise AssertionError("quality-blocked import wrote scrape audit")

    monkeypatch.setattr(dealer_scrape, "upsert_dealer", forbidden_upsert)
    monkeypatch.setattr(dealer_scrape, "_record_scrape_audit", forbidden_audit)

    preview = dealer_scrape.scrape_dealers_enqueue(record_only=True, limit=5)
    assert preview["ok"] is True
    assert preview["import_allowed"] is False
    assert preview["quality_status"] == "blocked_for_import"
    assert preview["claim_status"] == "descriptive_only"
    with pytest.raises(ValueError, match="dealer import blocked by quality contract"):
        dealer_scrape.scrape_dealers_enqueue(record_only=False, limit=5)

    assert calls == {"upsert": 0, "audit": 0}


def test_quality_routes_delegate_to_read_only_domain_contract(monkeypatch):
    combined = {"claim_status": "descriptive_only", "read_only": True}
    dealer = {"claim_status": "descriptive_only", "read_only": True}
    monkeypatch.setattr(vkpi_event_radar.radar, "quality_audit", lambda: combined)
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "reviewed_candidates_quality_audit",
        lambda: dealer,
    )

    assert vkpi_event_radar.event_radar_quality_audit(staff={"id": 1}) is combined
    assert vkpi_dealers.dealer_quality_audit_route(staff={"id": 1}) is dealer
