from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

from app.domains.events import radar


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/audit_vkpi_event_radar_catalog.py"
_SPEC = importlib.util.spec_from_file_location("audit_vkpi_event_dealer_coverage", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _codes(report: dict) -> set[str]:
    return {str(item["code"]) for item in report["issues"]}


def _reviewed_dealer(**overrides) -> dict:
    candidate = {
        "name": "Example Camera · Midtown",
        "address": "1 Main St",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "US",
        "brand_listing_url": "https://dealer.example/brands/viltrox",
        "location_source_url": "https://dealer.example/stores/midtown",
        "stable_org_key": "dealer_org_example",
        "stable_location_key": "dealer_loc_example_midtown",
        "source_checked_at": "2026-07-13T18:00:00Z",
        "authorization_status": "needs_viltrox_confirmation",
        "phone": "+1 212 555 0100",
        "contact_provenance": {
            "phone": "https://dealer.example/stores/midtown",
        },
        "aliases": [
            {
                "alias_type": "domain",
                "alias_value": "dealer.example",
                "alias_normalized": "dealer example",
                "country_code": "US",
            }
        ],
    }
    candidate.update(overrides)
    return candidate


def test_current_event_dealer_contract_reports_coverage_without_global_claims():
    catalog = radar.load_reviewed_catalog()
    dealers = _MODULE.load_reviewed_dealer_candidates()

    report = _MODULE.build_event_dealer_coverage_audit(
        catalog,
        dealers,
        as_of=datetime(2026, 7, 15, 16, tzinfo=timezone.utc),
    )

    assert report["ok"] is True
    assert report["quality_status"] == "partial"
    assert report["claim_status"] == "descriptive_only"
    assert report["contract"] == {
        "id": "vkpi.event_dealer.coverage_quality",
        "version": 1,
        "generated_at": "2026-07-15T16:00:00+00:00",
        "read_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
    }

    source_coverage = report["event_source_coverage"]
    assert source_coverage["observed_reviewed_sources"] == 72
    assert source_coverage["known_source_universe_denominator"] is None
    assert source_coverage["denominator_status"] == "denominator_unavailable"
    assert source_coverage["global_coverage_rate"] is None
    assert source_coverage["status_counts"] == {
        "active": 61,
        "hold": 8,
        "blocked": 1,
        "retired": 2,
        "unknown": 0,
    }
    assert source_coverage["reviewed_active_source_yield_rate"] == 0.2623
    assert source_coverage["yield_rate_scope"] == "reviewed_active_sources_only_not_global_coverage"

    geography = report["geographic_coverage"]
    assert geography["source_country_count"] == 18
    assert geography["source_region_count"] == 7
    assert geography["global_country_universe_denominator"] is None
    assert geography["denominator_status"] == "denominator_unavailable"
    assert geography["global_country_coverage_rate"] is None

    assert report["freshness"]["event_catalog_snapshot"]["status"] == "fresh"
    assert report["freshness"]["event_sources"] == {
        "rows": 72,
        "rows_with_source_checked_at": 72,
        "coverage_rate": 1.0,
        "status": "available",
    }

    linkage = report["dealer_event_identity_linkage"]
    assert linkage["dealer_local_opportunities"] == 11
    assert linkage["opportunities_with_name_hint"] == 9
    assert linkage["name_hint_rate"] == 0.8182
    assert linkage["name_hints_resolved_to_one_candidate"] == 9
    assert linkage["candidate_name_resolution_rate"] == 1.0
    assert linkage["reviewed_location_identity_links"] == 0
    assert linkage["reviewed_location_identity_rate"] == 0.0
    assert linkage["ambiguous_name_hints"] == 0
    assert {row["status"] for row in linkage["rows"]} == {
        "missing_name_hint",
        "derived_candidate_name_match",
    }

    dealer = report["dealer_quality"]
    assert dealer["counts"] == {
        "candidate_locations": 5,
        "derived_candidate_organizations": 3,
        "countries": 1,
    }
    assert dealer["identity"]["derived_grouping_status"] == "candidate_only_not_reviewed_merge"
    assert dealer["identity"]["explicit_stable_org_keys"] == 0
    assert dealer["identity"]["explicit_stable_location_keys"] == 0
    assert dealer["identity"]["explicit_alias_records"] == 0
    assert dealer["identity"]["alias_completeness_denominator_status"] == "denominator_unavailable"
    assert dealer["contact_provenance"]["fields"]["phone"]["present"] == 5
    assert dealer["contact_provenance"]["fields"]["phone"]["provenance_rate"] == 0.0
    assert dealer["viltrox_product_page_presence"]["structural_url_rate"] == 1.0
    assert dealer["viltrox_product_page_presence"]["remote_pages_verified_in_this_audit"] == 0
    assert dealer["viltrox_product_page_presence"]["remote_presence_status"] == "not_checked_offline"

    assert report["claim_boundaries"] == {
        "global_full_coverage_claim_allowed": False,
        "public_listing_proves_viltrox_product_page": False,
        "public_listing_proves_authorization": False,
        "public_listing_proves_inventory_or_stock": False,
        "public_listing_proves_sales_or_attribution": False,
        "public_listing_proves_local_impact": False,
        "remote_page_presence_not_verified_offline": True,
    }
    assert {
        "dealer.stable_org_identity_incomplete",
        "dealer.stable_location_identity_incomplete",
        "dealer.alias_review_missing",
        "dealer.contact_provenance_incomplete",
        "dealer.freshness_unavailable",
    } <= _codes(report)
    json.dumps(report, ensure_ascii=False, sort_keys=True)


def test_snapshot_freshness_becomes_stale_without_changing_coverage_claims():
    report = _MODULE.build_event_dealer_coverage_audit(
        radar.load_reviewed_catalog(),
        [],
        as_of=datetime(2026, 8, 20, tzinfo=timezone.utc),
        stale_after_days=30,
    )

    freshness = report["freshness"]["event_catalog_snapshot"]
    assert freshness["status"] == "stale"
    assert freshness["age_days"] > 30
    assert report["event_source_coverage"]["denominator_status"] == "denominator_unavailable"
    assert report["claim_status"] == "descriptive_only"


def test_reviewed_dealer_identity_contact_and_alias_contract_can_be_complete():
    report = _MODULE.audit_dealer_candidates([_reviewed_dealer()])

    assert report["ok"] is True
    assert report["issue_counts"] == {"errors": 0, "warnings": 0}
    assert report["identity"]["stable_org_key_coverage_rate"] == 1.0
    assert report["identity"]["stable_location_key_coverage_rate"] == 1.0
    assert report["identity"]["orgs_with_alias_rate"] == 1.0
    assert report["identity"]["alias_completeness_denominator_status"] == "denominator_unavailable"
    assert report["contact_provenance"]["fields"]["phone"]["provenance_rate"] == 1.0
    assert report["freshness"]["coverage_rate"] == 1.0
    assert report["viltrox_product_page_presence"]["remote_presence_status"] == "not_checked_offline"


def test_dealer_gate_rejects_identity_collisions_and_positive_business_claims():
    first = _reviewed_dealer(in_stock=True, authorization_status="authorized")
    second = deepcopy(first)
    second.update(
        name="Other Camera · Downtown",
        address="2 Main St",
        stable_org_key="dealer_org_other",
        stable_location_key="dealer_loc_example_midtown",
    )

    report = _MODULE.audit_dealer_candidates([first, second])

    assert report["ok"] is False
    assert {
        "dealer.location_identity_duplicate",
        "dealer.alias_conflict",
        "dealer.unsupported_authorization_claim",
        "dealer.unsupported_business_claim",
    } <= _codes(report)


def test_dealer_source_is_literal_parsed_without_importing_application(tmp_path):
    source = tmp_path / "dealer_source.py"
    source.write_text(
        "raise RuntimeError('must not import')\n"
        "_REVIEWED_PUBLIC_RETAILERS = "
        + repr([_reviewed_dealer()])
        + "\n",
        encoding="utf-8",
    )

    loaded = _MODULE.load_reviewed_dealer_candidates(source)

    assert loaded == [_reviewed_dealer()]
