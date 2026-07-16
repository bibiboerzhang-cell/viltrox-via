from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pathlib import Path

from app.domains.events import (
    radar_quality,
    radar_quality_audits,
    radar_quality_dealer_audit,
)


AS_OF = datetime(2026, 7, 13, 20, tzinfo=timezone.utc)
CHECKED_AT = "2026-07-13T18:00:00Z"
LOCAL_SOURCE_KINDS = (
    "venue_calendar",
    "school_calendar",
    "university_calendar",
    "photo_club",
    "community_calendar",
    "brand_event",
)


def test_quality_audit_split_stays_below_line_guard_and_preserves_reexport():
    assert radar_quality.audit_dealer_candidates is radar_quality_audits.audit_dealer_candidates
    assert (
        radar_quality_audits.audit_dealer_candidates
        is radar_quality_dealer_audit.audit_dealer_candidates
    )
    for module in (radar_quality_audits, radar_quality_dealer_audit):
        assert len(Path(module.__file__).read_text(encoding="utf-8").splitlines()) < 1000


def _catalog(source_kind: str, *, lane: str = "local_activity") -> dict:
    return {
        "global_complete": False,
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "checked_at": CHECKED_AT,
        "sources": [
            {
                "id": f"source_{source_kind}",
                "source_kind": source_kind,
                "canonical_url": f"https://events.example/{source_kind}",
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
        "opportunities": [
            {
                "id": f"opportunity_{source_kind}",
                "canonical_key": f"{source_kind}|2026-07-20|new-york",
                "source_id": f"source_{source_kind}",
                "external_event_key": f"{source_kind}-2026-07-20",
                "lane": lane,
                "title": "Camera Community Activity",
                "start_date": "2026-07-20",
                "end_date": "2026-07-20",
                "timezone": "America/New_York",
                "country_code": "US",
                "official_url": f"https://events.example/{source_kind}/2026-07-20",
                "event_status": "scheduled",
                "verification_status": "verified",
                "source_checked_at": CHECKED_AT,
                "reviewer_id": "staff_7",
                "evidence_scope": "event_official_listing",
                "value_status": "observed",
                "viltrox_presence_status": "unknown",
            }
        ],
    }


@pytest.mark.parametrize("source_kind", LOCAL_SOURCE_KINDS)
def test_supported_local_calendars_can_emit_local_activity_without_dealer_identity(source_kind: str):
    report = radar_quality.audit_event_catalog(_catalog(source_kind), as_of=AS_OF)

    assert report["import_gate"]["allowed"] is True
    assert report["counts"]["dealer_or_local_opportunities"] == 0
    assert report["coverage"]["exact_dealer_location_linkage"]["denominator"] == 0
    assert "event.lane_source_kind_mismatch" not in {
        str(issue.get("code")) for issue in report["issues"]
    }


def test_brand_event_is_a_source_kind_mapped_to_local_activity_not_a_lane():
    accepted = radar_quality.audit_event_catalog(
        _catalog("brand_event", lane="local_activity"),
        as_of=AS_OF,
    )
    rejected = radar_quality.audit_event_catalog(
        _catalog("brand_event", lane="brand_event"),
        as_of=AS_OF,
    )

    assert accepted["import_gate"]["allowed"] is True
    assert "event.lane_source_kind_mismatch" not in {
        str(issue.get("code")) for issue in accepted["issues"]
    }
    assert rejected["import_gate"]["allowed"] is False
    assert "event.lane_invalid" in {
        str(issue.get("code")) for issue in rejected["issues"]
    }


def test_non_dealer_local_source_cannot_emit_dealer_event():
    report = radar_quality.audit_event_catalog(
        _catalog("university_calendar", lane="dealer_event"),
        as_of=AS_OF,
    )

    assert report["import_gate"]["allowed"] is False
    assert "event.lane_source_kind_mismatch" in {
        str(issue.get("code")) for issue in report["issues"]
    }


def test_combined_contract_does_not_require_dealer_link_for_university_activity():
    report = radar_quality.build_event_dealer_quality_audit(
        _catalog("university_calendar"),
        [],
        as_of=AS_OF,
    )

    assert report["dealer_event_linkage"]["denominator"] == 0
    assert report["dealer_event_linkage"]["ready"] is True
    assert not any(
        task.get("issue_code") == "event.dealer_location_linkage_missing"
        for task in radar_quality.build_event_remediation_queue(
            _catalog("university_calendar"),
            as_of=AS_OF,
        )["tasks"]
    )
