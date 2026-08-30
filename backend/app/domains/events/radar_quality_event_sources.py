"""Source-row validation for the Event catalog quality contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domains.events.radar_quality_core import (
    _COUNTRY_RE,
    _NONACTIVE_SOURCE_STATUSES,
    _SAFE_REVIEWER_ID_RE,
    _SOURCE_ID_RE,
    _evidence_contract_valid,
    _freshness,
    _is_https_url,
    _review_status,
)


Issue = Callable[[str, str, str, str], None]

LOCAL_ACTIVITY_SOURCE_KINDS = {
    "dealer_event",
    "venue_calendar",
    "school_calendar",
    "university_calendar",
    "photo_club",
    "community_calendar",
    "brand_event",
}
EVENT_SOURCE_KINDS = {"major_expo", *LOCAL_ACTIVITY_SOURCE_KINDS}
EVENT_SOURCE_STATUSES = {"active", *_NONACTIVE_SOURCE_STATUSES}
EVENT_LANES = {"major_expo", "dealer_event", "local_activity"}
EVENT_STATUSES = {"scheduled", "postponed", "cancelled", "ended", "unknown"}
EVENT_DATE_PRECISIONS = {"date", "date_time", "month_only", "tbd"}


def valid_iana_timezone(value: Any) -> bool:
    name = str(value or "").strip()
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def parse_calendar_date(value: Any) -> date | None:
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
    return parsed if text == parsed.isoformat() else None


@dataclass(frozen=True)
class SourceRowAudit:
    source_id: str
    source_url: str
    source_kind: str
    status: str
    active: bool
    fresh: bool
    evidence_valid: bool
    active_fresh: bool
    stable_id_valid: bool
    evidence_record: dict[str, Any]


@dataclass
class EventSourceAudit:
    rows: list[Any]
    source_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    source_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    fresh_count: int = 0
    review_evidence_count: int = 0
    active_count: int = 0
    active_fresh_ids: set[str] = field(default_factory=set)
    evidence_records: list[dict[str, Any]] = field(default_factory=list)


def _invalid_source_record(index: int) -> dict[str, Any]:
    return {
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


def _source_identity_and_location(
    raw: Mapping[str, Any], *, path: str, issue: Issue
) -> tuple[str, str, str, bool, bool, bool]:
    source_id = str(raw.get("id") or "").strip()
    source_url = str(raw.get("canonical_url") or "").strip()
    stable_id_valid = bool(_SOURCE_ID_RE.fullmatch(source_id))
    if not stable_id_valid:
        issue(
            "error", "event.source_id_missing_or_invalid", f"{path}.id",
            "stable source id is required",
        )
    if not _is_https_url(source_url):
        issue(
            "error", "event.source_url_invalid", f"{path}.canonical_url",
            "credential-free HTTPS source URL is required",
        )
    source_kind = str(raw.get("source_kind") or "").strip().casefold()
    if source_kind not in EVENT_SOURCE_KINDS:
        issue(
            "error", "event.source_kind_invalid", f"{path}.source_kind",
            "source_kind must be one of the supported exhibition, dealer, venue, school, university, photo-club, community, or brand calendars",
        )
    source_country = str(raw.get("country_code") or "").strip()
    source_country_valid = bool(_COUNTRY_RE.fullmatch(source_country))
    if not source_country_valid:
        issue(
            "error", "event.source_country_invalid", f"{path}.country_code",
            "source country_code must be uppercase ISO alpha-2",
        )
    source_timezone = str(raw.get("timezone") or "").strip()
    source_timezone_valid = valid_iana_timezone(source_timezone)
    if not source_timezone_valid:
        issue(
            "error", "event.source_timezone_invalid", f"{path}.timezone",
            "source timezone must be a valid IANA timezone",
        )
    return (
        source_id, source_url, source_kind, stable_id_valid,
        source_country_valid, source_timezone_valid,
    )


def _source_operational_status(
    raw: Mapping[str, Any], *, path: str, issue: Issue
) -> tuple[str, bool, bool]:
    status = str(raw.get("status") or "unknown").strip().casefold()
    if status not in EVENT_SOURCE_STATUSES:
        issue(
            "error", "event.source_status_invalid", f"{path}.status",
            "source status must be active, hold, blocked, or retired",
        )
    enabled_value = raw.get("enabled")
    if enabled_value is not None and not isinstance(enabled_value, bool):
        issue(
            "error", "event.source_enabled_invalid", f"{path}.enabled",
            "enabled must be a boolean when present",
        )
    source_enabled = enabled_value is None or enabled_value is True
    if status == "active" and not source_enabled:
        issue(
            "error", "event.active_source_disabled", f"{path}.enabled",
            "an active source cannot be explicitly disabled",
        )
    if status in _NONACTIVE_SOURCE_STATUSES and raw.get("enabled") is True:
        issue(
            "error", "event.nonactive_source_enabled", f"{path}.enabled",
            "non-active source cannot be enabled",
        )
    return status, source_enabled, status == "active" and source_enabled


def _audit_source_row(
    raw: dict[str, Any],
    *,
    index: int,
    now: datetime,
    stale_after_days: int,
    issue: Issue,
) -> SourceRowAudit:
    path = f"sources[{index}]"
    (
        source_id, source_url, source_kind, stable_id_valid,
        source_country_valid, source_timezone_valid,
    ) = _source_identity_and_location(raw, path=path, issue=issue)
    status, source_enabled, active = _source_operational_status(
        raw, path=path, issue=issue
    )
    source_freshness = _freshness(
        raw.get("source_checked_at"), as_of=now, stale_after_days=stale_after_days
    )
    source_contract_valid = _evidence_contract_valid(
        raw, expected_scope="event_source_listing"
    )
    if not source_contract_valid:
        issue(
            "error", "event.source_evidence_contract_invalid", f"{path}.reviewer_id",
            "Event source evidence requires safe reviewer_id, evidence_scope=event_source_listing, and value_status=observed",
        )
    fresh = source_freshness["status"] == "fresh"
    active_fresh = bool(
        fresh
        and status == "active"
        and source_enabled
        and source_kind in EVENT_SOURCE_KINDS
        and stable_id_valid
        and _is_https_url(source_url)
        and source_country_valid
        and source_timezone_valid
        and source_contract_valid
    )
    reviewer_id = str(raw.get("reviewer_id") or "").strip()
    record = {
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
        "reviewer_id": reviewer_id if _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id) else None,
        "evidence_scope": str(raw.get("evidence_scope") or "").strip() or None,
        "value_status": str(raw.get("value_status") or "").strip().casefold() or None,
        "operational_status": status,
        "import_eligible": active_fresh,
    }
    return SourceRowAudit(
        source_id=source_id,
        source_url=source_url,
        source_kind=source_kind,
        status=status,
        active=active,
        fresh=fresh,
        evidence_valid=source_contract_valid,
        active_fresh=active_fresh,
        stable_id_valid=stable_id_valid,
        evidence_record=record,
    )


def audit_event_sources(
    rows: list[Any],
    *,
    now: datetime,
    stale_after_days: int,
    issue: Issue,
) -> EventSourceAudit:
    result = EventSourceAudit(rows=rows)
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            issue(
                "error", "event.source_type", f"sources[{index}]",
                "source must be an object",
            )
            result.evidence_records.append(_invalid_source_record(index))
            continue
        row = _audit_source_row(
            raw, index=index, now=now,
            stale_after_days=stale_after_days, issue=issue,
        )
        result.source_ids.append(row.source_id)
        result.source_urls.append(row.source_url)
        if row.stable_id_valid and row.source_id not in result.source_by_id:
            result.source_by_id[row.source_id] = raw
        result.fresh_count += int(row.fresh)
        result.review_evidence_count += int(row.evidence_valid)
        result.active_count += int(row.active)
        if row.active_fresh:
            result.active_fresh_ids.add(row.source_id)
        result.evidence_records.append(row.evidence_record)
    return result
