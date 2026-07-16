from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

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

    def digest(value) -> str:
        canonical = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    return {
        "manifest_version": 1,
        "scope": scope,
        "denominator": len(entity_ids),
        "entity_ids": entity_ids,
        "source_inventory": source_inventory,
        "entity_ids_sha256": digest(entity_ids),
        "source_inventory_sha256": digest(source_inventory),
        "as_of": "2026-07-13T18:00:00Z",
        "methodology": "Hermetic exact-id fixture inventory.",
        "reviewer_id": "staff_7",
    }


def _tasks(queue: dict, *, scope: str | None = None, field: str | None = None) -> list[dict]:
    return [
        task
        for task in queue["tasks"]
        if (scope is None or task["scope"] == scope)
        and (field is None or task["field"] == field)
    ]


def _fully_evidenced_dealer() -> dict:
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
    checked_at = "2026-07-13T18:00:00Z"

    def evidence(url: str, scope: str) -> dict:
        return {
            "status": "verified",
            "source_url": url,
            "checked_at": checked_at,
            "reviewer_id": "staff_7",
            "evidence_scope": scope,
            "value_status": "observed",
        }

    location_url = "https://dealer.example/stores/midtown"
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
        "location_source_url": location_url,
        "brand_listing_url": "https://dealer.example/brands/viltrox",
        "source_checked_at": checked_at,
        "source_status": "public_listing_verified",
        "reviewer_id": "staff_7",
        "evidence_scope": "dealer_location_listing",
        "value_status": "observed",
        "authorization_status": "needs_viltrox_confirmation",
        "phone": "+1 212 555 0100",
        "contact_email": "store@dealer.example",
        "store_hours": "Mon-Fri 09:00-18:00",
        "public_services": "camera retail",
        "contact_evidence": {
            field: evidence(location_url, "dealer_contact_field")
            for field in ("phone", "contact_email", "store_hours", "public_services")
        },
        "social_evidence": {
            platform: evidence(f"https://social.example/{platform}/example-camera", "dealer_social_profile")
            for platform in ("instagram", "facebook", "youtube", "tiktok", "x")
        },
        "viltrox_product_evidence": {
            **evidence("https://dealer.example/brands/viltrox", "dealer_viltrox_product_page"),
            "status": "public_listing_observed",
        },
        "activity_evidence": {
            **evidence("https://dealer.example/events", "dealer_activity_page"),
        },
    }
    return row


def test_current_event_queue_expands_every_known_gap_at_entity_grain():
    queue = radar_quality.build_event_remediation_queue(
        radar.load_reviewed_catalog(),
        as_of=AS_OF,
    )

    assert queue["queue"] == {
        "id": "vkpi.event_dealer.remediation",
        "version": 1,
        "scope": "event",
        "generated_at": "2026-07-13T20:00:00+00:00",
        "read_only": True,
        "preview_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
    }
    assert queue["task_count"] == 136
    assert len(_tasks(queue, field="source_checked_at")) == 72
    assert len(_tasks(queue, field="activity_evidence")) == 25
    assert len(_tasks(queue, field="viltrox_presence_evidence")) == 25
    assert len(_tasks(queue, field="dealer_stable_location_key")) == 12
    assert len(_tasks(queue, field="known_source_universe_denominator")) == 1
    assert queue["task_counts"]["blocking_import"] == 98
    assert queue["evidence_gaps"]["source_current_check"]["denominator"] == 72
    assert queue["evidence_gaps"]["activity_evidence"]["denominator"] == 25


def test_current_dealer_queue_expands_source_identity_product_and_optional_fields():
    queue = radar_quality.build_dealer_remediation_queue(
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    assert queue["task_count"] == 79
    assert len(_tasks(queue, field="source_id")) == 5
    assert len(_tasks(queue, field="source_checked_at")) == 5
    assert len(_tasks(queue, field="source_status")) == 5
    assert len(_tasks(queue, field="stable_location_key")) == 5
    assert len(_tasks(queue, field="viltrox_product_evidence")) == 5
    assert len([task for task in queue["tasks"] if task["field"].startswith("contact_evidence.")]) == 20
    assert len([task for task in queue["tasks"] if task["field"].startswith("social_evidence.")]) == 25
    assert len(_tasks(queue, field="activity_evidence")) == 5
    assert queue["task_counts"]["duplicate_occurrences_collapsed"] == 2
    assert queue["evidence_gaps"]["stable_identity"]["covered"] == 0
    assert queue["evidence_gaps"]["stable_identity"]["denominator"] == 5


def test_task_identity_is_stable_across_as_of_and_queue_is_exactly_deduplicated():
    catalog = radar.load_reviewed_catalog()
    candidates = dealer_scrape.reviewed_candidates()
    first = radar_quality.build_event_dealer_remediation_queue(
        catalog,
        candidates,
        as_of=AS_OF,
    )
    second = radar_quality.build_event_dealer_remediation_queue(
        deepcopy(catalog),
        deepcopy(candidates),
        as_of=AS_OF + timedelta(days=1),
    )

    assert [task["task_id"] for task in first["tasks"]] == [
        task["task_id"] for task in second["tasks"]
    ]
    assert len({task["task_id"] for task in first["tasks"]}) == first["task_count"]
    assert len({task["dedupe_key"] for task in first["tasks"]}) == first["task_count"]
    assert first["task_count"] == 215


def test_missing_global_denominators_emit_no_rate_property_or_coverage_claim():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    for descriptor in queue["universe_coverage"].values():
        assert descriptor["denominator"] is None
        assert descriptor["rate_available"] is False
        assert "rate" not in descriptor
    assert queue["claim_status"] == "descriptive_only"
    assert queue["persistence_policy"]["unreviewed_catalog_import_allowed"] is False

    invalid = radar_quality.build_event_remediation_queue(
        radar.load_reviewed_catalog(),
        as_of=AS_OF,
        known_source_universe_denominator=-1,
    )
    invalid_descriptor = invalid["universe_coverage"]["event_source_universe"]
    assert invalid_descriptor["status"] == "unavailable"
    assert invalid_descriptor["reason"] == "universe_manifest_required"
    assert "rate" not in invalid_descriptor
    assert len(_tasks(invalid, field="known_source_universe_denominator")) == 1


def test_accepted_denominator_enables_bounded_rate_but_never_global_full_claim():
    catalog = radar.load_reviewed_catalog()
    queue = radar_quality.build_event_remediation_queue(
        catalog,
        as_of=AS_OF,
        known_source_universe_denominator=_manifest("event_sources", catalog["sources"]),
    )

    descriptor = queue["universe_coverage"]["event_source_universe"]
    assert descriptor["denominator"] == 72
    assert descriptor["rate"] == 0.0
    assert descriptor["rate_available"] is True
    assert not _tasks(queue, field="known_source_universe_denominator")
    assert queue["claim_status"] == "descriptive_only"


def test_resolved_dealer_evidence_removes_all_tasks_without_mutating_input():
    row = _fully_evidenced_dealer()
    before = deepcopy(row)
    queue = radar_quality.build_dealer_remediation_queue(
        [row],
        as_of=AS_OF,
        known_location_universe_denominator=_manifest("dealer_locations", [row]),
    )

    assert queue["status"] == "clear"
    assert queue["task_count"] == 0
    assert row == before
    assert queue["universe_coverage"]["dealer_location_universe"]["rate"] == 1.0


def test_each_task_has_dispatch_and_fail_closed_review_contract():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    for task in queue["tasks"]:
        assert task["task_id"].startswith("remediation_")
        assert task["entity_type"]
        assert task["entity_id"]
        assert task["field"]
        assert task["severity"] in {"high", "medium"}
        assert task["required_evidence"]["fields"]
        assert task["required_evidence"]["unknown_counts_as_covered"] is False
        assert task["freshness"]["due_at"]
        assert task["manual_review_status"] == "pending"
        assert task["manual_review"]["state_persisted"] is False
        assert task["manual_review"]["acceptance_does_not_auto_persist_business_rows"] is True
        assert task["persistence_eligible"] is False


def test_runtime_queue_accessors_do_not_reach_database(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only remediation queue reached persistence")

    monkeypatch.setattr(radar, "get_conn", forbidden)
    monkeypatch.setattr(radar, "table_exists", forbidden)
    monkeypatch.setattr(dealer_scrape, "get_conn", forbidden)
    monkeypatch.setattr(dealer_scrape, "table_exists", forbidden)
    monkeypatch.setattr(dealer_scrape, "upsert_dealer", forbidden)

    combined = radar.remediation_queue(as_of=AS_OF)
    dealer = radar_quality.build_dealer_remediation_queue(
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    assert combined["queue"]["database_accessed"] is False
    assert combined["queue"]["business_rows_written"] == 0
    assert dealer["queue"]["database_accessed"] is False


def test_read_routes_delegate_to_preview_only_queues(monkeypatch):
    combined = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )
    dealer = radar_quality.build_dealer_remediation_queue(
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )
    monkeypatch.setattr(vkpi_event_radar.radar, "remediation_queue", lambda: combined)
    monkeypatch.setattr(
        vkpi_dealers.dealer_scrape,
        "reviewed_candidates_remediation_queue",
        lambda: dealer,
    )

    combined_view = vkpi_event_radar.event_radar_remediation_queue(
        scope="dealer",
        entity_type=None,
        field="activity_evidence",
        issue_code="dealer.activity_evidence_missing_or_stale",
        severity=None,
        freshness_status="unavailable",
        due_status="due_now_missing_evidence",
        blocks_import=False,
        offset=1,
        limit=2,
        staff={"id": 1},
    )
    dealer_view = vkpi_dealers.dealer_remediation_queue_route(
        entity_type=None,
        field="activity_evidence",
        issue_code="dealer.activity_evidence_missing_or_stale",
        severity=None,
        freshness_status="unavailable",
        due_status="due_now_missing_evidence",
        blocks_import=False,
        offset=1,
        limit=2,
        staff={"id": 1},
    )

    assert combined_view["task_count"] == 215
    assert combined_view["filtered_total"] == 5
    assert combined_view["returned"] == 2
    assert dealer_view["task_count"] == 79
    assert dealer_view["filtered_total"] == 5
    assert dealer_view["returned"] == 2
    assert all(task["field"] == "activity_evidence" for task in dealer_view["tasks"])


def test_query_projection_filters_activity_evidence_without_rewriting_global_truth():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )
    before = deepcopy(queue)

    view = radar_quality.query_remediation_queue(
        queue,
        scope="DEALER",
        field="Activity_Evidence",
        issue_code="dealer.activity_evidence_missing_or_stale",
        freshness_status="UNAVAILABLE",
        due_status="due_now_missing_evidence",
        blocks_import=False,
        offset=1,
        limit=2,
    )

    assert queue == before
    assert view["status"] == "action_required"
    assert view["task_count"] == 215
    assert view["task_count_total"] == 215
    assert view["unfiltered_total"] == 215
    assert view["filtered_total"] == 5
    assert view["returned"] == 2
    assert view["has_more"] is True
    assert all(task["scope"] == "dealer" for task in view["tasks"])
    assert all(task["field"] == "activity_evidence" for task in view["tasks"])
    assert all(task["freshness"]["status"] == "unavailable" for task in view["tasks"])
    assert all(task["blocks_import"] is False for task in view["tasks"])
    assert view["task_view"] == {
        "status": "page_returned",
        "filters": {
            "scope": "dealer",
            "field": "activity_evidence",
            "issue_code": "dealer.activity_evidence_missing_or_stale",
            "freshness_status": "unavailable",
            "due_status": "due_now_missing_evidence",
            "blocks_import": False,
        },
        "offset": 1,
        "limit": 2,
        "has_more": True,
        "next_offset": 3,
        "global_queue_status": "action_required",
        "empty_page_means_queue_clear": False,
        "global_task_count_preserved": True,
        "read_only": True,
    }


def test_query_projection_marks_scope_mismatch_without_claiming_queue_clear():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    view = radar_quality.query_remediation_queue(
        queue,
        scope="not-a-real-scope",
        offset=0,
        limit=10,
    )

    assert view["status"] == "action_required"
    assert view["task_count"] == 215
    assert view["task_counts"] == queue["task_counts"]
    assert view["filtered_total"] == 0
    assert view["returned"] == 0
    assert view["tasks"] == []
    assert view["task_view"]["status"] == "no_filter_matches"
    assert view["task_view"]["global_queue_status"] == "action_required"
    assert view["task_view"]["empty_page_means_queue_clear"] is False


def test_query_projection_distinguishes_offset_past_end_from_no_matches():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    view = radar_quality.query_remediation_queue(
        queue,
        scope="dealer",
        offset=999,
        limit=2,
    )

    assert view["status"] == "action_required"
    assert view["task_count"] == 215
    assert view["filtered_total"] == 79
    assert view["returned"] == 0
    assert view["has_more"] is False
    assert view["task_view"]["status"] == "offset_out_of_range"
    assert view["task_view"]["next_offset"] is None


def test_query_projection_reports_only_a_genuinely_empty_unfiltered_queue_as_clear():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )
    empty = deepcopy(queue)
    empty["status"] = "clear"
    empty["task_count"] = 0
    empty["tasks"] = []

    unfiltered = radar_quality.query_remediation_queue(empty)
    filtered = radar_quality.query_remediation_queue(empty, scope="dealer")

    assert unfiltered["task_view"]["status"] == "global_queue_clear"
    assert unfiltered["task_view"]["empty_page_means_queue_clear"] is True
    assert filtered["task_view"]["status"] == "no_filter_matches"
    assert filtered["task_view"]["empty_page_means_queue_clear"] is False


def test_query_projection_rejects_global_status_count_contradiction():
    queue = radar_quality.build_dealer_remediation_queue(
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )
    queue["status"] = "clear"

    with pytest.raises(ValueError, match="status must agree"):
        radar_quality.query_remediation_queue(queue)


def test_query_projection_preserves_boolean_filter_semantics():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    blocking = radar_quality.query_remediation_queue(queue, blocks_import=True, limit=500)
    nonblocking = radar_quality.query_remediation_queue(queue, blocks_import=False, limit=500)

    assert blocking["filtered_total"] == queue["task_counts"]["blocking_import"]
    assert nonblocking["filtered_total"] == queue["task_counts"]["nonblocking_quality"]
    assert all(task["blocks_import"] is True for task in blocking["tasks"])
    assert all(task["blocks_import"] is False for task in nonblocking["tasks"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"limit": 0}, "limit must be an integer in [1, 500]"),
        ({"limit": 501}, "limit must be an integer in [1, 500]"),
        ({"limit": True}, "limit must be an integer in [1, 500]"),
        ({"offset": -1}, "offset must be a non-negative integer"),
        ({"offset": True}, "offset must be a non-negative integer"),
        ({"blocks_import": "false"}, "blocks_import must be boolean when provided"),
    ],
)
def test_query_projection_rejects_invalid_pagination_and_boolean_values(kwargs, message):
    queue = radar_quality.build_dealer_remediation_queue(
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        radar_quality.query_remediation_queue(queue, **kwargs)


def test_queue_does_not_expand_or_override_ordinary_crud_scope():
    queue = radar_quality.build_event_dealer_remediation_queue(
        radar.load_reviewed_catalog(),
        dealer_scrape.reviewed_candidates(),
        as_of=AS_OF,
    )

    assert queue["ordinary_crud_scope"]["changed_by_this_queue"] is False
    assert "existing staff-authorized CRUD" in queue["ordinary_crud_scope"]["dealer_manual_crud"]
    assert "quality-gated separately" in queue["ordinary_crud_scope"]["batch_imports"]
    assert queue["persistence_policy"] == {
        "queue_tasks_are_business_rows": False,
        "queue_preview_can_write": False,
        "manual_review_state_is_persisted": False,
        "accepted_task_auto_imports_catalog": False,
        "unreviewed_catalog_import_allowed": False,
        "import_requires_separate_quality_gate": True,
    }


def test_new_or_nonstandard_quality_failures_cannot_disappear_from_queue():
    catalog = radar.load_reviewed_catalog()
    catalog["global_complete"] = True
    catalog["opportunities"].append(deepcopy(catalog["opportunities"][0]))
    catalog["opportunities"][0]["roi"] = 9.9

    queue = radar_quality.build_event_remediation_queue(catalog, as_of=AS_OF)
    generic = {
        task["issue_code"]: task
        for task in queue["tasks"]
        if task["entity_type"] == "event_quality_contract"
    }

    assert "event.global_complete" in generic
    assert "event.opportunity_id_duplicate" in generic
    assert "event.canonical_key_duplicate" in generic
    assert "event.external_key_duplicate" in generic
    assert "event.unsupported_business_claim" in generic
    assert all(task["blocks_import"] is True for task in generic.values())
