"""Pure Event catalog audit with a compatibility Dealer re-export."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable

from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    _as_utc,
    _exact_linkage_coverage,
    _freshness,
    _global_coverage,
    _issue_counts,
    _issue_factory,
    _rate,
    _reviewed_location_key_universe,
)
from app.domains.events.radar_quality_dealer_audit import audit_dealer_candidates
from app.domains.events.radar_quality_event_opportunities import (
    EventOpportunityAudit,
    audit_event_opportunities,
)
from app.domains.events.radar_quality_event_sources import (
    EventSourceAudit,
    audit_event_sources,
)


Issue = Callable[[str, str, str, str], None]


def _catalog_rows(
    payload: dict[str, Any], *, field_name: str, issue: Issue
) -> list[Any]:
    rows = payload.get(field_name)
    if isinstance(rows, list):
        return rows
    issue(
        "error",
        f"event.{field_name}_type",
        field_name,
        f"{field_name} must be an array",
    )
    return []


def _audit_catalog_envelope(payload: dict[str, Any], *, issue: Issue) -> None:
    if payload.get("global_complete") is not False:
        issue(
            "error",
            "event.global_complete",
            "global_complete",
            "global_complete must remain false",
        )
    if (
        str(payload.get("coverage_claim") or "")
        != "registered_publisher_owned_public_entries_only"
    ):
        issue(
            "error",
            "event.coverage_claim",
            "coverage_claim",
            "coverage must stay bounded to registered publisher-owned public entries",
        )


def _duplicate_values(values: list[Any]) -> list[Any]:
    counts = Counter(value for value in values if value not in (None, "", ("", "")))
    return sorted(value for value, count in counts.items() if count > 1)


def _audit_duplicate_keys(
    sources: EventSourceAudit,
    opportunities: EventOpportunityAudit,
    *,
    issue: Issue,
) -> dict[str, list[Any]]:
    duplicate_sets = {
        "source_ids": _duplicate_values(sources.source_ids),
        "source_urls": _duplicate_values(sources.source_urls),
        "opportunity_ids": _duplicate_values(opportunities.opportunity_ids),
        "canonical_keys": _duplicate_values(opportunities.canonical_keys),
        "external_keys": _duplicate_values(opportunities.external_keys),
    }
    for code, values in (
        ("event.source_id_duplicate", duplicate_sets["source_ids"]),
        ("event.source_url_duplicate", duplicate_sets["source_urls"]),
        ("event.opportunity_id_duplicate", duplicate_sets["opportunity_ids"]),
        ("event.canonical_key_duplicate", duplicate_sets["canonical_keys"]),
        ("event.external_key_duplicate", duplicate_sets["external_keys"]),
    ):
        for value in values:
            issue("error", code, "catalog", f"exact duplicate key: {value!r}")
    return duplicate_sets


def _observed_source_inventory(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": str(raw.get("id") or "").strip(),
            "source_id": str(raw.get("id") or "").strip(),
            "canonical_url": str(raw.get("canonical_url") or "").strip(),
        }
        if isinstance(raw, dict)
        else {"entity_id": None, "source_id": None, "canonical_url": None}
        for raw in rows
    ]


def _event_coverage(
    sources: EventSourceAudit,
    opportunities: EventOpportunityAudit,
    *,
    known_source_universe_denominator: Any,
    reviewed_dealer_locations: frozenset[str] | None,
    issue: Issue,
) -> dict[str, Any]:
    active_fresh_source_count = len(sources.active_fresh_ids)
    return {
        "reviewed_active_sources": _rate(sources.active_count, len(sources.rows)),
        "source_row_freshness": _rate(sources.fresh_count, len(sources.rows)),
        "source_review_evidence": _rate(
            sources.review_evidence_count, len(sources.rows)
        ),
        "activity_url_presence": _rate(
            opportunities.activity_url_presence_count, len(opportunities.rows)
        ),
        "activity_evidence": _rate(
            opportunities.activity_evidence_count, len(opportunities.rows)
        ),
        "viltrox_presence_evidence": _rate(
            opportunities.viltrox_evidence_count, len(opportunities.rows)
        ),
        "exact_dealer_location_linkage": _exact_linkage_coverage(
            opportunities.exact_dealer_links,
            opportunities.dealer_local_count,
            reviewed_dealer_locations,
        ),
        "global_source_coverage": _global_coverage(
            active_fresh_source_count,
            known_source_universe_denominator,
            observed_inventory=_observed_source_inventory(sources.rows),
            issue=issue,
            code="event.global_source_coverage",
            path="known_source_universe_denominator",
            expected_scope="event_sources",
        ),
    }


def _event_import_allowed(
    *,
    counts: dict[str, int],
    sources: EventSourceAudit,
    opportunities: EventOpportunityAudit,
    catalog_freshness: dict[str, Any],
) -> bool:
    return all(
        (
            counts["errors"] == 0,
            bool(sources.rows),
            bool(opportunities.rows),
            catalog_freshness["status"] == "fresh",
            sources.fresh_count == len(sources.rows),
            sources.review_evidence_count == len(sources.rows),
            opportunities.activity_evidence_count == len(opportunities.rows),
        )
    )


def _optional_evidence_complete(
    *,
    sources: EventSourceAudit,
    opportunities: EventOpportunityAudit,
    coverage: dict[str, Any],
) -> bool:
    return all(
        (
            sources.fresh_count == len(sources.rows),
            sources.review_evidence_count == len(sources.rows),
            opportunities.viltrox_evidence_count == len(opportunities.rows),
            opportunities.exact_dealer_links == opportunities.dealer_local_count,
            coverage["global_source_coverage"]["manifest_status"] == "accepted",
        )
    )


def _quality_status(*, import_allowed: bool, optional_complete: bool) -> str:
    if not import_allowed:
        return "blocked_for_import"
    return "verified_descriptive" if optional_complete else "partial_descriptive"


def _event_report(
    *,
    now: datetime,
    stale_after_days: int,
    catalog_freshness: dict[str, Any],
    sources: EventSourceAudit,
    opportunities: EventOpportunityAudit,
    coverage: dict[str, Any],
    duplicate_sets: dict[str, list[Any]],
    issues: list[dict[str, str]],
    counts: dict[str, int],
    import_allowed: bool,
    optional_complete: bool,
) -> dict[str, Any]:
    return {
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "scope": "event_catalog",
        },
        "ok": counts["errors"] == 0,
        "quality_status": _quality_status(
            import_allowed=import_allowed,
            optional_complete=optional_complete,
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
            "reviewed_sources": len(sources.rows),
            "active_sources_with_current_check": len(sources.active_fresh_ids),
            "reviewed_opportunities": len(opportunities.rows),
            "dealer_or_local_opportunities": opportunities.dealer_local_count,
            "name_only_dealer_hints": opportunities.name_only_dealer_hints,
            "declared_dealer_location_keys": opportunities.declared_dealer_links,
            "exact_dealer_location_links": opportunities.exact_dealer_links,
        },
        "coverage": coverage,
        "evidence_records": {
            "source_grain": "one_record_per_catalog_source",
            "opportunity_grain": "one_record_per_catalog_opportunity",
            "association_policy": "source_id_and_stable_location_key_exact_only",
            "sources": sources.evidence_records,
            "opportunities": opportunities.evidence_records,
        },
        "deduplication": {
            "mode": "exact_keys_only_no_fuzzy_auto_merge",
            "source_key": "id",
            "opportunity_keys": [
                "id",
                "canonical_key",
                "(source_id,external_event_key)",
            ],
            "duplicate_source_ids": duplicate_sets["source_ids"],
            "duplicate_source_urls": duplicate_sets["source_urls"],
            "duplicate_opportunity_ids": duplicate_sets["opportunity_ids"],
            "duplicate_canonical_keys": duplicate_sets["canonical_keys"],
            "duplicate_external_keys": [
                list(value) for value in duplicate_sets["external_keys"]
            ],
        },
        "import_gate": {
            "allowed": import_allowed,
            "reason": (
                "quality_contract_passed"
                if import_allowed
                else "catalog_identity_or_activity_evidence_failed"
            ),
            "does_not_prove_global_coverage": True,
        },
        "claim_boundaries": {
            "global_full_coverage_claim_allowed": False,
            "unknown_viltrox_presence_counted_as_covered": False,
            "event_listing_proves_viltrox_participation": False,
            "event_listing_proves_attendance_or_sales": False,
        },
        "issue_counts": counts,
        "issues": sorted(
            issues,
            key=lambda item: (item["severity"], item["code"], item["path"]),
        ),
    }


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
    reviewed_dealer_locations = _reviewed_location_key_universe(
        reviewed_dealer_location_keys
    )
    payload = deepcopy(catalog or {})
    issues: list[dict[str, str]] = []
    issue = _issue_factory(issues)
    sources_rows = _catalog_rows(payload, field_name="sources", issue=issue)
    opportunity_rows = _catalog_rows(
        payload, field_name="opportunities", issue=issue
    )
    _audit_catalog_envelope(payload, issue=issue)
    catalog_freshness = _freshness(
        payload.get("checked_at"),
        as_of=now,
        stale_after_days=stale_after_days,
    )
    if catalog_freshness["status"] != "fresh":
        issue(
            "error",
            "event.catalog_not_fresh",
            "checked_at",
            "reviewed catalog snapshot is stale or unavailable",
        )

    sources = audit_event_sources(
        sources_rows,
        now=now,
        stale_after_days=stale_after_days,
        issue=issue,
    )
    if sources.rows and sources.fresh_count != len(sources.rows):
        issue(
            "error",
            "event.source_freshness_incomplete",
            "sources[*].source_checked_at",
            "every imported source requires its own current source_checked_at; catalog checked_at is not a substitute",
        )
    if sources.rows and sources.review_evidence_count != len(sources.rows):
        issue(
            "error",
            "event.source_review_evidence_incomplete",
            "sources[*].reviewer_id",
            "every imported source requires safe reviewer_id plus explicit evidence_scope and value_status",
        )

    opportunities = audit_event_opportunities(
        opportunity_rows,
        source_by_id=sources.source_by_id,
        reviewed_locations=reviewed_dealer_locations,
        catalog_checked_at=payload.get("checked_at"),
        now=now,
        stale_after_days=stale_after_days,
        issue=issue,
    )
    duplicate_sets = _audit_duplicate_keys(sources, opportunities, issue=issue)
    coverage = _event_coverage(
        sources,
        opportunities,
        known_source_universe_denominator=known_source_universe_denominator,
        reviewed_dealer_locations=reviewed_dealer_locations,
        issue=issue,
    )
    if opportunities.viltrox_evidence_count < len(opportunities.rows):
        issue(
            "warning",
            "event.viltrox_presence_evidence_incomplete",
            "opportunities",
            "unknown Viltrox presence is not covered",
        )
    if opportunities.exact_dealer_links < opportunities.dealer_local_count:
        issue(
            "warning",
            "event.dealer_location_linkage_incomplete",
            "opportunities",
            "name hints do not count as exact Dealer location linkage",
        )
    if known_source_universe_denominator is None:
        issue(
            "warning",
            "event.global_denominator_unavailable",
            "known_source_universe_denominator",
            "global Event source coverage cannot be calculated",
        )
    counts = _issue_counts(issues)
    import_allowed = _event_import_allowed(
        counts=counts,
        sources=sources,
        opportunities=opportunities,
        catalog_freshness=catalog_freshness,
    )
    optional_complete = _optional_evidence_complete(
        sources=sources,
        opportunities=opportunities,
        coverage=coverage,
    )
    return _event_report(
        now=now,
        stale_after_days=stale_after_days,
        catalog_freshness=catalog_freshness,
        sources=sources,
        opportunities=opportunities,
        coverage=coverage,
        duplicate_sets=duplicate_sets,
        issues=issues,
        counts=counts,
        import_allowed=import_allowed,
        optional_complete=optional_complete,
    )


__all__ = ["audit_dealer_candidates", "audit_event_catalog"]
