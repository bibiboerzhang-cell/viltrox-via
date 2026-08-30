"""Fail-closed US Event/Dealer source registry.

This module registers first-party discovery entry points without scraping them or
writing business rows.  A source being official for its own publisher never
proves Viltrox authorization, product presence, inventory, participation, sales,
ROI, attendance, or local impact.
"""
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_REGISTRY_PATH = Path(__file__).with_name("us_coverage_source_registry.json")
_US_JURISDICTIONS = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC".split()
)
_EVENT_SOURCE_KINDS = {
    "major_expo",
    "dealer_event",
    "venue_calendar",
    "school_calendar",
    "university_calendar",
    "photo_club",
    "community_calendar",
    "brand_event",
    "association_directory",
}
_DEALER_SOURCE_KINDS = {
    "manufacturer_dealer_directory",
    "retailer_location_directory",
}
_DEALER_OFFICIAL_INGEST_KINDS = {
    "company_feed",
    "manual_official_entry",
}
_DEALER_SOURCE_CHANNELS = {
    "official_public_directory",
    "official_public_pdf",
    "official_support_article",
    "official_store_directory",
    "publisher_owned_location_page",
}
_DEALER_OFFICIAL_INGEST_CHANNELS = {
    "company_feed",
    "manual_official_entry",
}
_FIXTURE_STATUSES = {
    "format_fixture_only_not_source_verified",
    "not_provided",
}


def load_registry() -> dict[str, Any]:
    payload = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("US coverage source registry must be an object")
    return payload


def _https_url(value: Any) -> bool:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    return bool(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password)


def _jurisdiction_matrix(
    rows: list[dict[str, Any]],
    *,
    truth_note: Any,
) -> dict[str, Any]:
    covered = sorted(
        {
            state
            for row in rows
            for state in row.get("state_codes", [])
            if state in _US_JURISDICTIONS
        }
    )
    missing = sorted(_US_JURISDICTIONS - set(covered))
    return {
        "scope": "registered_source_discovery_jurisdictions_only",
        "covered_states_dc": covered,
        "missing_states_dc": missing,
        "covered_count": len(covered),
        "jurisdiction_count": len(_US_JURISDICTIONS),
        "source_discovery_rate": round(len(covered) / len(_US_JURISDICTIONS), 4),
        "extracted_candidate_count": None,
        "verified_business_row_count": None,
        "entity_coverage_rate": None,
        "claim_status": "descriptive_only",
        "truth_note": str(truth_note or "").strip() or None,
    }


def _validated_source_lists(
    data: dict[str, Any],
    issues: list[dict[str, str]],
) -> tuple[list[Any], list[Any], list[Any]]:
    event_sources = data.get("event_sources")
    dealer_sources = data.get("dealer_discovery_sources")
    dealer_official_ingest_sources = data.get("dealer_official_ingest_sources")
    if not isinstance(event_sources, list):
        issues.append(
            {
                "code": "event_sources_type",
                "path": "event_sources",
                "message": "must be an array",
            }
        )
        event_sources = []
    if not isinstance(dealer_sources, list):
        issues.append(
            {
                "code": "dealer_sources_type",
                "path": "dealer_discovery_sources",
                "message": "must be an array",
            }
        )
        dealer_sources = []
    if not isinstance(dealer_official_ingest_sources, list):
        issues.append(
            {
                "code": "dealer_official_ingest_sources_type",
                "path": "dealer_official_ingest_sources",
                "message": "must be an array",
            }
        )
        dealer_official_ingest_sources = []
    return event_sources, dealer_sources, dealer_official_ingest_sources


def _append_registry_boundary_issues(
    data: dict[str, Any],
    issues: list[dict[str, str]],
) -> None:
    if data.get("full_us_coverage") is not False:
        issues.append(
            {
                "code": "full_us_coverage_forbidden",
                "path": "full_us_coverage",
                "message": "must remain false",
            }
        )
    if str(data.get("coverage_claim") or "") != "registered_publisher_owned_public_entries_only":
        issues.append(
            {
                "code": "coverage_claim_invalid",
                "path": "coverage_claim",
                "message": "claim must remain source-bounded",
            }
        )


def _normalize_state_codes(
    raw: dict[str, Any],
    path: str,
    issues: list[dict[str, str]],
) -> list[str]:
    raw_state_codes = raw.get("state_codes", [])
    if not isinstance(raw_state_codes, list):
        issues.append(
            {
                "code": "state_codes_type",
                "path": f"{path}.state_codes",
                "message": "must be an array",
            }
        )
        return []
    state_codes = [str(value or "").strip().upper() for value in raw_state_codes]
    invalid_state_codes = sorted(
        {value for value in state_codes if value not in _US_JURISDICTIONS}
    )
    if invalid_state_codes:
        issues.append(
            {
                "code": "state_code_invalid",
                "path": f"{path}.state_codes",
                "message": "unsupported US state/DC code: " + ", ".join(invalid_state_codes),
            }
        )
    if len(state_codes) != len(set(state_codes)):
        issues.append(
            {
                "code": "state_code_duplicate",
                "path": f"{path}.state_codes",
                "message": "state/DC codes must be unique",
            }
        )
    return sorted({value for value in state_codes if value in _US_JURISDICTIONS})


def _append_dealer_review_issues(
    raw: dict[str, Any],
    *,
    path: str,
    requires_review: bool,
    fixture_status: str,
    issues: list[dict[str, str]],
) -> None:
    if requires_review and fixture_status not in _FIXTURE_STATUSES:
        issues.append(
            {
                "code": "dealer_fixture_status_invalid",
                "path": f"{path}.fixture_status",
                "message": "fixture status must remain explicit and unverified",
            }
        )
    if requires_review and str(raw.get("status") or "") != "awaiting_review":
        issues.append(
            {
                "code": "dealer_status_not_awaiting_review",
                "path": f"{path}.status",
                "message": "registered dealer sources must default to awaiting_review",
            }
        )
    if requires_review and raw.get("enabled") is not False:
        issues.append(
            {
                "code": "dealer_source_not_disabled",
                "path": f"{path}.enabled",
                "message": "registered dealer sources must default disabled",
            }
        )
    if requires_review and str(raw.get("terms_robots_status") or "") != "pending_review":
        issues.append(
            {
                "code": "dealer_terms_status_not_pending",
                "path": f"{path}.terms_robots_status",
                "message": "terms/robots review must remain pending until separately approved",
            }
        )


def _normalize_registry_row(
    *,
    scope: str,
    raw: Any,
    allowed_kinds: set[str],
    index: int,
    seen_ids: set[str],
    seen_urls: set[str],
    issues: list[dict[str, str]],
) -> dict[str, Any] | None:
    path = f"{scope}[{index}]"
    if not isinstance(raw, dict):
        issues.append(
            {"code": "source_row_type", "path": path, "message": "must be an object"}
        )
        return None

    source_id = str(raw.get("id") or "").strip()
    url = str(raw.get("canonical_url") or "").strip()
    source_kind = str(raw.get("source_kind") or "").strip()
    if not source_id or source_id in seen_ids:
        issues.append(
            {
                "code": "source_id_invalid_or_duplicate",
                "path": f"{path}.id",
                "message": "stable unique id required",
            }
        )
    if not _https_url(url) or url in seen_urls:
        issues.append(
            {
                "code": "source_url_invalid_or_duplicate",
                "path": f"{path}.canonical_url",
                "message": "unique credential-free HTTPS URL required",
            }
        )
    if source_kind not in allowed_kinds:
        issues.append(
            {
                "code": "source_kind_invalid",
                "path": f"{path}.source_kind",
                "message": "source kind is not allowed for this registry scope",
            }
        )
    if not str(raw.get("publisher") or "").strip():
        issues.append(
            {
                "code": "publisher_missing",
                "path": f"{path}.publisher",
                "message": "publisher is required",
            }
        )

    source_channel = str(raw.get("source_channel") or "").strip()
    if (
        scope == "dealer_discovery_sources"
        and source_kind == "manufacturer_dealer_directory"
        and source_channel not in _DEALER_SOURCE_CHANNELS
    ):
        issues.append(
            {
                "code": "dealer_source_channel_invalid",
                "path": f"{path}.source_channel",
                "message": "official dealer source channel is required",
            }
        )
    if (
        scope == "dealer_official_ingest_sources"
        and source_channel not in _DEALER_OFFICIAL_INGEST_CHANNELS
    ):
        issues.append(
            {
                "code": "dealer_official_ingest_channel_invalid",
                "path": f"{path}.source_channel",
                "message": "company_feed or manual_official_entry is required",
            }
        )

    fixture_status = str(raw.get("fixture_status") or "").strip()
    requires_review = bool(
        scope == "dealer_official_ingest_sources"
        or source_kind == "manufacturer_dealer_directory"
    )
    _append_dealer_review_issues(
        raw,
        path=path,
        requires_review=requires_review,
        fixture_status=fixture_status,
        issues=issues,
    )
    state_codes = _normalize_state_codes(raw, path, issues)
    seen_ids.add(source_id)
    seen_urls.add(url)
    is_dealer_scope = scope != "event_sources"
    return {
        **raw,
        "state_codes": state_codes,
        "country_code": "US",
        "source_channel": source_channel
        or (
            "publisher_owned_location_page"
            if scope == "dealer_discovery_sources"
            else ""
        ),
        "status": "awaiting_review" if is_dealer_scope else "hold",
        "enabled": False,
        "direct_import_allowed": False,
        "requires_human_review": True,
        "terms_robots_status": "pending_review",
        "fixture_status": fixture_status or "not_provided",
        "publisher_identity_status": str(raw.get("publisher_identity_status") or "unverified"),
        "claim_status": "descriptive_only",
        "scope": scope,
        "candidate_only": scope == "dealer_discovery_sources",
        "site_has_viltrox_product": "unknown",
        "viltrox_authorized": "unknown",
        "viltrox_authorization_evidence": False,
    }


def _normalize_registry_rows(
    event_sources: list[Any],
    dealer_sources: list[Any],
    dealer_official_ingest_sources: list[Any],
    issues: list[dict[str, str]],
) -> list[dict[str, Any]]:
    all_rows = [("event_sources", row, _EVENT_SOURCE_KINDS) for row in event_sources]
    all_rows += [
        ("dealer_discovery_sources", row, _DEALER_SOURCE_KINDS) for row in dealer_sources
    ]
    all_rows += [
        ("dealer_official_ingest_sources", row, _DEALER_OFFICIAL_INGEST_KINDS)
        for row in dealer_official_ingest_sources
    ]
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    normalized_rows: list[dict[str, Any]] = []
    for scope, raw, allowed_kinds in all_rows:
        index = len([item for item in normalized_rows if item["scope"] == scope])
        normalized = _normalize_registry_row(
            scope=scope,
            raw=raw,
            allowed_kinds=allowed_kinds,
            index=index,
            seen_ids=seen_ids,
            seen_urls=seen_urls,
            issues=issues,
        )
        if normalized is not None:
            normalized_rows.append(normalized)
    return normalized_rows


def _partition_registry_rows(
    normalized_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    events = [row for row in normalized_rows if row["scope"] == "event_sources"]
    dealers = [
        row for row in normalized_rows if row["scope"] == "dealer_discovery_sources"
    ]
    official_ingest = [
        row
        for row in normalized_rows
        if row["scope"] == "dealer_official_ingest_sources"
    ]
    return events, dealers, official_ingest


def _registry_counts(
    events: list[dict[str, Any]],
    dealers: list[dict[str, Any]],
    official_ingest: list[dict[str, Any]],
    event_jurisdictions: dict[str, Any],
    dealer_jurisdictions: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_sources": len(events),
        "dealer_discovery_sources": len(dealers),
        "dealer_official_ingest_sources": len(official_ingest),
        "event_source_kinds": dict(
            sorted(Counter(row["source_kind"] for row in events).items())
        ),
        "dealer_source_kinds": dict(
            sorted(Counter(row["source_kind"] for row in dealers).items())
        ),
        "dealer_official_ingest_kinds": dict(
            sorted(Counter(row["source_kind"] for row in official_ingest).items())
        ),
        "event_source_jurisdictions": event_jurisdictions["covered_count"],
        "dealer_source_jurisdictions": dealer_jurisdictions["covered_count"],
        "dealer_discovery_scopes": len(
            {str(row.get("manufacturer_authorization_scope") or "") for row in dealers}
        ),
        "dealer_manufacturer_scopes": len(
            {
                str(row.get("manufacturer_authorization_scope") or "")
                for row in dealers
                if row.get("source_kind") == "manufacturer_dealer_directory"
            }
        ),
        "enabled": 0,
        "direct_import_allowed": 0,
    }


def _build_registry_report(
    *,
    data: dict[str, Any],
    issues: list[dict[str, str]],
    events: list[dict[str, Any]],
    dealers: list[dict[str, Any]],
    official_ingest: list[dict[str, Any]],
) -> dict[str, Any]:
    jurisdiction_truth_note = data.get("jurisdiction_truth_note")
    event_jurisdictions = _jurisdiction_matrix(events, truth_note=jurisdiction_truth_note)
    dealer_jurisdictions = _jurisdiction_matrix(dealers, truth_note=jurisdiction_truth_note)
    return {
        "ok": not issues,
        "contract": {
            "id": "vkpi.us_event_dealer.source_registry",
            "version": 2,
            "read_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
        },
        "registry_version": data.get("registry_version"),
        "checked_at": data.get("checked_at"),
        "country_code": "US",
        "coverage_claim": "registered_publisher_owned_public_entries_only",
        "full_us_coverage": False,
        "global_denominator": None,
        "global_coverage_rate": None,
        "claim_status": "descriptive_only",
        "truth_note": data.get("truth_note"),
        "jurisdiction_truth_note": jurisdiction_truth_note,
        "source_jurisdiction_matrix": {
            "event_sources": event_jurisdictions,
            "dealer_discovery_sources": dealer_jurisdictions,
        },
        "event_sources": events,
        "dealer_discovery_sources": dealers,
        "dealer_official_ingest_sources": official_ingest,
        "counts": _registry_counts(
            events,
            dealers,
            official_ingest,
            event_jurisdictions,
            dealer_jurisdictions,
        ),
        "dealer_truth_dimensions": {
            "candidate": "registered discovery lead only",
            "official_ingest": "disabled authorization slot only until a reviewed company feed or exact manual receipt exists",
            "site_has_viltrox_product": "requires current retailer-owned product-page field evidence",
            "viltrox_authorized": "requires separate current Viltrox-owned authorization evidence",
        },
        "import_gate": {
            "allowed": False,
            "reason": "source_registry_requires_per_entity_human_review_and_passports",
        },
        "claim_boundaries": {
            "manufacturer_authorization_proves_viltrox_authorization": False,
            "dealer_candidate_proves_viltrox_product_presence": False,
            "product_page_proves_current_inventory": False,
            "registered_sources_equal_all_us_sources": False,
            "source_jurisdiction_coverage_proves_entity_coverage": False,
            "registered_event_source_proves_viltrox_participation": False,
            "official_ingest_entry_proves_feed_received": False,
            "company_feed_entry_proves_authorized_dealers": False,
            "manual_entry_proves_viltrox_authorization": False,
        },
        "issues": issues,
    }


def audit_registry(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate source identities and attach non-negotiable truth controls."""
    data = deepcopy(payload if payload is not None else load_registry())
    issues: list[dict[str, str]] = []
    event_sources, dealer_sources, official_ingest_sources = _validated_source_lists(
        data,
        issues,
    )
    _append_registry_boundary_issues(data, issues)
    normalized_rows = _normalize_registry_rows(
        event_sources,
        dealer_sources,
        official_ingest_sources,
        issues,
    )
    events, dealers, official_ingest = _partition_registry_rows(normalized_rows)
    return _build_registry_report(
        data=data,
        issues=issues,
        events=events,
        dealers=dealers,
        official_ingest=official_ingest,
    )


def event_registry() -> dict[str, Any]:
    report = audit_registry()
    return {
        **report,
        "dealer_discovery_sources": [],
        "dealer_official_ingest_sources": [],
    }


def dealer_registry() -> dict[str, Any]:
    report = audit_registry()
    return {**report, "event_sources": []}


__all__ = ["audit_registry", "dealer_registry", "event_registry", "load_registry"]
