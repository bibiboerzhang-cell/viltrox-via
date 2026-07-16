"""Pure, fail-closed quality contract for Event Radar and Dealer candidates.

This module deliberately has no database, HTTP, worker, or application-router
dependency.  It audits already supplied rows and makes the distinction between
three different facts explicit:

* a row exists in a reviewed local catalog;
* a source/evidence item was checked recently; and
* the system knows the denominator of the intended universe.

Only the second fact can contribute to evidence coverage.  ``unknown`` and
``unavailable`` never count as covered, and a missing universe denominator
always produces ``rate=None`` rather than a fabricated global percentage.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domains.events.radar_quality_audits import (
    audit_dealer_candidates,
    audit_event_catalog,
)
from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    REMEDIATION_QUEUE_ID,
    REMEDIATION_QUEUE_VERSION,
    _as_utc,
    _issue_counts,
    query_remediation_queue,
    _rate,
    _STABLE_LOCATION_RE,
)


def build_event_dealer_quality_audit(
    catalog: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_event_source_universe_denominator: Any = None,
    known_dealer_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Build the combined read-only contract consumed by Event Radar API."""
    now = _as_utc(as_of)
    dealer = audit_dealer_candidates(
        candidates,
        as_of=now,
        stale_after_days=stale_after_days,
        known_location_universe_denominator=known_dealer_location_universe_denominator,
    )
    dealer_locations = frozenset(
        str(record.get("exact_stable_location_key") or "").strip()
        for record in dealer["evidence_records"]["items"]
        if record.get("import_eligible") and record.get("exact_stable_location_key")
    )
    event = audit_event_catalog(
        catalog,
        as_of=now,
        stale_after_days=stale_after_days,
        known_source_universe_denominator=known_event_source_universe_denominator,
        reviewed_dealer_location_keys=dealer_locations,
    )
    opportunities = catalog.get("opportunities") if isinstance(catalog, dict) else []
    if not isinstance(opportunities, list):
        opportunities = []
    sources = catalog.get("sources") if isinstance(catalog, dict) else []
    if not isinstance(sources, list):
        sources = []
    source_kind_by_id = {
        str(item.get("id") or "").strip(): str(item.get("source_kind") or "").strip().casefold()
        for item in sources
        if isinstance(item, dict)
    }
    dealer_local = [
        item
        for item in opportunities
        if (
            isinstance(item, dict)
            and (
                str(item.get("lane") or "").strip().casefold() == "dealer_event"
                or (
                    str(item.get("lane") or "").strip().casefold() == "local_activity"
                    and source_kind_by_id.get(str(item.get("source_id") or "").strip())
                    == "dealer_event"
                )
            )
        )
    ]
    exact_keys = [str(item.get("dealer_stable_location_key") or "").strip() for item in dealer_local]
    valid_exact_keys = [key for key in exact_keys if _STABLE_LOCATION_RE.fullmatch(key)]
    exact_resolved = sum(1 for key in valid_exact_keys if key in dealer_locations)
    linkage_issues: list[dict[str, str]] = []
    for index, (item, key) in enumerate(zip(dealer_local, exact_keys)):
        opportunity_id = str(item.get("id") or f"index_{index}")
        path = f"opportunities[{opportunity_id}].dealer_stable_location_key"
        if not key:
            code = "event.dealer_location_key_missing"
            message = "dealer-backed opportunity requires an exact Dealer location key"
        elif not _STABLE_LOCATION_RE.fullmatch(key):
            code = "event.dealer_location_key_invalid"
            message = "Dealer location key does not satisfy the exact identity contract"
        elif key not in dealer_locations:
            code = "event.dealer_location_key_unresolved"
            message = "Dealer location key does not resolve to the supplied reviewed Dealer candidates"
        else:
            continue
        linkage_issues.append(
            {
                "severity": "error",
                "code": code,
                "path": path,
                "message": message,
                "scope": "linkage",
            }
        )
    linkage_ready = exact_resolved == len(dealer_local)
    linkage = {
        "denominator": len(dealer_local),
        "exact_location_keys_present": sum(1 for key in exact_keys if key),
        "valid_exact_location_keys_present": len(valid_exact_keys),
        "exact_location_keys_resolved": exact_resolved,
        "unknown_or_name_only_not_covered": len(dealer_local) - exact_resolved,
        "coverage": _rate(exact_resolved, len(dealer_local)),
        "matching_policy": "stable_location_key_exact_only",
        "ready": linkage_ready,
        "issues": linkage_issues,
    }

    combined_issues = [
        {**item, "scope": "event"} for item in event["issues"]
    ] + [
        {**item, "scope": "dealer"} for item in dealer["issues"]
    ] + linkage_issues
    counts = _issue_counts(combined_issues)
    full_import_ready = bool(
        event["import_gate"]["allowed"]
        and dealer["import_gate"]["allowed"]
        and linkage_ready
    )
    combined_quality_status = (
        "blocked_or_partial"
        if not full_import_ready
        else "verified_descriptive"
        if event["quality_status"] == dealer["quality_status"] == "verified_descriptive"
        else "partial_descriptive"
    )
    return {
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "generated_at": now.isoformat(),
            "read_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
        },
        "ok": counts["errors"] == 0,
        "quality_status": combined_quality_status,
        "claim_status": "descriptive_only",
        "event": event,
        "dealer": dealer,
        "dealer_event_linkage": linkage,
        "coverage_truth": {
            "event_source_universe_manifest_status": event["coverage"]["global_source_coverage"]["manifest_status"],
            "dealer_location_universe_manifest_status": dealer["coverage"]["global_location_coverage"]["manifest_status"],
            "known_event_source_universe_denominator": event["coverage"]["global_source_coverage"]["denominator"],
            "known_dealer_location_universe_denominator": dealer["coverage"]["global_location_coverage"]["denominator"],
            "global_event_source_coverage_rate": event["coverage"]["global_source_coverage"]["rate"],
            "global_dealer_location_coverage_rate": dealer["coverage"]["global_location_coverage"]["rate"],
            "global_full_coverage_claim_allowed": False,
            "unknown_counted_as_covered": False,
        },
        "import_gates": {
            "event_catalog": event["import_gate"],
            "dealer_candidates": dealer["import_gate"],
            "combined_ready": full_import_ready,
        },
        "issue_counts": counts,
        "issues": sorted(combined_issues, key=lambda item: (item["severity"], item["scope"], item["code"], item["path"])),
    }




from app.domains.events.radar_remediation import (  # noqa: E402
    build_dealer_remediation_queue,
    build_event_dealer_remediation_queue,
    build_event_remediation_queue,
)


__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DEFAULT_STALE_AFTER_DAYS",
    "REMEDIATION_QUEUE_ID",
    "REMEDIATION_QUEUE_VERSION",
    "audit_dealer_candidates",
    "audit_event_catalog",
    "build_dealer_remediation_queue",
    "build_event_dealer_quality_audit",
    "build_event_dealer_remediation_queue",
    "build_event_remediation_queue",
    "query_remediation_queue",
]
