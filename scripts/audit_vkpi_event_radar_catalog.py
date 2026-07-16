#!/usr/bin/env python3
"""Read-only coverage/quality audit for V-KPI Event Radar and Dealer candidates.

The audit is deliberately offline: it validates the Event catalog, parses the
Dealer candidate literal without importing application code, and reports
coverage numerators separately from unavailable global denominators.  URL shape
never claims that a page is currently reachable or that a publisher is an
authorized Viltrox dealer.  It never writes PostgreSQL or calls import endpoints.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from scripts.ops.event_radar_audit_common import (
    ALLOWED_DATE_PRECISIONS, ALLOWED_EVENT_STATUSES, ALLOWED_EVIDENCE_GRADES,
    ALLOWED_LANES, ALLOWED_SOURCE_KINDS, ALLOWED_SOURCE_STATUSES,
    ALLOWED_VERIFICATION_STATUSES, ALLOWED_VILTROX_PRESENCE,
    COVERAGE_CONTRACT_ID, COVERAGE_CONTRACT_VERSION, DEALER_CONTACT_FIELDS,
    DEALER_NON_EQUIVALENT_FACTS, DEALER_POSITIVE_CLAIM_FIELDS, DEFAULT_CATALOG,
    DEFAULT_DEALER_SOURCE, DEFAULT_STALE_AFTER_DAYS, NON_ACTIONABLE_SOURCE_STATUSES,
    UNKNOWN_CLAIM_VALUES, UNSUPPORTED_BUSINESS_CLAIM_FIELDS, as_utc as _as_utc,
    candidate_key as _candidate_key, claim_is_inferred as _claim_is_inferred,
    dealer_domain as _dealer_domain, host as _host, is_https_url as _is_https_url,
    load_reviewed_dealer_candidates, normalized_text as _normalized_text,
    parse_checked_at as _parse_checked_at, parse_date as _parse_date,
    ratio as _ratio, related_hosts as _related_hosts,
)


def audit_catalog(data: dict[str, Any], *, catalog_path: str = "<memory>") -> dict[str, Any]:
    """Return deterministic, JSON-serializable catalog quality evidence."""
    payload = deepcopy(data)
    issues: list[dict[str, str]] = []

    def issue(severity: str, code: str, path: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "path": path, "message": message})

    sources = payload.get("sources")
    opportunities = payload.get("opportunities")
    if not isinstance(sources, list):
        issue("error", "catalog.sources_type", "sources", "sources must be an array")
        sources = []
    if not isinstance(opportunities, list):
        issue("error", "catalog.opportunities_type", "opportunities", "opportunities must be an array")
        opportunities = []

    coverage_claim = payload.get("coverage_claim")
    global_complete = payload.get("global_complete")
    if coverage_claim != "registered_publisher_owned_public_entries_only":
        issue(
            "error",
            "catalog.coverage_claim",
            "coverage_claim",
            "coverage_claim must remain registered_publisher_owned_public_entries_only",
        )
    if global_complete is not False:
        issue(
            "error",
            "catalog.global_complete",
            "global_complete",
            "global_complete must be explicitly false",
        )
    truth_note = str(payload.get("truth_note") or "")
    truth_lower = truth_note.lower()
    if "not every" not in truth_lower or "separate facts" not in truth_lower:
        issue(
            "error",
            "catalog.truth_note",
            "truth_note",
            "truth_note must state incomplete coverage and separate commercial facts",
        )

    checked_at = _parse_checked_at(payload.get("checked_at"))
    if checked_at is None:
        issue("error", "catalog.checked_at", "checked_at", "checked_at must be an ISO timestamp with timezone")
    checked_date = checked_at.date() if checked_at is not None else None

    source_ids: list[str] = []
    source_urls: list[str] = []
    source_by_id: dict[str, dict[str, Any]] = {}
    for index, raw_source in enumerate(sources):
        path = f"sources[{index}]"
        if not isinstance(raw_source, dict):
            issue("error", "source.type", path, "source must be an object")
            continue
        source = raw_source
        source_id = str(source.get("id") or "").strip()
        source_ids.append(source_id)
        if not source_id:
            issue("error", "source.id_missing", f"{path}.id", "source id is required")
        elif source_id not in source_by_id:
            source_by_id[source_id] = source

        source_kind = str(source.get("source_kind") or "")
        if source_kind not in ALLOWED_SOURCE_KINDS:
            issue("error", "source.source_kind", f"{path}.source_kind", f"unsupported source_kind: {source_kind!r}")
        status = str(source.get("status") or "")
        if status not in ALLOWED_SOURCE_STATUSES:
            issue("error", "source.status", f"{path}.status", f"unsupported source status: {status!r}")
        if status in NON_ACTIONABLE_SOURCE_STATUSES and source.get("enabled") is True:
            issue("error", "source.nonactive_enabled", f"{path}.enabled", f"{status} source cannot be enabled")

        country = str(source.get("country_code") or "")
        if not re.fullmatch(r"[A-Z]{2}", country):
            issue("error", "source.country_code", f"{path}.country_code", "country_code must be uppercase ISO alpha-2")
        timezone_name = str(source.get("timezone") or "")
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            issue("error", "source.timezone", f"{path}.timezone", f"invalid IANA timezone: {timezone_name!r}")

        canonical_url = str(source.get("canonical_url") or "").strip()
        source_urls.append(canonical_url)
        if not _is_https_url(canonical_url):
            issue("error", "source.canonical_url", f"{path}.canonical_url", "canonical_url must be a credential-free HTTPS URL")
        discovery_url = str(source.get("discovery_url") or "").strip()
        if discovery_url and not _is_https_url(discovery_url):
            issue("error", "source.discovery_url", f"{path}.discovery_url", "discovery_url must be HTTPS when present")

        evidence_grade = str(source.get("evidence_grade") or "")
        if evidence_grade not in ALLOWED_EVIDENCE_GRADES:
            issue("error", "source.evidence_grade", f"{path}.evidence_grade", f"unsupported evidence grade: {evidence_grade!r}")
        if status in {"blocked", "retired"} and evidence_grade != "X":
            issue("warning", "source.nonactive_grade", f"{path}.evidence_grade", f"{status} source should normally use evidence grade X")

    duplicate_source_ids = sorted(key for key, count in Counter(source_ids).items() if key and count > 1)
    for source_id in duplicate_source_ids:
        issue("error", "source.id_duplicate", "sources", f"duplicate source id: {source_id}")
    duplicate_source_urls = sorted(key for key, count in Counter(source_urls).items() if key and count > 1)
    for url in duplicate_source_urls:
        issue("error", "source.canonical_url_duplicate", "sources", f"duplicate source canonical_url: {url}")

    opportunity_ids: list[str] = []
    canonical_keys: list[str] = []
    external_keys: list[tuple[str, str]] = []
    dealer_candidates = 0
    dealer_matches = 0
    dealer_missing: list[str] = []
    actionable_by_source: Counter[str] = Counter()
    for index, raw_opportunity in enumerate(opportunities):
        path = f"opportunities[{index}]"
        if not isinstance(raw_opportunity, dict):
            issue("error", "opportunity.type", path, "opportunity must be an object")
            continue
        opportunity = raw_opportunity
        opportunity_id = str(opportunity.get("id") or "").strip()
        opportunity_ids.append(opportunity_id)
        if not opportunity_id:
            issue("error", "opportunity.id_missing", f"{path}.id", "opportunity id is required")
        canonical_key = str(opportunity.get("canonical_key") or "").strip()
        canonical_keys.append(canonical_key)
        if not canonical_key:
            issue("error", "opportunity.canonical_key_missing", f"{path}.canonical_key", "canonical_key is required")

        source_id = str(opportunity.get("source_id") or "").strip()
        source = source_by_id.get(source_id)
        if source is None:
            issue("error", "opportunity.source_orphan", f"{path}.source_id", f"unknown source_id: {source_id!r}")
        else:
            source_status = str(source.get("status") or "")
            if source_status in NON_ACTIONABLE_SOURCE_STATUSES:
                issue(
                    "error",
                    "opportunity.nonactive_source",
                    f"{path}.source_id",
                    f"opportunity cannot be emitted from {source_status} source {source_id}",
                )
            actionable_by_source[source_id] += 1

        external_event_key = str(opportunity.get("external_event_key") or "").strip()
        if not external_event_key:
            issue("error", "opportunity.external_event_key_missing", f"{path}.external_event_key", "external_event_key is required")
        external_keys.append((source_id, external_event_key))

        lane = str(opportunity.get("lane") or "")
        if lane not in ALLOWED_LANES:
            issue("error", "opportunity.lane", f"{path}.lane", f"unsupported lane: {lane!r}")
        if source is not None:
            source_kind = str(source.get("source_kind") or "")
            if lane == "major_expo" and source_kind != "major_expo":
                issue("error", "opportunity.lane_source_kind", f"{path}.lane", "major_expo must reference a major_expo source")
            if lane in {"dealer_event", "local_activity"} and source_kind != "dealer_event":
                issue("error", "opportunity.lane_source_kind", f"{path}.lane", f"{lane} must reference a dealer_event source")

        event_status = str(opportunity.get("event_status") or "")
        if event_status not in ALLOWED_EVENT_STATUSES:
            issue("error", "opportunity.event_status", f"{path}.event_status", f"unsupported event_status: {event_status!r}")
        verification_status = str(opportunity.get("verification_status") or "")
        if verification_status not in ALLOWED_VERIFICATION_STATUSES:
            issue("error", "opportunity.verification_status", f"{path}.verification_status", f"unsupported verification_status: {verification_status!r}")
        evidence_grade = str(opportunity.get("evidence_grade") or "")
        if evidence_grade not in ALLOWED_EVIDENCE_GRADES:
            issue("error", "opportunity.evidence_grade", f"{path}.evidence_grade", f"unsupported evidence grade: {evidence_grade!r}")
        date_precision = str(opportunity.get("date_precision") or "date")
        if date_precision not in ALLOWED_DATE_PRECISIONS:
            issue("error", "opportunity.date_precision", f"{path}.date_precision", f"unsupported date_precision: {date_precision!r}")

        country = str(opportunity.get("country_code") or "")
        if not re.fullmatch(r"[A-Z]{2}", country):
            issue("error", "opportunity.country_code", f"{path}.country_code", "country_code must be uppercase ISO alpha-2")
        timezone_name = str(opportunity.get("timezone") or "")
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            issue("error", "opportunity.timezone", f"{path}.timezone", f"invalid IANA timezone: {timezone_name!r}")
        if source is not None and country != str(source.get("country_code") or ""):
            issue("error", "opportunity.source_country_mismatch", f"{path}.country_code", "opportunity country must match its source")
        if source is not None and timezone_name != str(source.get("timezone") or ""):
            issue("error", "opportunity.source_timezone_mismatch", f"{path}.timezone", "opportunity timezone must match its source")

        start_date = _parse_date(opportunity.get("start_date"))
        end_date = _parse_date(opportunity.get("end_date"))
        if opportunity.get("start_date") and start_date is None:
            issue("error", "opportunity.start_date", f"{path}.start_date", "start_date must be YYYY-MM-DD")
        if opportunity.get("end_date") and end_date is None:
            issue("error", "opportunity.end_date", f"{path}.end_date", "end_date must be YYYY-MM-DD")
        if start_date is not None and end_date is not None and end_date < start_date:
            issue("error", "opportunity.date_order", path, "end_date cannot precede start_date")
        if event_status == "scheduled" and verification_status == "verified" and (start_date is None or end_date is None):
            issue("error", "opportunity.verified_dates", path, "verified scheduled opportunity requires start_date and end_date")
        if checked_date is not None and event_status == "scheduled" and end_date is not None and end_date < checked_date:
            issue("error", "opportunity.past_scheduled", f"{path}.end_date", "scheduled opportunity already ended before catalog checked_at")
        if start_date is not None and end_date is not None and (end_date - start_date).days > 31:
            issue("warning", "opportunity.long_duration", path, "event duration exceeds 31 days and needs review")

        official_url = str(opportunity.get("official_url") or "").strip()
        if not _is_https_url(official_url):
            issue("error", "opportunity.official_url", f"{path}.official_url", "official_url must be a credential-free HTTPS URL")
        if source is not None:
            source_urls_for_match = [source.get("canonical_url"), source.get("discovery_url")]
            if not any(_related_hosts(official_url, source_url) for source_url in source_urls_for_match if source_url):
                issue("error", "opportunity.official_host", f"{path}.official_url", "official_url host must match the reviewed source host")
        registration_url = str(opportunity.get("registration_url") or "").strip()
        if registration_url and not _is_https_url(registration_url):
            issue("error", "opportunity.registration_url", f"{path}.registration_url", "registration_url must be HTTPS when present")

        presence = str(opportunity.get("viltrox_presence_status") or "unknown")
        evidence_url = str(opportunity.get("viltrox_evidence_url") or "").strip()
        if presence not in ALLOWED_VILTROX_PRESENCE:
            issue("error", "opportunity.viltrox_presence_status", f"{path}.viltrox_presence_status", f"unsupported Viltrox presence status: {presence!r}")
        if presence in {"brand_listed", "confirmed_exhibitor"} and not _is_https_url(evidence_url):
            issue("error", "opportunity.viltrox_presence_evidence", f"{path}.viltrox_evidence_url", "positive Viltrox presence requires a separate HTTPS evidence URL")
        if evidence_url and presence not in {"brand_listed", "confirmed_exhibitor"}:
            issue("error", "opportunity.viltrox_presence_evidence_state", f"{path}.viltrox_evidence_url", "Viltrox evidence URL requires an explicit positive evidence state")

        for field in UNSUPPORTED_BUSINESS_CLAIM_FIELDS:
            if field in opportunity and _claim_is_inferred(opportunity.get(field)):
                issue(
                    "error",
                    "opportunity.unsupported_business_claim",
                    f"{path}.{field}",
                    f"public activity source cannot infer {field}",
                )

        if lane in {"dealer_event", "local_activity"}:
            dealer_candidates += 1
            if str(opportunity.get("dealer_match_name") or "").strip():
                dealer_matches += 1
            else:
                dealer_missing.append(opportunity_id or f"index:{index}")
                issue(
                    "warning",
                    "opportunity.dealer_match_missing",
                    f"{path}.dealer_match_name",
                    "dealer/local opportunity has no reviewed Dealer entity match yet",
                )
        elif opportunity.get("dealer_match_name"):
            issue("error", "opportunity.dealer_match_lane", f"{path}.dealer_match_name", "dealer match is only valid for dealer/local lanes")

        try:
            confidence = float(opportunity.get("confidence"))
            if not 0 <= confidence <= 1:
                raise ValueError
        except (TypeError, ValueError):
            issue("error", "opportunity.confidence", f"{path}.confidence", "confidence must be between 0 and 1")
        relevance = opportunity.get("relevance_score")
        if relevance is not None:
            try:
                if not 0 <= float(relevance) <= 100:
                    raise ValueError
            except (TypeError, ValueError):
                issue("error", "opportunity.relevance_score", f"{path}.relevance_score", "relevance_score must be between 0 and 100")

    for key in sorted(key for key, count in Counter(opportunity_ids).items() if key and count > 1):
        issue("error", "opportunity.id_duplicate", "opportunities", f"duplicate opportunity id: {key}")
    for key in sorted(key for key, count in Counter(canonical_keys).items() if key and count > 1):
        issue("error", "opportunity.canonical_key_duplicate", "opportunities", f"duplicate canonical_key: {key}")
    for source_id, external_key in sorted(key for key, count in Counter(external_keys).items() if all(key) and count > 1):
        issue(
            "error",
            "opportunity.external_key_duplicate",
            "opportunities",
            f"duplicate (source_id, external_event_key): ({source_id}, {external_key})",
        )

    for source_id, source in source_by_id.items():
        status = str(source.get("status") or "")
        if status in NON_ACTIONABLE_SOURCE_STATUSES and actionable_by_source[source_id] > 0:
            issue(
                "error",
                "source.nonactive_has_opportunities",
                f"sources[{source_id}]",
                f"{status} source emits {actionable_by_source[source_id]} actionable opportunities",
            )

    severity_counts = Counter(item["severity"] for item in issues)
    source_kind_counts = Counter(str(item.get("source_kind") or "unknown") for item in sources if isinstance(item, dict))
    source_status_counts = Counter(str(item.get("status") or "unknown") for item in sources if isinstance(item, dict))
    lane_counts = Counter(str(item.get("lane") or "unknown") for item in opportunities if isinstance(item, dict))
    return {
        "ok": severity_counts["error"] == 0,
        "read_only": True,
        "catalog_path": catalog_path,
        "catalog_version": payload.get("catalog_version"),
        "checked_at": payload.get("checked_at"),
        "coverage": {
            "claim": coverage_claim,
            "global_complete": global_complete,
        },
        "counts": {
            "sources": len(sources),
            "opportunities": len(opportunities),
            "countries": len({str(item.get("country_code")) for item in sources if isinstance(item, dict) and item.get("country_code")}),
            "source_kinds": dict(sorted(source_kind_counts.items())),
            "source_statuses": dict(sorted(source_status_counts.items())),
            "lanes": dict(sorted(lane_counts.items())),
        },
        "dealer_linkage": {
            "candidate_opportunities": dealer_candidates,
            "matched_by_name": dealer_matches,
            "unmatched": len(dealer_missing),
            "coverage_rate": round(dealer_matches / dealer_candidates, 4) if dealer_candidates else None,
            "unmatched_opportunity_ids": dealer_missing,
        },
        "promotion_boundary": {
            "catalog_rows_are_external_leads": True,
            "catalog_import_is_not_event_promotion": True,
            "approved_decision_required_by_runtime": True,
            "ordered_decision_transition_not_validated_by_catalog": True,
            "business_outcomes_remain_unmeasured": True,
        },
        "issue_counts": {
            "errors": severity_counts["error"],
            "warnings": severity_counts["warning"],
        },
        "issues": issues,
        "limitations": [
            "URL checks are structural and offline; they do not prove current reachability or publisher ownership.",
            "dealer_match_name is only a catalog hint; database entity resolution and store-level coverage require a separate audited pipeline.",
            "public activity pages do not prove Viltrox authorization, stock, attendance, sales attribution, ROI, or local impact.",
        ],
    }


def audit_dealer_candidates(
    candidates: list[dict[str, Any]],
    *,
    source_path: str = "<memory>",
) -> dict[str, Any]:
    """Audit reviewed Dealer candidates without importing code or touching DB.

    Organization grouping derived from an official-site host is deliberately a
    *candidate* identity.  Only explicit ``stable_org_key``,
    ``stable_location_key`` and ``aliases`` fields count as reviewed identity
    evidence.  This prevents a shared retail domain from becoming an automatic
    entity merge.
    """
    rows = deepcopy(candidates)
    issues: list[dict[str, str]] = []

    def issue(severity: str, code: str, path: str, message: str) -> None:
        issues.append({"severity": severity, "code": code, "path": path, "message": message})

    explicit_org_keys = 0
    explicit_location_keys = 0
    explicit_source_checked = 0
    product_url_present = 0
    product_url_structural = 0
    location_url_structural = 0
    contact_presence: Counter[str] = Counter()
    contact_provenance: Counter[str] = Counter()
    missing_contact_provenance: dict[str, list[str]] = {field: [] for field in DEALER_CONTACT_FIELDS}
    org_groups: dict[str, dict[str, Any]] = {}
    location_keys: list[str] = []
    alias_targets: dict[tuple[str, str, str], set[str]] = {}
    explicit_alias_count = 0
    orgs_with_alias: set[str] = set()

    for index, raw_candidate in enumerate(rows):
        path = f"dealers[{index}]"
        if not isinstance(raw_candidate, dict):
            issue("error", "dealer.type", path, "dealer candidate must be an object")
            continue
        candidate = raw_candidate
        name = str(candidate.get("name") or "").strip()
        address = str(candidate.get("address") or "").strip()
        country = str(candidate.get("country") or "").strip().upper()
        if not name:
            issue("error", "dealer.name_missing", f"{path}.name", "dealer name is required")
        if not address:
            issue("error", "dealer.address_missing", f"{path}.address", "store address is required")
        if not re.fullmatch(r"[A-Z]{2}", country):
            issue("error", "dealer.country", f"{path}.country", "country must be uppercase ISO alpha-2")

        domain = _dealer_domain(candidate)
        explicit_org_key = str(candidate.get("stable_org_key") or "").strip()
        explicit_location_key = str(candidate.get("stable_location_key") or "").strip()
        if explicit_org_key:
            explicit_org_keys += 1
            if not explicit_org_key.startswith("dealer_org_"):
                issue("error", "dealer.stable_org_key", f"{path}.stable_org_key", "stable_org_key must start with dealer_org_")
        if explicit_location_key:
            explicit_location_keys += 1
            if not explicit_location_key.startswith("dealer_loc_"):
                issue(
                    "error",
                    "dealer.stable_location_key",
                    f"{path}.stable_location_key",
                    "stable_location_key must start with dealer_loc_",
                )

        derived_org_key = explicit_org_key or _candidate_key("dealer_org", domain or name.split("·", 1)[0])
        derived_location_key = explicit_location_key or _candidate_key(
            "dealer_loc", derived_org_key, country, address, candidate.get("postal_code")
        )
        if derived_location_key:
            location_keys.append(derived_location_key)
        group = org_groups.setdefault(
            derived_org_key or f"unresolved:{index}",
            {
                "candidate_org_key": derived_org_key,
                "domain": domain,
                "identity_status": "reviewed" if explicit_org_key else "derived_candidate_only",
                "location_count": 0,
                "location_names": [],
            },
        )
        group["location_count"] += 1
        group["location_names"].append(name)
        if group["domain"] != domain:
            issue(
                "warning",
                "dealer.organization_domain_conflict",
                path,
                "one candidate organization key resolves to more than one domain",
            )

        brand_url = str(candidate.get("brand_listing_url") or "").strip()
        if brand_url:
            product_url_present += 1
            if _is_https_url(brand_url):
                product_url_structural += 1
            else:
                issue(
                    "error",
                    "dealer.brand_listing_url",
                    f"{path}.brand_listing_url",
                    "brand_listing_url must be a credential-free HTTPS URL",
                )
        else:
            issue(
                "warning",
                "dealer.brand_listing_url_missing",
                f"{path}.brand_listing_url",
                "candidate has no declared Viltrox brand/product page URL",
            )
        location_url = str(candidate.get("location_source_url") or "").strip()
        if _is_https_url(location_url):
            location_url_structural += 1
        else:
            issue(
                "error",
                "dealer.location_source_url",
                f"{path}.location_source_url",
                "location_source_url must be a credential-free HTTPS URL",
            )

        checked_at = candidate.get("source_checked_at")
        if checked_at not in (None, ""):
            if _parse_checked_at(checked_at) is None:
                issue(
                    "error",
                    "dealer.source_checked_at",
                    f"{path}.source_checked_at",
                    "source_checked_at must be an ISO timestamp with timezone",
                )
            else:
                explicit_source_checked += 1

        raw_contact_provenance = candidate.get("contact_provenance")
        provenance_map = raw_contact_provenance if isinstance(raw_contact_provenance, dict) else {}
        if raw_contact_provenance not in (None, {}) and not isinstance(raw_contact_provenance, dict):
            issue(
                "error",
                "dealer.contact_provenance_type",
                f"{path}.contact_provenance",
                "contact_provenance must be an object keyed by contact field",
            )
        for field in DEALER_CONTACT_FIELDS:
            if not str(candidate.get(field) or "").strip():
                continue
            contact_presence[field] += 1
            provenance_url = str(
                provenance_map.get(field) or candidate.get(f"{field}_source_url") or ""
            ).strip()
            if not provenance_url:
                missing_contact_provenance[field].append(name or f"index:{index}")
            elif _is_https_url(provenance_url):
                contact_provenance[field] += 1
            else:
                issue(
                    "error",
                    "dealer.contact_provenance_url",
                    f"{path}.contact_provenance.{field}",
                    "contact provenance must be a credential-free HTTPS URL",
                )

        aliases = candidate.get("aliases") or []
        if not isinstance(aliases, list):
            issue("error", "dealer.aliases_type", f"{path}.aliases", "aliases must be an array")
            aliases = []
        for alias_index, raw_alias in enumerate(aliases):
            alias_path = f"{path}.aliases[{alias_index}]"
            if not isinstance(raw_alias, dict):
                issue("error", "dealer.alias_type", alias_path, "alias must be an object")
                continue
            alias_type = str(raw_alias.get("alias_type") or "").strip()
            alias_value = str(raw_alias.get("alias_value") or "").strip()
            alias_country = str(raw_alias.get("country_code") or country).strip().upper()
            normalized = _normalized_text(raw_alias.get("alias_normalized") or alias_value)
            if not alias_type or not normalized:
                issue("error", "dealer.alias_identity", alias_path, "alias_type and alias_value are required")
                continue
            explicit_alias_count += 1
            orgs_with_alias.add(derived_org_key)
            alias_targets.setdefault((alias_type, normalized, alias_country), set()).add(derived_org_key)

        authorization_status = str(candidate.get("authorization_status") or "unknown").strip().lower()
        if authorization_status not in UNKNOWN_CLAIM_VALUES | {"needs_viltrox_confirmation"}:
            issue(
                "error",
                "dealer.unsupported_authorization_claim",
                f"{path}.authorization_status",
                "public retail pages cannot establish Viltrox authorization",
            )
        for field in DEALER_POSITIVE_CLAIM_FIELDS:
            if field in candidate and _claim_is_inferred(candidate.get(field)):
                issue(
                    "error",
                    "dealer.unsupported_business_claim",
                    f"{path}.{field}",
                    f"public retail pages cannot establish {field}",
                )

    duplicate_location_keys = sorted(key for key, count in Counter(location_keys).items() if key and count > 1)
    for key in duplicate_location_keys:
        issue(
            "error",
            "dealer.location_identity_duplicate",
            "dealers",
            f"duplicate candidate location identity: {key}",
        )
    alias_conflicts = [
        {"alias_type": key[0], "alias_normalized": key[1], "country_code": key[2], "org_keys": sorted(targets)}
        for key, targets in sorted(alias_targets.items())
        if len(targets) > 1
    ]
    for conflict in alias_conflicts:
        issue(
            "error",
            "dealer.alias_conflict",
            "dealers.aliases",
            f"alias maps to multiple organization candidates: {conflict['alias_normalized']}",
        )

    missing_org_keys = len(rows) - explicit_org_keys
    missing_location_keys = len(rows) - explicit_location_keys
    if missing_org_keys:
        issue(
            "warning",
            "dealer.stable_org_identity_incomplete",
            "dealers",
            f"{missing_org_keys} dealer candidates lack reviewed stable_org_key",
        )
    if missing_location_keys:
        issue(
            "warning",
            "dealer.stable_location_identity_incomplete",
            "dealers",
            f"{missing_location_keys} dealer candidates lack reviewed stable_location_key",
        )
    if org_groups and not explicit_alias_count:
        issue(
            "warning",
            "dealer.alias_review_missing",
            "dealers.aliases",
            "no reviewed organization, domain, store or social aliases are present",
        )
    missing_contact_total = sum(len(items) for items in missing_contact_provenance.values())
    if missing_contact_total:
        issue(
            "warning",
            "dealer.contact_provenance_incomplete",
            "dealers.contact_provenance",
            f"{missing_contact_total} populated contact fields lack field-level provenance",
        )
    if rows and not explicit_source_checked:
        issue(
            "warning",
            "dealer.freshness_unavailable",
            "dealers.source_checked_at",
            "dealer candidates have no per-row source_checked_at timestamps",
        )

    severity_counts = Counter(item["severity"] for item in issues)
    derived_orgs = sorted(org_groups.values(), key=lambda item: (item["domain"], item["candidate_org_key"]))
    contact_fields = {
        field: {
            "present": contact_presence[field],
            "with_field_level_provenance": contact_provenance[field],
            "provenance_rate": _ratio(contact_provenance[field], contact_presence[field]),
            "missing_provenance_dealers": missing_contact_provenance[field],
        }
        for field in DEALER_CONTACT_FIELDS
    }
    return {
        "ok": severity_counts["error"] == 0,
        "read_only": True,
        "source_path": source_path,
        "grain": "one reviewed public retailer location candidate",
        "counts": {
            "candidate_locations": len(rows),
            "derived_candidate_organizations": len(org_groups),
            "countries": len({str(item.get("country") or "").upper() for item in rows if item.get("country")}),
        },
        "identity": {
            "derived_grouping_status": "candidate_only_not_reviewed_merge",
            "explicit_stable_org_keys": explicit_org_keys,
            "stable_org_key_coverage_rate": _ratio(explicit_org_keys, len(rows)),
            "explicit_stable_location_keys": explicit_location_keys,
            "stable_location_key_coverage_rate": _ratio(explicit_location_keys, len(rows)),
            "explicit_alias_records": explicit_alias_count,
            "candidate_orgs_with_alias": len(orgs_with_alias),
            "orgs_with_alias_rate": _ratio(len(orgs_with_alias), len(org_groups)),
            "alias_completeness_denominator_status": "denominator_unavailable",
            "duplicate_location_keys": duplicate_location_keys,
            "alias_conflicts": alias_conflicts,
            "derived_organization_candidates": derived_orgs,
        },
        "contact_provenance": {
            "requirement": "each populated contact field must name its own public source URL",
            "fields": contact_fields,
        },
        "viltrox_product_page_presence": {
            "semantics": "declared_public_listing_url_structural_check_only",
            "candidate_locations": len(rows),
            "declared_urls": product_url_present,
            "structurally_valid_urls": product_url_structural,
            "structural_url_rate": _ratio(product_url_structural, len(rows)),
            "remote_pages_verified_in_this_audit": 0,
            "remote_presence_status": "not_checked_offline",
            "not_equivalent_to": DEALER_NON_EQUIVALENT_FACTS,
        },
        "location_evidence": {
            "structurally_valid_official_location_urls": location_url_structural,
            "structural_url_rate": _ratio(location_url_structural, len(rows)),
        },
        "freshness": {
            "rows_with_source_checked_at": explicit_source_checked,
            "coverage_rate": _ratio(explicit_source_checked, len(rows)),
            "status": "available" if explicit_source_checked == len(rows) and rows else "unavailable",
        },
        "issue_counts": {
            "errors": severity_counts["error"],
            "warnings": severity_counts["warning"],
        },
        "issues": issues,
    }


def build_event_dealer_coverage_audit(
    catalog: dict[str, Any],
    dealer_candidates: list[dict[str, Any]],
    *,
    catalog_path: str = "<memory>",
    dealer_source_path: str = "<memory>",
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    """Build the machine-readable, offline Event/Dealer coverage contract."""
    if stale_after_days < 1:
        raise ValueError("stale_after_days must be positive")
    audit_time = _as_utc(as_of)
    event_report = audit_catalog(catalog, catalog_path=catalog_path)
    dealer_report = audit_dealer_candidates(dealer_candidates, source_path=dealer_source_path)

    sources = [item for item in catalog.get("sources", []) if isinstance(item, dict)]
    opportunities = [item for item in catalog.get("opportunities", []) if isinstance(item, dict)]
    countries = sorted({str(item.get("country_code")) for item in sources if item.get("country_code")})
    regions = sorted({str(item.get("region")) for item in sources if item.get("region")})
    opportunity_countries = sorted(
        {str(item.get("country_code")) for item in opportunities if item.get("country_code")}
    )
    status_counts = Counter(str(item.get("status") or "unknown") for item in sources)
    source_ids_with_opportunities = {
        str(item.get("source_id")) for item in opportunities if item.get("source_id")
    }
    active_source_ids = {
        str(item.get("id")) for item in sources if item.get("status") == "active" and item.get("id")
    }

    checked_at = _parse_checked_at(catalog.get("checked_at"))
    if checked_at is None:
        snapshot_freshness = {
            "status": "unknown",
            "checked_at": catalog.get("checked_at"),
            "as_of": audit_time.isoformat(),
            "age_days": None,
            "stale_after_days": stale_after_days,
        }
    else:
        age_days = (audit_time - checked_at.astimezone(timezone.utc)).total_seconds() / 86400
        if age_days < 0:
            freshness_status = "future_timestamp"
        elif age_days > stale_after_days:
            freshness_status = "stale"
        else:
            freshness_status = "fresh"
        snapshot_freshness = {
            "status": freshness_status,
            "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
            "as_of": audit_time.isoformat(),
            "age_days": round(age_days, 3),
            "stale_after_days": stale_after_days,
        }
    source_checked_count = sum(
        1 for item in sources if _parse_checked_at(item.get("source_checked_at")) is not None
    )

    dealer_name_index: dict[str, list[dict[str, Any]]] = {}
    for index, candidate in enumerate(dealer_candidates):
        if not isinstance(candidate, dict):
            continue
        name_key = _normalized_text(candidate.get("name"))
        if not name_key:
            continue
        domain = _dealer_domain(candidate)
        org_key = str(candidate.get("stable_org_key") or "").strip() or _candidate_key(
            "dealer_org", domain or str(candidate.get("name") or "").split("·", 1)[0]
        )
        explicit_location_key = str(candidate.get("stable_location_key") or "").strip()
        dealer_name_index.setdefault(name_key, []).append(
            {
                "candidate_index": index,
                "name": str(candidate.get("name") or ""),
                "org_key": org_key,
                "location_key": explicit_location_key
                or _candidate_key(
                    "dealer_loc",
                    org_key,
                    candidate.get("country"),
                    candidate.get("address"),
                    candidate.get("postal_code"),
                ),
                "identity_status": "reviewed" if explicit_location_key else "derived_candidate_only",
            }
        )
    dealer_local_opportunities = [
        item for item in opportunities if item.get("lane") in {"dealer_event", "local_activity"}
    ]
    linkage_issues: list[dict[str, str]] = []
    linkage_rows: list[dict[str, Any]] = []
    name_hint_count = 0
    name_resolved_count = 0
    reviewed_identity_count = 0
    ambiguous_count = 0
    for opportunity in dealer_local_opportunities:
        opportunity_id = str(opportunity.get("id") or "")
        name_hint = str(opportunity.get("dealer_match_name") or "").strip()
        if not name_hint:
            linkage_rows.append(
                {
                    "opportunity_id": opportunity_id,
                    "dealer_match_name": "",
                    "status": "missing_name_hint",
                    "candidate_matches": [],
                }
            )
            continue
        name_hint_count += 1
        matches = dealer_name_index.get(_normalized_text(name_hint), [])
        if len(matches) == 1:
            name_resolved_count += 1
            if matches[0]["identity_status"] == "reviewed":
                reviewed_identity_count += 1
            status = (
                "reviewed_location_identity"
                if matches[0]["identity_status"] == "reviewed"
                else "derived_candidate_name_match"
            )
        elif len(matches) > 1:
            ambiguous_count += 1
            status = "ambiguous_name_match"
            linkage_issues.append(
                {
                    "severity": "error",
                    "code": "opportunity.dealer_identity_ambiguous",
                    "path": f"opportunities[{opportunity_id}].dealer_match_name",
                    "message": f"dealer_match_name resolves to {len(matches)} location candidates",
                }
            )
        else:
            status = "unresolved_name_hint"
            linkage_issues.append(
                {
                    "severity": "warning",
                    "code": "opportunity.dealer_identity_unresolved",
                    "path": f"opportunities[{opportunity_id}].dealer_match_name",
                    "message": "dealer_match_name does not resolve to a reviewed Dealer candidate",
                }
            )
        linkage_rows.append(
            {
                "opportunity_id": opportunity_id,
                "dealer_match_name": name_hint,
                "status": status,
                "candidate_matches": matches,
            }
        )

    issues = [*event_report["issues"], *dealer_report["issues"], *linkage_issues]
    severity_counts = Counter(item["severity"] for item in issues)
    structural_ok = severity_counts["error"] == 0
    return {
        "contract": {
            "id": COVERAGE_CONTRACT_ID,
            "version": COVERAGE_CONTRACT_VERSION,
            "generated_at": audit_time.isoformat(),
            "read_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
        },
        "ok": structural_ok,
        "quality_status": "partial" if structural_ok else "invalid",
        "claim_status": "descriptive_only",
        "inputs": {
            "event_catalog": catalog_path,
            "dealer_candidate_source": dealer_source_path,
        },
        "event_source_coverage": {
            "claim": catalog.get("coverage_claim"),
            "global_complete": catalog.get("global_complete"),
            "observed_reviewed_sources": len(sources),
            "known_source_universe_denominator": None,
            "denominator_status": "denominator_unavailable",
            "global_coverage_rate": None,
            "official_ownership_reverified_online": False,
            "status_counts": {
                "active": status_counts["active"],
                "hold": status_counts["hold"],
                "blocked": status_counts["blocked"],
                "retired": status_counts["retired"],
                "unknown": status_counts["unknown"],
            },
            "reviewed_active_sources": len(active_source_ids),
            "active_sources_with_catalog_opportunity": len(active_source_ids & source_ids_with_opportunities),
            "reviewed_active_source_yield_rate": _ratio(
                len(active_source_ids & source_ids_with_opportunities), len(active_source_ids)
            ),
            "yield_rate_scope": "reviewed_active_sources_only_not_global_coverage",
        },
        "geographic_coverage": {
            "source_countries": countries,
            "source_country_count": len(countries),
            "source_regions": regions,
            "source_region_count": len(regions),
            "opportunity_countries": opportunity_countries,
            "opportunity_country_count": len(opportunity_countries),
            "global_country_universe_denominator": None,
            "denominator_status": "denominator_unavailable",
            "global_country_coverage_rate": None,
        },
        "freshness": {
            "event_catalog_snapshot": snapshot_freshness,
            "event_sources": {
                "rows": len(sources),
                "rows_with_source_checked_at": source_checked_count,
                "coverage_rate": _ratio(source_checked_count, len(sources)),
                "status": "available" if source_checked_count == len(sources) and sources else "unavailable",
            },
            "dealer_candidates": dealer_report["freshness"],
        },
        "dealer_event_identity_linkage": {
            "grain": "one dealer_event/local_activity opportunity",
            "matching_basis": "normalized dealer_match_name to candidate name; never a database foreign key",
            "dealer_local_opportunities": len(dealer_local_opportunities),
            "opportunities_with_name_hint": name_hint_count,
            "name_hint_rate": _ratio(name_hint_count, len(dealer_local_opportunities)),
            "name_hints_resolved_to_one_candidate": name_resolved_count,
            "candidate_name_resolution_rate": _ratio(name_resolved_count, name_hint_count),
            "reviewed_location_identity_links": reviewed_identity_count,
            "reviewed_location_identity_rate": _ratio(
                reviewed_identity_count, len(dealer_local_opportunities)
            ),
            "ambiguous_name_hints": ambiguous_count,
            "rows": linkage_rows,
        },
        "dealer_quality": dealer_report,
        "event_catalog_quality": event_report,
        "claim_boundaries": {
            "global_full_coverage_claim_allowed": False,
            "public_listing_proves_viltrox_product_page": False,
            "public_listing_proves_authorization": False,
            "public_listing_proves_inventory_or_stock": False,
            "public_listing_proves_sales_or_attribution": False,
            "public_listing_proves_local_impact": False,
            "remote_page_presence_not_verified_offline": True,
        },
        "issue_counts": {
            "errors": severity_counts["error"],
            "warnings": severity_counts["warning"],
        },
        "issues": issues,
        "limitations": [
            "No global source or dealer universe denominator is available; global coverage rates are intentionally null.",
            "URL checks are structural and offline; they do not prove reachability, current content or publisher ownership.",
            "Domain grouping is a candidate identity only; reviewed organization/location keys and aliases remain separate evidence.",
            "A Viltrox listing URL is not evidence of authorization, inventory, sales attribution or local impact.",
        ],
    }


def audit_catalog_file(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("event radar catalog root must be an object")
    return audit_catalog(data, catalog_path=str(path.resolve()))


def audit_coverage_quality_files(
    catalog_path: Path = DEFAULT_CATALOG,
    dealer_source_path: Path = DEFAULT_DEALER_SOURCE,
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("event radar catalog root must be an object")
    dealers = load_reviewed_dealer_candidates(dealer_source_path)
    return build_event_dealer_coverage_audit(
        catalog,
        dealers,
        catalog_path=str(catalog_path.resolve()),
        dealer_source_path=str(dealer_source_path.resolve()),
        as_of=as_of,
        stale_after_days=stale_after_days,
    )


def main(argv: list[str] | None = None) -> int:
    from scripts.ops.event_radar_audit_cli import run_cli

    return run_cli(audit_coverage_quality_files, argv)


if __name__ == "__main__":
    raise SystemExit(main())
