from __future__ import annotations

import ast
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domains.events import candidate_staging, feed_adapters, us_coverage_registry


FIXTURES = Path(__file__).parent / "fixtures" / "event_feeds"
OBSERVED_AT = datetime(2026, 7, 15, 6, 30, tzinfo=timezone.utc)


def _registered_source(source_id: str) -> dict:
    return next(
        deepcopy(row)
        for row in us_coverage_registry.audit_registry()["event_sources"]
        if row["id"] == source_id
    )


def _reviewed_source(
    source_id: str,
    *,
    parser_profile: str,
    feed_url: str,
    timezone_name: str,
) -> dict:
    source = _registered_source(source_id)
    source.update(
        {
            "status": "active",
            "enabled": True,
            "direct_import_allowed": False,
            "requires_human_review": True,
            "terms_robots_status": "reviewed_allowed",
            "terms_robots_reviewer_id": "staff_7",
            "terms_robots_reviewed_at": "2026-07-15T05:00:00Z",
            "parser_profile": parser_profile,
            "feed_url": feed_url,
            "timezone": timezone_name,
        }
    )
    return source


def _tribe_source() -> dict:
    return _reviewed_source(
        "dealer_samys_photo_school_us",
        parser_profile=feed_adapters.PARSER_TRIBE_JSON,
        feed_url="https://samysphotoschool.com/wp-json/tribe/events/v1/events",
        timezone_name="America/Los_Angeles",
    )


def _ics_source() -> dict:
    return _reviewed_source(
        "university_gw_corcoran_events_dc_us",
        parser_profile=feed_adapters.PARSER_ICS,
        feed_url="https://calendar.gwu.edu/corcoran/calendar.ics",
        timezone_name="America/New_York",
    )


def _atom_source() -> dict:
    return _reviewed_source(
        "brand_viltrox_official_event_feed_us",
        parser_profile=feed_adapters.PARSER_ATOM,
        feed_url="https://viltrox.com/blogs/event.atom",
        timezone_name="America/New_York",
    )


def test_current_registry_is_fail_closed_before_malformed_payload_is_parsed() -> None:
    source = _registered_source("dealer_samys_photo_school_us")
    source.update(
        {
            "parser_profile": feed_adapters.PARSER_TRIBE_JSON,
            "feed_url": "https://samysphotoschool.com/wp-json/tribe/events/v1/events",
        }
    )

    preflight = feed_adapters.source_fetch_preflight(source)

    assert preflight["allowed"] is False
    assert {
        "source_not_active",
        "source_not_enabled",
        "terms_robots_not_reviewed_allowed",
        "terms_robots_reviewer_missing",
        "terms_robots_review_timestamp_invalid",
    } <= set(preflight["reasons"])
    with pytest.raises(feed_adapters.EventFeedBlocked, match="terms_robots_not_reviewed_allowed"):
        feed_adapters.adapt_feed_to_candidates(
            source,
            "this is intentionally not JSON",
            observed_at=OBSERVED_AT,
            organization_id=7,
        )


def test_preflight_rejects_unregistered_private_or_import_enabled_sources() -> None:
    source = _tribe_source()
    source.update(
        {
            "id": "unregistered_event_source",
            "feed_url": "https://127.0.0.1/events.json",
            "direct_import_allowed": True,
        }
    )

    result = feed_adapters.source_fetch_preflight(source)

    assert result["allowed"] is False
    assert {
        "source_not_registered",
        "feed_url_not_public_https",
        "direct_import_must_remain_disabled",
    } <= set(result["reasons"])


@pytest.mark.parametrize(
    "hostile_feed_url",
    [
        "https://feeds.attacker.example/wp-json/tribe/events/v1/events",
        "https://samysphotoschool.com/wp-json/tribe/events/v1/users",
    ],
)
def test_preflight_rejects_cross_domain_or_wrong_path_feed_binding(
    hostile_feed_url: str,
) -> None:
    source = _tribe_source()
    source["feed_url"] = hostile_feed_url

    result = feed_adapters.source_fetch_preflight(source)

    assert result["allowed"] is False
    assert "feed_url_registry_binding_mismatch" in result["reasons"]
    with pytest.raises(
        feed_adapters.EventFeedBlocked,
        match="feed_url_registry_binding_mismatch",
    ):
        feed_adapters.adapt_feed_to_candidates(
            source,
            (FIXTURES / "tribe_events.json").read_text(encoding="utf-8"),
            observed_at=OBSERVED_AT,
            organization_id=7,
        )


def test_only_bounded_tribe_pagination_query_is_ignored_for_feed_identity() -> None:
    source = _tribe_source()
    source["feed_url"] += "?page=2&per_page=50"

    allowed = feed_adapters.source_fetch_preflight(source)

    assert allowed["allowed"] is True
    assert allowed["feed_url"] == (
        "https://samysphotoschool.com/wp-json/tribe/events/v1/events"
    )
    source["feed_url"] += "&redirect=https://attacker.example"
    blocked = feed_adapters.source_fetch_preflight(source)
    assert blocked["allowed"] is False
    assert "feed_url_query_not_allowed" in blocked["reasons"]
    source = _tribe_source()
    source["feed_url"] += "?utm_source=unreviewed"
    tracking_query = feed_adapters.source_fetch_preflight(source)
    assert tracking_query["allowed"] is False
    assert "feed_url_query_not_allowed" in tracking_query["reasons"]


def test_rit_page_does_not_become_a_runnable_ics_source_without_exact_feed() -> None:
    source = _reviewed_source(
        "university_rit_filmfotofest_us",
        parser_profile=feed_adapters.PARSER_ICS,
        feed_url="https://www.rit.edu/events/filmfotofestrit26.ics",
        timezone_name="America/New_York",
    )

    result = feed_adapters.source_fetch_preflight(source)

    assert result["allowed"] is False
    assert "structured_feed_policy_missing" in result["reasons"]


def test_photoville_generic_wordpress_rows_do_not_become_dated_event_feed() -> None:
    source = _reviewed_source(
        "major_photoville_festival_us",
        parser_profile=feed_adapters.PARSER_TRIBE_JSON,
        feed_url="https://photoville.nyc/wp-json/wp/v2/event",
        timezone_name="America/New_York",
    )

    result = feed_adapters.source_fetch_preflight(source)

    assert result["allowed"] is False
    assert "structured_feed_policy_missing" in result["reasons"]


@pytest.mark.parametrize(
    ("source_id", "feed_url", "timezone_name"),
    [
        (
            "school_hcp_events_us",
            "https://hcponline.org/wp-json/tribe/events/v1/events",
            "America/Chicago",
        ),
        (
            "school_maine_media_photography_us",
            "https://www.mainemedia.edu/wp-json/tribe/events/v1/events",
            "America/New_York",
        ),
    ],
)
def test_new_school_feeds_require_the_exact_reviewed_first_party_endpoint(
    source_id: str,
    feed_url: str,
    timezone_name: str,
) -> None:
    source = _reviewed_source(
        source_id,
        parser_profile=feed_adapters.PARSER_TRIBE_JSON,
        feed_url=feed_url,
        timezone_name=timezone_name,
    )

    allowed = feed_adapters.source_fetch_preflight(source)

    assert allowed["allowed"] is True
    assert allowed["feed_url"] == feed_url
    source["feed_url"] = feed_url.replace("/events", "/users")
    blocked = feed_adapters.source_fetch_preflight(source)
    assert blocked["allowed"] is False
    assert "feed_url_registry_binding_mismatch" in blocked["reasons"]


def test_tribe_json_normalizes_provenance_times_location_hashes_and_duplicates() -> None:
    payload = (FIXTURES / "tribe_events.json").read_text(encoding="utf-8")

    result = feed_adapters.adapt_feed_to_candidates(
        _tribe_source(), payload, observed_at=OBSERVED_AT, organization_id=7
    )

    assert result["status"] == "ready"
    assert result["counts"] == {
        "parsed_items": 3,
        "candidate_items": 2,
        "duplicate_items": 1,
        "rejected_items": 0,
    }
    first = result["candidates"][0]
    item = first["candidate_payload"]
    assert item["event_uid"] == "samysphotoschool.com?id=901"
    assert item["start_at"] == "2026-08-21T10:00:00-07:00"
    assert item["end_at"] == "2026-08-21T13:00:00-07:00"
    assert item["timezone"] == "America/Los_Angeles"
    assert item["city"] == "Pasadena"
    assert item["region"] == "CA"
    assert item["country_code"] == "US"
    assert re.fullmatch(r"[0-9a-f]{64}", item["dedupe_fingerprint"])
    assert item["evidence_url"] == "https://samysphotoschool.com/event/video-lighting"
    assert item["description"] == "Practice a three-light interview setup."
    assert re.fullmatch(r"[0-9a-f]{64}", first["content_sha256"])
    assert re.fullmatch(r"event\.[0-9a-f]{40}", first["source_entity_key"])
    assert first["promotion_gate_status"] == "blocked"
    assert first["review_status"] == "pending"
    assert first["organization_id"] == 7
    assert re.fullmatch(r"cand_[0-9a-f]{32}", first["id"])
    assert item["provenance"] == {
        "source_registry_id": "dealer_samys_photo_school_us",
        "publisher": "Samy's Camera",
        "parser_profile": feed_adapters.PARSER_TRIBE_JSON,
        "feed_url": "https://samysphotoschool.com/wp-json/tribe/events/v1/events",
        "evidence_url": "https://samysphotoschool.com/event/video-lighting",
        "external_uid": "samysphotoschool.com?id=901",
        "observed_at": "2026-07-15T06:30:00+00:00",
        "terms_robots_status": "reviewed_allowed",
        "terms_robots_reviewed_at": "2026-07-15T05:00:00+00:00",
        "normalized_content_sha256": item["provenance"]["normalized_content_sha256"],
    }
    assert re.fullmatch(
        r"[0-9a-f]{64}", item["provenance"]["normalized_content_sha256"]
    )
    preview = candidate_staging.preview_candidate(
        {
            "record_only": True,
            "source_registry_id": first["source_registry_id"],
            "source_entity_key": first["source_entity_key"],
            "source_url": first["source_url"],
            "candidate_payload": item,
        },
        candidate_type="event_opportunity",
        organization_id=7,
    )
    assert preview["candidate"]["content_sha256"] == first["content_sha256"]
    assert preview["candidate"]["id"] == first["id"]


def test_normalized_hash_ignores_observation_clock_while_staging_hash_tracks_payload() -> None:
    payload = (FIXTURES / "tribe_events.json").read_text(encoding="utf-8")
    first = feed_adapters.adapt_feed_to_candidates(
        _tribe_source(), payload, observed_at=OBSERVED_AT, organization_id=7
    )["candidates"][0]
    later = feed_adapters.adapt_feed_to_candidates(
        _tribe_source(),
        payload,
        observed_at=datetime(2026, 7, 16, 6, 30, tzinfo=timezone.utc),
        organization_id=7,
    )["candidates"][0]

    assert first["source_entity_key"] == later["source_entity_key"]
    assert first["content_sha256"] != later["content_sha256"]
    assert (
        first["candidate_payload"]["provenance"]["normalized_content_sha256"]
        == later["candidate_payload"]["provenance"]["normalized_content_sha256"]
    )
    assert (
        first["candidate_payload"]["provenance"]["observed_at"]
        != later["candidate_payload"]["provenance"]["observed_at"]
    )


def test_ics_normalizes_tzid_uid_and_exclusive_all_day_end_date() -> None:
    payload = (FIXTURES / "gw_events.ics").read_text(encoding="utf-8")

    result = feed_adapters.adapt_feed_to_candidates(
        _ics_source(), payload, observed_at=OBSERVED_AT, organization_id=7
    )

    assert result["counts"]["candidate_items"] == 2
    timed, all_day = result["candidates"]
    timed_item = timed["candidate_payload"]
    assert timed_item["event_uid"] == "gw-corcoran-photo-20260910@example.edu"
    assert timed_item["start_at"] == "2026-09-10T18:00:00-04:00"
    assert timed_item["timezone"] == "America/New_York"
    assert timed_item["venue"] == "Corcoran School"
    assert timed_item["city"] == "Washington"
    assert timed_item["region"] == "DC"
    assert timed_item["evidence_url"] == "https://calendar.gwu.edu/event/documentary-photography-lecture"
    all_day_item = all_day["candidate_payload"]
    assert all_day_item["event_uid"] == (
        "gw-corcoran-film-photo-festival-20261002@example.edu"
    )
    assert all_day_item["date_precision"] == "date"
    assert all_day_item["start_date"] == "2026-10-02"
    assert all_day_item["end_date"] == "2026-10-04"
    assert all_day_item["region"] == "DC"


def test_atom_requires_explicit_event_time_and_verified_us_location() -> None:
    payload = (FIXTURES / "viltrox_events.atom").read_text(encoding="utf-8")

    result = feed_adapters.adapt_feed_to_candidates(
        _atom_source(), payload, observed_at=OBSERVED_AT, organization_id=7
    )

    assert result["counts"] == {
        "parsed_items": 3,
        "candidate_items": 1,
        "duplicate_items": 0,
        "rejected_items": 2,
    }
    item = result["candidates"][0]["candidate_payload"]
    assert item["event_uid"] == "gid://shopify/Article/usa-demo-2026"
    assert item["start_at"] == "2026-09-12T10:00:00-07:00"
    assert item["region"] == "CA"
    assert {row["reason"] for row in result["rejections"]} == {
        "missing event time",
        "us_location_unverified",
    }


def test_conflicting_duplicate_uid_is_removed_instead_of_silently_overwritten() -> None:
    payload = json.loads((FIXTURES / "tribe_events.json").read_text(encoding="utf-8"))
    payload["events"] = payload["events"][:2]
    payload["events"][1]["title"] = "Conflicting title for the same upstream UID"

    result = feed_adapters.adapt_feed_to_candidates(
        _tribe_source(), payload, observed_at=OBSERVED_AT, organization_id=7
    )

    assert result["counts"] == {
        "parsed_items": 2,
        "candidate_items": 0,
        "duplicate_items": 1,
        "rejected_items": 1,
    }
    assert result["rejections"] == [
        {"index": 1, "reason": "duplicate_identity_conflict"}
    ]


def test_cross_domain_item_evidence_is_rejected_and_never_becomes_source_url() -> None:
    payload = json.loads((FIXTURES / "tribe_events.json").read_text(encoding="utf-8"))
    payload["events"] = [payload["events"][0]]
    payload["events"][0]["url"] = "https://tickets.attacker.example/event/901"

    result = feed_adapters.adapt_feed_to_candidates(
        _tribe_source(), payload, observed_at=OBSERVED_AT, organization_id=7
    )

    assert result["status"] == "empty"
    assert result["candidates"] == []
    assert result["counts"]["rejected_items"] == 1
    assert result["rejections"] == [
        {"index": 0, "reason": "evidence_url_host_not_allowlisted"}
    ]


@pytest.mark.parametrize(
    ("source", "payload", "message"),
    [
        (_tribe_source(), "{not-json", "valid JSON"),
        (_ics_source(), "BEGIN:VCALENDAR\nBEGIN:VEVENT", "VCALENDAR envelope"),
        (_atom_source(), "<feed><entry>", "valid XML"),
    ],
)
def test_malformed_structured_envelopes_fail_without_partial_candidates(
    source: dict, payload: str, message: str
) -> None:
    with pytest.raises(feed_adapters.MalformedEventFeed, match=message):
        feed_adapters.adapt_feed_to_candidates(
            source, payload, observed_at=OBSERVED_AT, organization_id=7
        )


def test_supported_source_bindings_do_not_enable_fetch_or_scheduler() -> None:
    assert feed_adapters.STRUCTURED_SOURCE_PARSER_PROFILES == {
        "dealer_samys_photo_school_us": feed_adapters.PARSER_TRIBE_JSON,
        "dealer_hunts_photo_calendar_us": feed_adapters.PARSER_TRIBE_JSON,
        "dealer_natcam_events_us": feed_adapters.PARSER_TRIBE_JSON,
        "dealer_pauls_creative_academy_us": feed_adapters.PARSER_TRIBE_JSON,
        "photo_asmp_chapters_us": feed_adapters.PARSER_TRIBE_JSON,
        "school_hcp_events_us": feed_adapters.PARSER_TRIBE_JSON,
        "school_maine_media_photography_us": feed_adapters.PARSER_TRIBE_JSON,
        "university_gw_corcoran_events_dc_us": feed_adapters.PARSER_ICS,
        "brand_viltrox_official_event_feed_us": feed_adapters.PARSER_ATOM,
    }
    assert {
        source_id: policy["feed_url"]
        for source_id, policy in feed_adapters.STRUCTURED_SOURCE_FEED_POLICIES.items()
    } == {
        "dealer_samys_photo_school_us": (
            "https://samysphotoschool.com/wp-json/tribe/events/v1/events"
        ),
        "dealer_hunts_photo_calendar_us": (
            "https://edu.huntsphoto.com/wp-json/tribe/events/v1/events"
        ),
        "dealer_natcam_events_us": (
            "https://www.natcam.com/wp-json/tribe/events/v1/events"
        ),
        "dealer_pauls_creative_academy_us": (
            "https://creativephotoacademy.com/wp-json/tribe/events/v1/events"
        ),
        "photo_asmp_chapters_us": (
            "https://www.asmp.org/wp-json/tribe/events/v1/events"
        ),
        "school_hcp_events_us": (
            "https://hcponline.org/wp-json/tribe/events/v1/events"
        ),
        "school_maine_media_photography_us": (
            "https://www.mainemedia.edu/wp-json/tribe/events/v1/events"
        ),
        "university_gw_corcoran_events_dc_us": (
            "https://calendar.gwu.edu/corcoran/calendar.ics"
        ),
        "brand_viltrox_official_event_feed_us": (
            "https://viltrox.com/blogs/event.atom"
        ),
    }
    assert "university_rit_filmfotofest_us" not in (
        feed_adapters.STRUCTURED_SOURCE_FEED_POLICIES
    )
    assert {
        "major_photoville_festival_us",
        "school_texas_school_professional_photography_us",
        "community_smpte_events_us",
        "university_usc_sca_events_us",
    }.isdisjoint(feed_adapters.STRUCTURED_SOURCE_FEED_POLICIES)
    result = feed_adapters.adapt_feed_to_candidates(
        _tribe_source(),
        (FIXTURES / "tribe_events.json").read_text(encoding="utf-8"),
        observed_at=OBSERVED_AT,
        organization_id=7,
    )
    assert result["contract"] == {
        "id": "vkpi.event_radar.structured_feed_candidates",
        "version": 1,
        "network_accessed": False,
        "database_accessed": False,
        "provider_calls": 0,
        "scheduler_enabled": False,
        "business_rows_written": 0,
        "candidate_rows_written": 0,
    }
    assert result["promotion_gate"] == {
        "status": "blocked",
        "automatic_promotion": False,
        "human_review_required": True,
    }
    assert result["dedupe"] == {
        "automatic_cross_source_merge": False,
        "within_source_payload": True,
        "cross_source_review_fingerprint": "candidate_payload.dedupe_fingerprint",
    }


def test_full_state_name_and_zip_are_normalized_without_fuzzy_matching() -> None:
    location = feed_adapters._location_parts(  # noqa: SLF001 - contract test
        address="100 Congress St, Portland, Maine 04101"
    )

    assert location["city"] == "Portland"
    assert location["region"] == "ME"
    assert location["country_code"] == "US"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("2026-03-08T02:30:00", "does not exist"),
        ("2026-11-01T01:30:00", "ambiguous"),
    ],
)
def test_naive_dst_edge_times_fail_closed(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        feed_adapters._parse_datetime(  # noqa: SLF001 - contract test
            value,
            timezone_name="America/New_York",
        )


def test_explicit_offset_disambiguates_dst_time() -> None:
    normalized, local_date, precision = feed_adapters._parse_datetime(  # noqa: SLF001
        "2026-11-01T01:30:00-04:00",
        timezone_name="America/New_York",
    )

    assert normalized == "2026-11-01T01:30:00-04:00"
    assert local_date == "2026-11-01"
    assert precision == "date_time"


def test_aware_instant_is_normalized_to_named_source_timezone_and_local_date() -> None:
    normalized, local_date, precision = feed_adapters._parse_datetime(  # noqa: SLF001
        "2026-08-21T02:30:00Z",
        timezone_name="America/Los_Angeles",
    )

    assert normalized == "2026-08-20T19:30:00-07:00"
    assert local_date == "2026-08-20"
    assert precision == "date_time"


def test_adapter_module_has_no_network_database_worker_or_scheduler_dependency() -> None:
    source_text = Path(feed_adapters.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    imported_modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(
        module.startswith(
            (
                "requests",
                "httpx",
                "urllib.request",
                "app.db",
                "app.workers",
                "app.scheduler",
            )
        )
        for module in imported_modules
    )
    assert "get_conn" not in source_text
    assert "INSERT INTO" not in source_text
    assert "UPDATE vkpi_" not in source_text
