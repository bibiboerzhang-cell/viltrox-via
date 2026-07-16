"""Pure Event catalog audit with a compatibility Dealer re-export."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    _COUNTRY_RE,
    _NONACTIVE_SOURCE_STATUSES,
    _POSITIVE_VILTROX_STATUSES,
    _SAFE_REVIEWER_ID_RE,
    _SOURCE_ID_RE,
    _STABLE_LOCATION_RE,
    _UNSUPPORTED_POSITIVE_CLAIMS,
    _as_utc,
    _evidence_contract_valid,
    _evidence_covered,
    _exact_linkage_coverage,
    _freshness,
    _global_coverage,
    _is_https_url,
    _issue_counts,
    _issue_factory,
    _positive_claim,
    _rate,
    _reviewed_location_key_universe,
    _review_status,
    _task,
)
from app.domains.events.radar_quality_dealer_audit import audit_dealer_candidates


_LOCAL_ACTIVITY_SOURCE_KINDS = {
    "dealer_event",
    "venue_calendar",
    "school_calendar",
    "university_calendar",
    "photo_club",
    "community_calendar",
    "brand_event",
}
_EVENT_SOURCE_KINDS = {"major_expo", *_LOCAL_ACTIVITY_SOURCE_KINDS}
_EVENT_SOURCE_STATUSES = {"active", *_NONACTIVE_SOURCE_STATUSES}
_EVENT_LANES = {"major_expo", "dealer_event", "local_activity"}
_EVENT_STATUSES = {"scheduled", "postponed", "cancelled", "ended", "unknown"}
_EVENT_DATE_PRECISIONS = {"date", "date_time", "month_only", "tbd"}


def _valid_iana_timezone(value: Any) -> bool:
    name = str(value or "").strip()
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _parse_calendar_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    # ``date.fromisoformat`` accepts compact forms such as YYYYMMDD.  The Event
    # contract deliberately persists the single canonical YYYY-MM-DD spelling.
    return parsed if text == parsed.isoformat() else None


def audit_event_catalog(
    catalog: dict[str, Any],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_source_universe_denominator: Any = None,
    reviewed_dealer_location_keys: Any = None,
) -> dict[str, Any]:
    """Audit the reviewed Event catalog at source and opportunity grain."""
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    stale_after_days = int(stale_after_days)
    reviewed_dealer_locations = _reviewed_location_key_universe(reviewed_dealer_location_keys)
    payload = deepcopy(catalog or {})
    issues: list[dict[str, str]] = []
    issue = _issue_factory(issues)
    sources = payload.get("sources")
    opportunities = payload.get("opportunities")
    if not isinstance(sources, list):
        issue("error", "event.sources_type", "sources", "sources must be an array")
        sources = []
    if not isinstance(opportunities, list):
        issue("error", "event.opportunities_type", "opportunities", "opportunities must be an array")
        opportunities = []
    if payload.get("global_complete") is not False:
        issue("error", "event.global_complete", "global_complete", "global_complete must remain false")
    if str(payload.get("coverage_claim") or "") != "registered_publisher_owned_public_entries_only":
        issue("error", "event.coverage_claim", "coverage_claim", "coverage must stay bounded to registered publisher-owned public entries")

    catalog_freshness = _freshness(payload.get("checked_at"), as_of=now, stale_after_days=stale_after_days)
    if catalog_freshness["status"] != "fresh":
        issue("error", "event.catalog_not_fresh", "checked_at", "reviewed catalog snapshot is stale or unavailable")

    source_ids: list[str] = []
    source_urls: list[str] = []
    source_by_id: dict[str, dict[str, Any]] = {}
    source_fresh_count = 0
    source_review_evidence_count = 0
    active_source_count = 0
    active_fresh_source_ids: set[str] = set()
    source_evidence_records: list[dict[str, Any]] = []
    for index, raw in enumerate(sources):
        path = f"sources[{index}]"
        if not isinstance(raw, dict):
            issue("error", "event.source_type", path, "source must be an object")
            source_evidence_records.append(
                {
                    "source_index": index,
                    "source_id": None,
                    "source_url": None,
                    "observed_at": None,
                    "declared_review_status": None,
                    "review_status": "invalid_candidate_row",
                    "reviewer_id": None,
                    "evidence_scope": None,
                    "value_status": None,
                    "operational_status": "unknown",
                    "import_eligible": False,
                }
            )
            continue
        source_id = str(raw.get("id") or "").strip()
        source_url = str(raw.get("canonical_url") or "").strip()
        source_ids.append(source_id)
        source_urls.append(source_url)
        if not _SOURCE_ID_RE.fullmatch(source_id):
            issue("error", "event.source_id_missing_or_invalid", f"{path}.id", "stable source id is required")
        elif source_id not in source_by_id:
            source_by_id[source_id] = raw
        if not _is_https_url(source_url):
            issue("error", "event.source_url_invalid", f"{path}.canonical_url", "credential-free HTTPS source URL is required")
        source_kind = str(raw.get("source_kind") or "").strip().casefold()
        if source_kind not in _EVENT_SOURCE_KINDS:
            issue(
                "error",
                "event.source_kind_invalid",
                f"{path}.source_kind",
                "source_kind must be one of the supported exhibition, dealer, venue, school, university, photo-club, community, or brand calendars",
            )
        source_country = str(raw.get("country_code") or "").strip()
        source_country_valid = bool(_COUNTRY_RE.fullmatch(source_country))
        if not source_country_valid:
            issue(
                "error",
                "event.source_country_invalid",
                f"{path}.country_code",
                "source country_code must be uppercase ISO alpha-2",
            )
        source_timezone = str(raw.get("timezone") or "").strip()
        source_timezone_valid = _valid_iana_timezone(source_timezone)
        if not source_timezone_valid:
            issue(
                "error",
                "event.source_timezone_invalid",
                f"{path}.timezone",
                "source timezone must be a valid IANA timezone",
            )
        status = str(raw.get("status") or "unknown").strip().casefold()
        if status not in _EVENT_SOURCE_STATUSES:
            issue(
                "error",
                "event.source_status_invalid",
                f"{path}.status",
                "source status must be active, hold, blocked, or retired",
            )
        enabled_value = raw.get("enabled")
        if enabled_value is not None and not isinstance(enabled_value, bool):
            issue(
                "error",
                "event.source_enabled_invalid",
                f"{path}.enabled",
                "enabled must be a boolean when present",
            )
        source_enabled = enabled_value is None or enabled_value is True
        if status == "active" and source_enabled:
            active_source_count += 1
        if status == "active" and not source_enabled:
            issue(
                "error",
                "event.active_source_disabled",
                f"{path}.enabled",
                "an active source cannot be explicitly disabled",
            )
        if status in _NONACTIVE_SOURCE_STATUSES and raw.get("enabled") is True:
            issue("error", "event.nonactive_source_enabled", f"{path}.enabled", "non-active source cannot be enabled")
        source_freshness = _freshness(
            raw.get("source_checked_at"),
            as_of=now,
            stale_after_days=stale_after_days,
        )
        source_contract_valid = _evidence_contract_valid(
            raw,
            expected_scope="event_source_listing",
        )
        if source_freshness["status"] == "fresh":
            source_fresh_count += 1
            if (
                status == "active"
                and source_enabled
                and source_kind in _EVENT_SOURCE_KINDS
                and _SOURCE_ID_RE.fullmatch(source_id)
                and _is_https_url(source_url)
                and source_country_valid
                and source_timezone_valid
                and source_contract_valid
            ):
                active_fresh_source_ids.add(source_id)
        if source_contract_valid:
            source_review_evidence_count += 1
        else:
            issue(
                "error",
                "event.source_evidence_contract_invalid",
                f"{path}.reviewer_id",
                "Event source evidence requires safe reviewer_id, evidence_scope=event_source_listing, and value_status=observed",
            )
        reviewer_id = str(raw.get("reviewer_id") or "").strip()
        source_evidence_records.append(
            {
                "source_index": index,
                "source_id": source_id or None,
                "source_url": source_url if _is_https_url(source_url) else None,
                "observed_at": source_freshness["checked_at"],
                "declared_review_status": str(raw.get("review_status") or "").strip() or None,
                "review_status": _review_status(
                    declared_reviewed=True,
                    evidence_contract_valid=source_contract_valid,
                    freshness_status=str(source_freshness["status"]),
                ),
                "reviewer_id": (
                    reviewer_id if _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id) else None
                ),
                "evidence_scope": str(raw.get("evidence_scope") or "").strip() or None,
                "value_status": str(raw.get("value_status") or "").strip().casefold() or None,
                "operational_status": status,
                "import_eligible": bool(
                    status == "active"
                    and source_enabled
                    and source_kind in _EVENT_SOURCE_KINDS
                    and _SOURCE_ID_RE.fullmatch(source_id)
                    and _is_https_url(source_url)
                    and source_country_valid
                    and source_timezone_valid
                    and source_freshness["status"] == "fresh"
                    and source_contract_valid
                ),
            }
        )

    if sources and source_fresh_count != len(sources):
        issue(
            "error",
            "event.source_freshness_incomplete",
            "sources[*].source_checked_at",
            "every imported source requires its own current source_checked_at; catalog checked_at is not a substitute",
        )
    if sources and source_review_evidence_count != len(sources):
        issue(
            "error",
            "event.source_review_evidence_incomplete",
            "sources[*].reviewer_id",
            "every imported source requires safe reviewer_id plus explicit evidence_scope and value_status",
        )

    opportunity_ids: list[str] = []
    canonical_keys: list[str] = []
    external_keys: list[tuple[str, str]] = []
    activity_url_presence_count = 0
    activity_evidence_count = 0
    viltrox_evidence_count = 0
    exact_dealer_links = 0
    declared_dealer_links = 0
    name_only_dealer_hints = 0
    dealer_local_count = 0
    opportunity_evidence_records: list[dict[str, Any]] = []
    for index, raw in enumerate(opportunities):
        path = f"opportunities[{index}]"
        if not isinstance(raw, dict):
            issue("error", "event.opportunity_type", path, "opportunity must be an object")
            opportunity_evidence_records.append(
                {
                    "opportunity_index": index,
                    "opportunity_id": None,
                    "source_id": None,
                    "source_url": None,
                    "observed_at": None,
                    "declared_review_status": None,
                    "review_status": "invalid_candidate_row",
                    "reviewer_id": None,
                    "evidence_scope": None,
                    "value_status": None,
                    "declared_stable_location_key": None,
                    "exact_stable_location_key": None,
                    "association_status": "unlinked_no_fuzzy_match",
                    "import_eligible": False,
                }
            )
            continue
        opportunity_id = str(raw.get("id") or "").strip()
        canonical_key = str(raw.get("canonical_key") or "").strip()
        source_id = str(raw.get("source_id") or "").strip()
        external_key = str(raw.get("external_event_key") or "").strip()
        opportunity_ids.append(opportunity_id)
        canonical_keys.append(canonical_key)
        external_keys.append((source_id, external_key))
        if not opportunity_id:
            issue("error", "event.opportunity_id_missing", f"{path}.id", "stable opportunity id is required")
        if not canonical_key:
            issue("error", "event.canonical_key_missing", f"{path}.canonical_key", "canonical entity key is required")
        if source_id not in source_by_id:
            issue("error", "event.source_orphan", f"{path}.source_id", "opportunity source id is unknown")
        if not external_key:
            issue("error", "event.external_event_key_missing", f"{path}.external_event_key", "source event key is required")
        source_row = source_by_id.get(source_id) or {}
        source_status = str(source_row.get("status") or "unknown").strip().casefold()
        source_enabled_value = source_row.get("enabled")
        source_enabled = source_enabled_value is None or source_enabled_value is True
        source_kind = str(source_row.get("source_kind") or "").strip().casefold()
        source_actionable = bool(
            source_row
            and source_status == "active"
            and source_enabled
            and source_kind in _EVENT_SOURCE_KINDS
        )
        if source_row and not source_actionable:
            issue(
                "error",
                "event.nonactive_source_opportunity",
                f"{path}.source_id",
                "only an enabled active source with a supported source_kind may emit opportunities",
            )

        lane = str(raw.get("lane") or "").strip().casefold()
        lane_valid = lane in _EVENT_LANES
        if not lane_valid:
            issue(
                "error",
                "event.lane_invalid",
                f"{path}.lane",
                "lane must be major_expo, dealer_event, or local_activity",
            )
        lane_source_valid = bool(
            lane_valid
            and source_row
            and (
                (lane == "major_expo" and source_kind == "major_expo")
                or (lane == "dealer_event" and source_kind == "dealer_event")
                or (lane == "local_activity" and source_kind in _LOCAL_ACTIVITY_SOURCE_KINDS)
            )
        )
        if lane_valid and source_row and not lane_source_valid:
            issue(
                "error",
                "event.lane_source_kind_mismatch",
                f"{path}.lane",
                "major exhibitions require major_expo sources; dealer events require dealer_event sources; local activities require a supported local calendar source",
            )
        country = str(raw.get("country_code") or "").strip()
        country_valid = bool(_COUNTRY_RE.fullmatch(country))
        if not country_valid:
            issue(
                "error",
                "event.activity_country_invalid",
                f"{path}.country_code",
                "activity country_code must be uppercase ISO alpha-2",
            )
        timezone_name = str(raw.get("timezone") or "").strip()
        timezone_valid = _valid_iana_timezone(timezone_name)
        if not timezone_valid:
            issue(
                "error",
                "event.activity_timezone_invalid",
                f"{path}.timezone",
                "activity timezone must be a valid IANA timezone",
            )
        source_country = str(source_row.get("country_code") or "").strip()
        source_timezone = str(source_row.get("timezone") or "").strip()
        source_alignment_valid = bool(
            source_row
            and country_valid
            and timezone_valid
            and country == source_country
            and timezone_name == source_timezone
        )
        if source_row and country != source_country:
            issue(
                "error",
                "event.activity_source_country_mismatch",
                f"{path}.country_code",
                "activity country must match its registered source",
            )
        if source_row and timezone_name != source_timezone:
            issue(
                "error",
                "event.activity_source_timezone_mismatch",
                f"{path}.timezone",
                "activity timezone must match its registered source",
            )
        event_status = str(raw.get("event_status") or "scheduled").strip().casefold()
        event_status_valid = event_status in _EVENT_STATUSES
        if not event_status_valid:
            issue(
                "error",
                "event.activity_status_invalid",
                f"{path}.event_status",
                "unsupported activity event_status",
            )
        date_precision = str(raw.get("date_precision") or "date").strip().casefold()
        date_precision_valid = date_precision in _EVENT_DATE_PRECISIONS
        if not date_precision_valid:
            issue(
                "error",
                "event.activity_date_precision_invalid",
                f"{path}.date_precision",
                "unsupported activity date_precision",
            )
        start_date = _parse_calendar_date(raw.get("start_date"))
        end_date = _parse_calendar_date(raw.get("end_date"))
        dates_valid = True
        if raw.get("start_date") not in (None, "") and start_date is None:
            dates_valid = False
            issue(
                "error",
                "event.activity_start_date_invalid",
                f"{path}.start_date",
                "activity start_date must be canonical YYYY-MM-DD",
            )
        if raw.get("end_date") not in (None, "") and end_date is None:
            dates_valid = False
            issue(
                "error",
                "event.activity_end_date_invalid",
                f"{path}.end_date",
                "activity end_date must be canonical YYYY-MM-DD",
            )
        verification_status = str(raw.get("verification_status") or "").strip().casefold()
        if verification_status == "verified" and event_status == "scheduled" and (
            start_date is None or end_date is None
        ):
            dates_valid = False
            issue(
                "error",
                "event.activity_verified_dates_required",
                path,
                "verified scheduled activity requires start_date and end_date",
            )
        if start_date is not None and end_date is not None and end_date < start_date:
            dates_valid = False
            issue(
                "error",
                "event.activity_date_order_invalid",
                path,
                "activity end_date cannot precede start_date",
            )
        activity_domain_valid = bool(
            country_valid
            and timezone_valid
            and source_alignment_valid
            and event_status_valid
            and date_precision_valid
            and dates_valid
        )
        official_url = raw.get("official_url")
        if not _is_https_url(official_url):
            issue("error", "event.official_url_invalid", f"{path}.official_url", "official HTTPS activity URL is required")
        else:
            activity_url_presence_count += 1
        activity_freshness = _freshness(
            raw.get("source_checked_at"),
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if activity_freshness["status"] != "fresh":
            issue(
                "error",
                "event.activity_observed_at_missing_or_stale",
                f"{path}.source_checked_at",
                "each opportunity requires its own current source_checked_at; source-row freshness is not an observation substitute",
            )
        activity_contract_valid = _evidence_contract_valid(
            raw,
            expected_scope="event_official_listing",
        )
        if not activity_contract_valid:
            issue(
                "error",
                "event.activity_evidence_contract_invalid",
                f"{path}.reviewer_id",
                "Event activity evidence requires safe reviewer_id, evidence_scope=event_official_listing, and value_status=observed",
            )
        activity_import_eligible = bool(
            str(raw.get("verification_status") or "").casefold() == "verified"
            and _is_https_url(official_url)
            and activity_freshness["status"] == "fresh"
            and activity_contract_valid
            and source_actionable
            and lane_source_valid
            and activity_domain_valid
        )
        if activity_import_eligible:
            activity_evidence_count += 1
        presence = str(raw.get("viltrox_presence_status") or "unknown").strip().casefold()
        if presence in _POSITIVE_VILTROX_STATUSES | {"not_found"}:
            evidence = raw.get("viltrox_evidence")
            if not isinstance(evidence, dict):
                evidence = {
                    "status": "verified",
                    "source_url": raw.get("viltrox_evidence_url"),
                    "checked_at": raw.get("source_checked_at") or payload.get("checked_at"),
                    "reviewer_id": raw.get("viltrox_reviewer_id"),
                    "evidence_scope": raw.get("viltrox_evidence_scope"),
                    "value_status": raw.get("viltrox_value_status"),
                }
            if _evidence_covered(
                evidence,
                as_of=now,
                stale_after_days=stale_after_days,
                expected_scope="event_viltrox_presence",
                allowed_value_statuses=(
                    {"not_found"} if presence == "not_found" else {"observed"}
                ),
            ):
                viltrox_evidence_count += 1
            else:
                issue("error", "event.viltrox_presence_without_evidence", f"{path}.viltrox_presence_status", "Viltrox presence observation needs separate current scoped evidence")
        elif presence != "unknown":
            issue("error", "event.viltrox_presence_status_invalid", f"{path}.viltrox_presence_status", "unsupported Viltrox presence status")

        dealer_location_key = str(raw.get("dealer_stable_location_key") or "").strip()
        dealer_match_name = str(raw.get("dealer_match_name") or "").strip()
        dealer_link_applicable = bool(
            lane == "dealer_event"
            or (lane == "local_activity" and source_kind == "dealer_event")
        )
        if dealer_link_applicable:
            dealer_local_count += 1
            if dealer_location_key and _STABLE_LOCATION_RE.fullmatch(dealer_location_key):
                declared_dealer_links += 1
                if reviewed_dealer_locations is not None and dealer_location_key in reviewed_dealer_locations:
                    exact_dealer_links += 1
                elif reviewed_dealer_locations is not None:
                    issue(
                        "error",
                        "event.dealer_location_key_unresolved",
                        f"{path}.dealer_stable_location_key",
                        "dealer_stable_location_key is absent from the supplied reviewed Dealer universe",
                    )
            elif dealer_location_key:
                issue(
                    "error",
                    "event.dealer_location_key_invalid",
                    f"{path}.dealer_stable_location_key",
                    "dealer_stable_location_key must satisfy the exact Dealer location identity contract",
                )
            elif dealer_match_name:
                name_only_dealer_hints += 1
        elif dealer_location_key or dealer_match_name:
            issue(
                "error",
                "event.dealer_link_wrong_lane",
                f"{path}.dealer_stable_location_key",
                "Dealer identity links are only valid for dealer-backed dealer_event or local_activity opportunities",
            )
        reviewer_id = str(raw.get("reviewer_id") or "").strip()
        declared_location_key = (
            dealer_location_key
            if dealer_link_applicable
            and _STABLE_LOCATION_RE.fullmatch(dealer_location_key)
            else None
        )
        exact_location_key = (
            declared_location_key
            if declared_location_key in (reviewed_dealer_locations or ())
            else None
        )
        association_status = (
            "not_applicable"
            if not dealer_link_applicable
            else "exact_reviewed_location"
            if exact_location_key
            else "declared_exact_key_unresolved"
            if declared_location_key
            else "name_hint_only_not_linked"
            if dealer_match_name
            else "unlinked_no_fuzzy_match"
        )
        opportunity_evidence_records.append(
            {
                "opportunity_index": index,
                "opportunity_id": opportunity_id or None,
                "source_id": source_id or None,
                "source_url": str(official_url).strip() if _is_https_url(official_url) else None,
                "observed_at": activity_freshness["checked_at"],
                "declared_review_status": (
                    str(raw.get("verification_status") or "").strip().casefold() or None
                ),
                "review_status": _review_status(
                    declared_reviewed=(
                        str(raw.get("verification_status") or "").strip().casefold()
                        == "verified"
                    ),
                    evidence_contract_valid=activity_contract_valid,
                    freshness_status=str(activity_freshness["status"]),
                ),
                "reviewer_id": (
                    reviewer_id if _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id) else None
                ),
                "evidence_scope": str(raw.get("evidence_scope") or "").strip() or None,
                "value_status": str(raw.get("value_status") or "").strip().casefold() or None,
                "declared_stable_location_key": dealer_location_key or None,
                "exact_stable_location_key": exact_location_key,
                "association_status": association_status,
                "import_eligible": activity_import_eligible,
            }
        )
        for field in sorted(_UNSUPPORTED_POSITIVE_CLAIMS):
            if field in raw and _positive_claim(raw.get(field)):
                issue("error", "event.unsupported_business_claim", f"{path}.{field}", f"unsupported positive business claim: {field}")

    def duplicates(values: list[Any]) -> list[Any]:
        counts = Counter(value for value in values if value not in (None, "", ("", "")))
        return sorted(value for value, count in counts.items() if count > 1)

    duplicate_source_ids = duplicates(source_ids)
    duplicate_source_urls = duplicates(source_urls)
    duplicate_opportunity_ids = duplicates(opportunity_ids)
    duplicate_canonical_keys = duplicates(canonical_keys)
    duplicate_external_keys = duplicates(external_keys)
    for code, values in (
        ("event.source_id_duplicate", duplicate_source_ids),
        ("event.source_url_duplicate", duplicate_source_urls),
        ("event.opportunity_id_duplicate", duplicate_opportunity_ids),
        ("event.canonical_key_duplicate", duplicate_canonical_keys),
        ("event.external_key_duplicate", duplicate_external_keys),
    ):
        for value in values:
            issue("error", code, "catalog", f"exact duplicate key: {value!r}")

    active_fresh_source_count = len(active_fresh_source_ids)
    observed_source_inventory = [
        {
            "entity_id": str(raw.get("id") or "").strip(),
            "source_id": str(raw.get("id") or "").strip(),
            "canonical_url": str(raw.get("canonical_url") or "").strip(),
        }
        if isinstance(raw, dict)
        else {"entity_id": None, "source_id": None, "canonical_url": None}
        for raw in sources
    ]
    coverage = {
        "reviewed_active_sources": _rate(active_source_count, len(sources)),
        "source_row_freshness": _rate(source_fresh_count, len(sources)),
        "source_review_evidence": _rate(source_review_evidence_count, len(sources)),
        "activity_url_presence": _rate(activity_url_presence_count, len(opportunities)),
        "activity_evidence": _rate(activity_evidence_count, len(opportunities)),
        "viltrox_presence_evidence": _rate(viltrox_evidence_count, len(opportunities)),
        "exact_dealer_location_linkage": _exact_linkage_coverage(
            exact_dealer_links, dealer_local_count, reviewed_dealer_locations
        ),
        "global_source_coverage": _global_coverage(
            active_fresh_source_count,
            known_source_universe_denominator,
            observed_inventory=observed_source_inventory,
            issue=issue,
            code="event.global_source_coverage",
            path="known_source_universe_denominator",
            expected_scope="event_sources",
        ),
    }
    if viltrox_evidence_count < len(opportunities):
        issue("warning", "event.viltrox_presence_evidence_incomplete", "opportunities", "unknown Viltrox presence is not covered")
    if exact_dealer_links < dealer_local_count:
        issue("warning", "event.dealer_location_linkage_incomplete", "opportunities", "name hints do not count as exact Dealer location linkage")
    if known_source_universe_denominator is None:
        issue("warning", "event.global_denominator_unavailable", "known_source_universe_denominator", "global Event source coverage cannot be calculated")
    counts = _issue_counts(issues)
    import_allowed = (
        counts["errors"] == 0
        and bool(sources)
        and bool(opportunities)
        and catalog_freshness["status"] == "fresh"
        and source_fresh_count == len(sources)
        and source_review_evidence_count == len(sources)
        and activity_evidence_count == len(opportunities)
    )
    optional_evidence_complete = (
        source_fresh_count == len(sources)
        and source_review_evidence_count == len(sources)
        and viltrox_evidence_count == len(opportunities)
        and exact_dealer_links == dealer_local_count
        and coverage["global_source_coverage"]["manifest_status"] == "accepted"
    )
    return {
        "contract": {"id": CONTRACT_ID, "version": CONTRACT_VERSION, "scope": "event_catalog"},
        "ok": counts["errors"] == 0,
        "quality_status": (
            "blocked_for_import"
            if not import_allowed
            else "verified_descriptive"
            if optional_evidence_complete
            else "partial_descriptive"
        ),
        "claim_status": "descriptive_only",
        "read_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
        "as_of": now.isoformat(),
        "stale_after_days": stale_after_days,
        "catalog_freshness": catalog_freshness,
        "counts": {
            "reviewed_sources": len(sources),
            "active_sources_with_current_check": active_fresh_source_count,
            "reviewed_opportunities": len(opportunities),
            "dealer_or_local_opportunities": dealer_local_count,
            "name_only_dealer_hints": name_only_dealer_hints,
            "declared_dealer_location_keys": declared_dealer_links,
            "exact_dealer_location_links": exact_dealer_links,
        },
        "coverage": coverage,
        "evidence_records": {
            "source_grain": "one_record_per_catalog_source",
            "opportunity_grain": "one_record_per_catalog_opportunity",
            "association_policy": "source_id_and_stable_location_key_exact_only",
            "sources": source_evidence_records,
            "opportunities": opportunity_evidence_records,
        },
        "deduplication": {
            "mode": "exact_keys_only_no_fuzzy_auto_merge",
            "source_key": "id",
            "opportunity_keys": ["id", "canonical_key", "(source_id,external_event_key)"],
            "duplicate_source_ids": duplicate_source_ids,
            "duplicate_source_urls": duplicate_source_urls,
            "duplicate_opportunity_ids": duplicate_opportunity_ids,
            "duplicate_canonical_keys": duplicate_canonical_keys,
            "duplicate_external_keys": [list(value) for value in duplicate_external_keys],
        },
        "import_gate": {
            "allowed": import_allowed,
            "reason": "quality_contract_passed" if import_allowed else "catalog_identity_or_activity_evidence_failed",
            "does_not_prove_global_coverage": True,
        },
        "claim_boundaries": {
            "global_full_coverage_claim_allowed": False,
            "unknown_viltrox_presence_counted_as_covered": False,
            "event_listing_proves_viltrox_participation": False,
            "event_listing_proves_attendance_or_sales": False,
        },
        "issue_counts": counts,
        "issues": sorted(issues, key=lambda item: (item["severity"], item["code"], item["path"])),
    }
