"""Opportunity-row validation for the Event catalog quality contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Collection, Mapping

from app.domains.events.radar_quality_core import (
    _COUNTRY_RE,
    _POSITIVE_VILTROX_STATUSES,
    _SAFE_REVIEWER_ID_RE,
    _STABLE_LOCATION_RE,
    _UNSUPPORTED_POSITIVE_CLAIMS,
    _evidence_contract_valid,
    _evidence_covered,
    _freshness,
    _is_https_url,
    _positive_claim,
    _review_status,
)
from app.domains.events.radar_quality_event_sources import (
    EVENT_DATE_PRECISIONS,
    EVENT_LANES,
    EVENT_SOURCE_KINDS,
    EVENT_STATUSES,
    LOCAL_ACTIVITY_SOURCE_KINDS,
    parse_calendar_date,
    valid_iana_timezone,
)


Issue = Callable[[str, str, str, str], None]


@dataclass(frozen=True)
class OpportunityIdentity:
    opportunity_id: str
    canonical_key: str
    source_id: str
    external_key: str
    source_row: Mapping[str, Any]
    source_kind: str
    source_actionable: bool


@dataclass(frozen=True)
class LaneAudit:
    lane: str
    source_valid: bool


@dataclass(frozen=True)
class ActivityDomainAudit:
    valid: bool


@dataclass(frozen=True)
class ActivityEvidenceAudit:
    official_url: Any
    freshness: Mapping[str, Any]
    contract_valid: bool
    import_eligible: bool
    url_present: int
    evidence_count: int


@dataclass(frozen=True)
class DealerAssociationAudit:
    dealer_location_key: str
    exact_location_key: str | None
    association_status: str
    local_count: int
    declared_count: int
    exact_count: int
    name_hint_count: int


@dataclass
class EventOpportunityAudit:
    rows: list[Any]
    opportunity_ids: list[str] = field(default_factory=list)
    canonical_keys: list[str] = field(default_factory=list)
    external_keys: list[tuple[str, str]] = field(default_factory=list)
    activity_url_presence_count: int = 0
    activity_evidence_count: int = 0
    viltrox_evidence_count: int = 0
    exact_dealer_links: int = 0
    declared_dealer_links: int = 0
    name_only_dealer_hints: int = 0
    dealer_local_count: int = 0
    evidence_records: list[dict[str, Any]] = field(default_factory=list)


def _invalid_opportunity_record(index: int) -> dict[str, Any]:
    return {
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


def _audit_opportunity_identity(
    raw: Mapping[str, Any],
    *,
    path: str,
    source_by_id: Mapping[str, dict[str, Any]],
    issue: Issue,
) -> OpportunityIdentity:
    opportunity_id = str(raw.get("id") or "").strip()
    canonical_key = str(raw.get("canonical_key") or "").strip()
    source_id = str(raw.get("source_id") or "").strip()
    external_key = str(raw.get("external_event_key") or "").strip()
    if not opportunity_id:
        issue(
            "error", "event.opportunity_id_missing", f"{path}.id",
            "stable opportunity id is required",
        )
    if not canonical_key:
        issue(
            "error", "event.canonical_key_missing", f"{path}.canonical_key",
            "canonical entity key is required",
        )
    if source_id not in source_by_id:
        issue(
            "error", "event.source_orphan", f"{path}.source_id",
            "opportunity source id is unknown",
        )
    if not external_key:
        issue(
            "error", "event.external_event_key_missing", f"{path}.external_event_key",
            "source event key is required",
        )
    source_row = source_by_id.get(source_id) or {}
    source_status = str(source_row.get("status") or "unknown").strip().casefold()
    source_enabled_value = source_row.get("enabled")
    source_enabled = source_enabled_value is None or source_enabled_value is True
    source_kind = str(source_row.get("source_kind") or "").strip().casefold()
    source_actionable = bool(
        source_row
        and source_status == "active"
        and source_enabled
        and source_kind in EVENT_SOURCE_KINDS
    )
    if source_row and not source_actionable:
        issue(
            "error", "event.nonactive_source_opportunity", f"{path}.source_id",
            "only an enabled active source with a supported source_kind may emit opportunities",
        )
    return OpportunityIdentity(
        opportunity_id=opportunity_id,
        canonical_key=canonical_key,
        source_id=source_id,
        external_key=external_key,
        source_row=source_row,
        source_kind=source_kind,
        source_actionable=source_actionable,
    )


def _audit_opportunity_lane(
    raw: Mapping[str, Any],
    *,
    path: str,
    identity: OpportunityIdentity,
    issue: Issue,
) -> LaneAudit:
    lane = str(raw.get("lane") or "").strip().casefold()
    lane_valid = lane in EVENT_LANES
    if not lane_valid:
        issue(
            "error", "event.lane_invalid", f"{path}.lane",
            "lane must be major_expo, dealer_event, or local_activity",
        )
    source_valid = bool(
        lane_valid
        and identity.source_row
        and (
            (lane == "major_expo" and identity.source_kind == "major_expo")
            or (lane == "dealer_event" and identity.source_kind == "dealer_event")
            or (
                lane == "local_activity"
                and identity.source_kind in LOCAL_ACTIVITY_SOURCE_KINDS
            )
        )
    )
    if lane_valid and identity.source_row and not source_valid:
        issue(
            "error", "event.lane_source_kind_mismatch", f"{path}.lane",
            "major exhibitions require major_expo sources; dealer events require dealer_event sources; local activities require a supported local calendar source",
        )
    return LaneAudit(lane=lane, source_valid=source_valid)


def _audit_activity_location(
    raw: Mapping[str, Any],
    *,
    path: str,
    identity: OpportunityIdentity,
    issue: Issue,
) -> tuple[bool, bool, bool]:
    country = str(raw.get("country_code") or "").strip()
    country_valid = bool(_COUNTRY_RE.fullmatch(country))
    if not country_valid:
        issue(
            "error", "event.activity_country_invalid", f"{path}.country_code",
            "activity country_code must be uppercase ISO alpha-2",
        )
    timezone_name = str(raw.get("timezone") or "").strip()
    timezone_valid = valid_iana_timezone(timezone_name)
    if not timezone_valid:
        issue(
            "error", "event.activity_timezone_invalid", f"{path}.timezone",
            "activity timezone must be a valid IANA timezone",
        )
    source_country = str(identity.source_row.get("country_code") or "").strip()
    source_timezone = str(identity.source_row.get("timezone") or "").strip()
    source_alignment_valid = bool(
        identity.source_row
        and country_valid
        and timezone_valid
        and country == source_country
        and timezone_name == source_timezone
    )
    if identity.source_row and country != source_country:
        issue(
            "error", "event.activity_source_country_mismatch", f"{path}.country_code",
            "activity country must match its registered source",
        )
    if identity.source_row and timezone_name != source_timezone:
        issue(
            "error", "event.activity_source_timezone_mismatch", f"{path}.timezone",
            "activity timezone must match its registered source",
        )
    return country_valid, timezone_valid, source_alignment_valid


def _audit_activity_status_and_dates(
    raw: Mapping[str, Any], *, path: str, issue: Issue
) -> tuple[bool, bool, bool]:
    event_status = str(raw.get("event_status") or "scheduled").strip().casefold()
    event_status_valid = event_status in EVENT_STATUSES
    if not event_status_valid:
        issue(
            "error", "event.activity_status_invalid", f"{path}.event_status",
            "unsupported activity event_status",
        )
    date_precision = str(raw.get("date_precision") or "date").strip().casefold()
    date_precision_valid = date_precision in EVENT_DATE_PRECISIONS
    if not date_precision_valid:
        issue(
            "error", "event.activity_date_precision_invalid", f"{path}.date_precision",
            "unsupported activity date_precision",
        )
    start_date = parse_calendar_date(raw.get("start_date"))
    end_date = parse_calendar_date(raw.get("end_date"))
    dates_valid = True
    if raw.get("start_date") not in (None, "") and start_date is None:
        dates_valid = False
        issue(
            "error", "event.activity_start_date_invalid", f"{path}.start_date",
            "activity start_date must be canonical YYYY-MM-DD",
        )
    if raw.get("end_date") not in (None, "") and end_date is None:
        dates_valid = False
        issue(
            "error", "event.activity_end_date_invalid", f"{path}.end_date",
            "activity end_date must be canonical YYYY-MM-DD",
        )
    verification_status = str(raw.get("verification_status") or "").strip().casefold()
    if verification_status == "verified" and event_status == "scheduled" and (
        start_date is None or end_date is None
    ):
        dates_valid = False
        issue(
            "error", "event.activity_verified_dates_required", path,
            "verified scheduled activity requires start_date and end_date",
        )
    if start_date is not None and end_date is not None and end_date < start_date:
        dates_valid = False
        issue(
            "error", "event.activity_date_order_invalid", path,
            "activity end_date cannot precede start_date",
        )
    return event_status_valid, date_precision_valid, dates_valid


def _audit_activity_domain(
    raw: Mapping[str, Any],
    *,
    path: str,
    identity: OpportunityIdentity,
    issue: Issue,
) -> ActivityDomainAudit:
    country_valid, timezone_valid, alignment_valid = _audit_activity_location(
        raw, path=path, identity=identity, issue=issue
    )
    status_valid, precision_valid, dates_valid = _audit_activity_status_and_dates(
        raw, path=path, issue=issue
    )
    return ActivityDomainAudit(
        valid=all(
            (
                country_valid, timezone_valid, alignment_valid,
                status_valid, precision_valid, dates_valid,
            )
        )
    )


def _audit_activity_evidence(
    raw: Mapping[str, Any],
    *,
    path: str,
    now: datetime,
    stale_after_days: int,
    identity: OpportunityIdentity,
    lane: LaneAudit,
    domain: ActivityDomainAudit,
    issue: Issue,
) -> ActivityEvidenceAudit:
    official_url = raw.get("official_url")
    url_valid = _is_https_url(official_url)
    if not url_valid:
        issue(
            "error", "event.official_url_invalid", f"{path}.official_url",
            "official HTTPS activity URL is required",
        )
    freshness = _freshness(
        raw.get("source_checked_at"), as_of=now, stale_after_days=stale_after_days
    )
    if freshness["status"] != "fresh":
        issue(
            "error", "event.activity_observed_at_missing_or_stale",
            f"{path}.source_checked_at",
            "each opportunity requires its own current source_checked_at; source-row freshness is not an observation substitute",
        )
    contract_valid = _evidence_contract_valid(
        raw, expected_scope="event_official_listing"
    )
    if not contract_valid:
        issue(
            "error", "event.activity_evidence_contract_invalid", f"{path}.reviewer_id",
            "Event activity evidence requires safe reviewer_id, evidence_scope=event_official_listing, and value_status=observed",
        )
    import_eligible = all(
        (
            str(raw.get("verification_status") or "").casefold() == "verified",
            url_valid,
            freshness["status"] == "fresh",
            contract_valid,
            identity.source_actionable,
            lane.source_valid,
            domain.valid,
        )
    )
    return ActivityEvidenceAudit(
        official_url=official_url,
        freshness=freshness,
        contract_valid=contract_valid,
        import_eligible=import_eligible,
        url_present=int(url_valid),
        evidence_count=int(import_eligible),
    )


def _audit_viltrox_presence(
    raw: Mapping[str, Any],
    *,
    path: str,
    catalog_checked_at: Any,
    now: datetime,
    stale_after_days: int,
    issue: Issue,
) -> int:
    presence = str(raw.get("viltrox_presence_status") or "unknown").strip().casefold()
    if presence in _POSITIVE_VILTROX_STATUSES | {"not_found"}:
        evidence = raw.get("viltrox_evidence")
        if not isinstance(evidence, dict):
            evidence = {
                "status": "verified",
                "source_url": raw.get("viltrox_evidence_url"),
                "checked_at": raw.get("source_checked_at") or catalog_checked_at,
                "reviewer_id": raw.get("viltrox_reviewer_id"),
                "evidence_scope": raw.get("viltrox_evidence_scope"),
                "value_status": raw.get("viltrox_value_status"),
            }
        covered = _evidence_covered(
            evidence,
            as_of=now,
            stale_after_days=stale_after_days,
            expected_scope="event_viltrox_presence",
            allowed_value_statuses=(
                {"not_found"} if presence == "not_found" else {"observed"}
            ),
        )
        if covered:
            return 1
        issue(
            "error", "event.viltrox_presence_without_evidence",
            f"{path}.viltrox_presence_status",
            "Viltrox presence observation needs separate current scoped evidence",
        )
        return 0
    if presence != "unknown":
        issue(
            "error", "event.viltrox_presence_status_invalid",
            f"{path}.viltrox_presence_status", "unsupported Viltrox presence status",
        )
    return 0


def _association_status(
    *, applicable: bool, exact_key: str | None,
    declared_key: str | None, name_hint: str,
) -> str:
    if not applicable:
        return "not_applicable"
    if exact_key:
        return "exact_reviewed_location"
    if declared_key:
        return "declared_exact_key_unresolved"
    if name_hint:
        return "name_hint_only_not_linked"
    return "unlinked_no_fuzzy_match"


def _audit_dealer_association(
    raw: Mapping[str, Any],
    *,
    path: str,
    identity: OpportunityIdentity,
    lane: LaneAudit,
    reviewed_locations: Collection[str] | None,
    issue: Issue,
) -> DealerAssociationAudit:
    dealer_location_key = str(raw.get("dealer_stable_location_key") or "").strip()
    dealer_match_name = str(raw.get("dealer_match_name") or "").strip()
    applicable = bool(
        lane.lane == "dealer_event"
        or (lane.lane == "local_activity" and identity.source_kind == "dealer_event")
    )
    declared_count = 0
    exact_count = 0
    name_hint_count = 0
    if applicable:
        if dealer_location_key and _STABLE_LOCATION_RE.fullmatch(dealer_location_key):
            declared_count = 1
            if reviewed_locations is not None and dealer_location_key in reviewed_locations:
                exact_count = 1
            elif reviewed_locations is not None:
                issue(
                    "error", "event.dealer_location_key_unresolved",
                    f"{path}.dealer_stable_location_key",
                    "dealer_stable_location_key is absent from the supplied reviewed Dealer universe",
                )
        elif dealer_location_key:
            issue(
                "error", "event.dealer_location_key_invalid",
                f"{path}.dealer_stable_location_key",
                "dealer_stable_location_key must satisfy the exact Dealer location identity contract",
            )
        elif dealer_match_name:
            name_hint_count = 1
    elif dealer_location_key or dealer_match_name:
        issue(
            "error", "event.dealer_link_wrong_lane",
            f"{path}.dealer_stable_location_key",
            "Dealer identity links are only valid for dealer-backed dealer_event or local_activity opportunities",
        )
    declared_location_key = (
        dealer_location_key
        if applicable and _STABLE_LOCATION_RE.fullmatch(dealer_location_key)
        else None
    )
    exact_location_key = (
        declared_location_key
        if declared_location_key in (reviewed_locations or ())
        else None
    )
    return DealerAssociationAudit(
        dealer_location_key=dealer_location_key,
        exact_location_key=exact_location_key,
        association_status=_association_status(
            applicable=applicable,
            exact_key=exact_location_key,
            declared_key=declared_location_key,
            name_hint=dealer_match_name,
        ),
        local_count=int(applicable),
        declared_count=declared_count,
        exact_count=exact_count,
        name_hint_count=name_hint_count,
    )


def _opportunity_evidence_record(
    raw: Mapping[str, Any],
    *,
    index: int,
    identity: OpportunityIdentity,
    activity: ActivityEvidenceAudit,
    dealer: DealerAssociationAudit,
) -> dict[str, Any]:
    reviewer_id = str(raw.get("reviewer_id") or "").strip()
    verification_status = str(raw.get("verification_status") or "").strip().casefold()
    return {
        "opportunity_index": index,
        "opportunity_id": identity.opportunity_id or None,
        "source_id": identity.source_id or None,
        "source_url": (
            str(activity.official_url).strip()
            if _is_https_url(activity.official_url)
            else None
        ),
        "observed_at": activity.freshness["checked_at"],
        "declared_review_status": verification_status or None,
        "review_status": _review_status(
            declared_reviewed=verification_status == "verified",
            evidence_contract_valid=activity.contract_valid,
            freshness_status=str(activity.freshness["status"]),
        ),
        "reviewer_id": reviewer_id if _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id) else None,
        "evidence_scope": str(raw.get("evidence_scope") or "").strip() or None,
        "value_status": str(raw.get("value_status") or "").strip().casefold() or None,
        "declared_stable_location_key": dealer.dealer_location_key or None,
        "exact_stable_location_key": dealer.exact_location_key,
        "association_status": dealer.association_status,
        "import_eligible": activity.import_eligible,
    }


def _audit_unsupported_claims(
    raw: Mapping[str, Any], *, path: str, issue: Issue
) -> None:
    for field_name in sorted(_UNSUPPORTED_POSITIVE_CLAIMS):
        if field_name in raw and _positive_claim(raw.get(field_name)):
            issue(
                "error", "event.unsupported_business_claim", f"{path}.{field_name}",
                f"unsupported positive business claim: {field_name}",
            )


def _audit_opportunity_row(
    raw: dict[str, Any],
    *,
    index: int,
    source_by_id: Mapping[str, dict[str, Any]],
    reviewed_locations: Collection[str] | None,
    catalog_checked_at: Any,
    now: datetime,
    stale_after_days: int,
    issue: Issue,
) -> tuple[
    OpportunityIdentity, ActivityEvidenceAudit,
    DealerAssociationAudit, int, dict[str, Any],
]:
    path = f"opportunities[{index}]"
    identity = _audit_opportunity_identity(
        raw, path=path, source_by_id=source_by_id, issue=issue
    )
    lane = _audit_opportunity_lane(raw, path=path, identity=identity, issue=issue)
    domain = _audit_activity_domain(raw, path=path, identity=identity, issue=issue)
    activity = _audit_activity_evidence(
        raw,
        path=path,
        now=now,
        stale_after_days=stale_after_days,
        identity=identity,
        lane=lane,
        domain=domain,
        issue=issue,
    )
    viltrox_count = _audit_viltrox_presence(
        raw,
        path=path,
        catalog_checked_at=catalog_checked_at,
        now=now,
        stale_after_days=stale_after_days,
        issue=issue,
    )
    dealer = _audit_dealer_association(
        raw,
        path=path,
        identity=identity,
        lane=lane,
        reviewed_locations=reviewed_locations,
        issue=issue,
    )
    record = _opportunity_evidence_record(
        raw, index=index, identity=identity, activity=activity, dealer=dealer
    )
    _audit_unsupported_claims(raw, path=path, issue=issue)
    return identity, activity, dealer, viltrox_count, record


def audit_event_opportunities(
    rows: list[Any],
    *,
    source_by_id: Mapping[str, dict[str, Any]],
    reviewed_locations: Collection[str] | None,
    catalog_checked_at: Any,
    now: datetime,
    stale_after_days: int,
    issue: Issue,
) -> EventOpportunityAudit:
    result = EventOpportunityAudit(rows=rows)
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            issue(
                "error", "event.opportunity_type", f"opportunities[{index}]",
                "opportunity must be an object",
            )
            result.evidence_records.append(_invalid_opportunity_record(index))
            continue
        identity, activity, dealer, viltrox_count, record = _audit_opportunity_row(
            raw,
            index=index,
            source_by_id=source_by_id,
            reviewed_locations=reviewed_locations,
            catalog_checked_at=catalog_checked_at,
            now=now,
            stale_after_days=stale_after_days,
            issue=issue,
        )
        result.opportunity_ids.append(identity.opportunity_id)
        result.canonical_keys.append(identity.canonical_key)
        result.external_keys.append((identity.source_id, identity.external_key))
        result.activity_url_presence_count += activity.url_present
        result.activity_evidence_count += activity.evidence_count
        result.viltrox_evidence_count += viltrox_count
        result.dealer_local_count += dealer.local_count
        result.declared_dealer_links += dealer.declared_count
        result.exact_dealer_links += dealer.exact_count
        result.name_only_dealer_hints += dealer.name_hint_count
        result.evidence_records.append(record)
    return result
