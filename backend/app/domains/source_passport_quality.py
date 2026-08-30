"""Offline source-passport quality contract for Dealer and Event evidence.

The contract is intentionally independent from PostgreSQL, HTTP clients, task
workers and application routers.  It answers a narrow question: does each
catalog row carry enough explicit evidence to identify the publisher, judge
freshness and detect later changes without inferring global completeness?

Important truth boundaries:

* a first-party retailer page does not prove Viltrox authorization;
* a Viltrox product page does not prove stock, sales or attribution;
* a dealer activity page does not prove attendance or local impact; and
* a local catalog count is never reused as the global-universe denominator.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping

from app.domains.source_passport_core import (
    CONTACT_FIELDS,
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    PUBLISHER_TIERS,
    SNAPSHOT_VERSION,
    SOCIAL_PLATFORMS,
    SOURCE_ID_RE,
    STABLE_LOCATION_RE,
    STABLE_ORG_RE,
    add_issue,
    as_utc,
    rate,
)
from app.domains.source_passport_quality_dealers import (
    DealerLocationAudit,
    audit_dealer_locations,
)
from app.domains.source_passport_quality_opportunities import (
    EventOpportunityAudit,
    audit_event_opportunities,
)
from app.domains.source_passport_quality_sources import (
    EventSourceAudit,
    audit_event_sources,
)
from app.domains.source_passport_urls import canonical_source_url, source_url_identity


def compare_source_snapshots(
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Lazy wrapper avoids coupling core validation to snapshot persistence."""
    from app.domains.source_passport_snapshot import compare_source_snapshots as compare

    return compare(current, previous)


def _event_source_section(state: EventSourceAudit, row_count: int) -> dict[str, Any]:
    return {
        "counts": {
            "rows": row_count,
            "valid_canonical_urls": state.valid_urls,
            "publisher_tier_declared": state.publisher_declared,
            "publisher_identity_verified": state.publisher_verified,
            "verified_primary_publishers": state.publisher_primary,
            "verified_secondary_publishers": state.publisher_secondary,
            "fresh_source_rows": state.fresh_rows,
        },
        "coverage": {
            "canonical_url_identity": rate(state.valid_urls, row_count),
            "publisher_tier_declaration": rate(state.publisher_declared, row_count),
            "publisher_identity_verification": rate(state.publisher_verified, row_count),
            "row_freshness": rate(state.fresh_rows, row_count),
        },
        "publisher_tiers": dict(sorted(state.publisher_tiers.items())),
        "deduplication": {
            "duplicate_source_ids": state.duplicate_ids,
            "duplicate_canonical_url_identities": state.duplicate_urls,
        },
    }


def _event_opportunity_section(
    state: EventOpportunityAudit,
    row_count: int,
) -> dict[str, Any]:
    return {
        "counts": {
            "rows": row_count,
            "exact_source_links": state.source_links,
            "valid_official_urls": state.valid_urls,
            "fresh_activity_evidence": state.fresh_evidence,
            "dealer_or_local_rows": state.dealer_local_rows,
            "exact_dealer_location_links": state.dealer_location_links,
        },
        "coverage": {
            "exact_source_linkage": rate(state.source_links, row_count),
            "official_url_identity": rate(state.valid_urls, row_count),
            "activity_evidence": rate(state.fresh_evidence, row_count),
            "exact_dealer_location_linkage": rate(
                state.dealer_location_links,
                state.dealer_local_rows,
            ),
        },
    }


def _valid_count(values: list[str], pattern: Any) -> int:
    return sum(1 for value in values if pattern.fullmatch(value))


def _dealer_location_section(
    state: DealerLocationAudit,
    row_count: int,
) -> dict[str, Any]:
    explicit_source_ids = _valid_count(state.source_ids, SOURCE_ID_RE)
    stable_org_keys = _valid_count(state.stable_org_keys, STABLE_ORG_RE)
    stable_location_keys = _valid_count(state.stable_location_keys, STABLE_LOCATION_RE)
    return {
        "counts": {
            "rows": row_count,
            "explicit_source_ids": explicit_source_ids,
            "stable_org_keys": stable_org_keys,
            "stable_location_keys": stable_location_keys,
            "valid_location_urls": state.valid_location_urls,
            "valid_declared_viltrox_product_urls": state.valid_product_urls,
            "publisher_tier_declared": state.publisher_declared,
            "publisher_identity_verified": state.publisher_verified,
            "fresh_rows": state.fresh_rows,
            "populated_contact_fields": state.contact_values,
            "current_contact_evidence": state.current_contact_evidence,
            "declared_social_profiles": state.social_values,
            "current_social_evidence": state.current_social_evidence,
            "current_viltrox_product_evidence": state.current_product_evidence,
            "current_activity_evidence": state.current_activity_evidence,
        },
        "coverage": {
            "source_id": rate(explicit_source_ids, row_count),
            "stable_location_identity": rate(stable_location_keys, row_count),
            "location_url_identity": rate(state.valid_location_urls, row_count),
            "publisher_identity_verification": rate(state.publisher_verified, row_count),
            "row_freshness": rate(state.fresh_rows, row_count),
            "contact_evidence": rate(
                state.current_contact_evidence,
                row_count * len(CONTACT_FIELDS),
            ),
            "social_evidence": rate(
                state.current_social_evidence,
                row_count * len(SOCIAL_PLATFORMS),
            ),
            "viltrox_product_evidence": rate(state.current_product_evidence, row_count),
            "activity_evidence": rate(state.current_activity_evidence, row_count),
        },
        "deduplication": {
            "duplicate_source_ids": state.duplicate_source_ids,
            "duplicate_stable_location_keys": state.duplicate_location_keys,
            "duplicate_natural_keys": [list(value) for value in state.duplicate_natural_keys],
            "shared_location_source_urls": state.shared_location_urls,
            "shared_url_policy": (
                "allowed_only_as_a_shared_listing_page; exact stable location keys remain required"
            ),
        },
    }


def _coverage_truth(
    sources: list[Any],
    *,
    source_state: EventSourceAudit,
    dealer_state: DealerLocationAudit,
    dealer_count: int,
) -> dict[str, Any]:
    country_codes = sorted(
        {
            str(item.get("country_code") or "").strip().upper()
            for item in sources
            if isinstance(item, Mapping) and item.get("country_code")
        }
    )
    region_names = sorted(
        {
            str(item.get("region") or "").strip()
            for item in sources
            if isinstance(item, Mapping) and item.get("region")
        }
    )
    return {
        "observed_source_rows": len(sources),
        "observed_dealer_location_rows": dealer_count,
        "observed_countries": country_codes,
        "observed_regions": region_names,
        "global_event_source_coverage": rate(
            source_state.publisher_verified,
            None,
            reason="reviewed_global_event_source_universe_manifest_unavailable",
        ),
        "global_dealer_location_coverage": rate(
            dealer_state.publisher_verified,
            None,
            reason="reviewed_global_dealer_location_universe_manifest_unavailable",
        ),
        "global_country_coverage": rate(
            len(country_codes),
            None,
            reason="target_country_universe_manifest_unavailable",
        ),
        "global_full_coverage_claim_allowed": False,
    }


def _local_import_ready(
    *,
    source_state: EventSourceAudit,
    opportunity_state: EventOpportunityAudit,
    dealer_state: DealerLocationAudit,
    source_count: int,
    opportunity_count: int,
    dealer_count: int,
    error_count: int,
) -> bool:
    complete_checks = (
        source_state.valid_urls == source_count,
        source_state.publisher_verified == source_count,
        source_state.fresh_rows == source_count,
        opportunity_state.source_links == opportunity_count,
        opportunity_state.valid_urls == opportunity_count,
        opportunity_state.fresh_evidence == opportunity_count,
        dealer_state.valid_location_urls == dealer_count,
        dealer_state.publisher_verified == dealer_count,
        dealer_state.fresh_rows == dealer_count,
        dealer_state.current_product_evidence == dealer_count,
        opportunity_state.dealer_location_links == opportunity_state.dealer_local_rows,
        error_count == 0,
    )
    return bool(source_count and dealer_count and all(complete_checks))


def _claim_boundaries() -> dict[str, bool]:
    return {
        "retailer_owned_means_viltrox_authorized": False,
        "viltrox_product_page_means_current_stock": False,
        "contact_page_means_response_or_sales": False,
        "activity_page_means_attendance_or_local_impact": False,
        "source_count_means_global_coverage": False,
    }


def build_source_passport_quality_audit(
    catalog: dict[str, Any],
    dealer_candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    previous_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, read-only Dealer/Event source quality report."""
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    stale_after_days = int(stale_after_days)
    now = as_utc(as_of)
    catalog = deepcopy(catalog if isinstance(catalog, dict) else {})
    dealers = deepcopy(dealer_candidates if isinstance(dealer_candidates, list) else [])
    sources = catalog.get("sources") if isinstance(catalog.get("sources"), list) else []
    opportunities = (
        catalog.get("opportunities")
        if isinstance(catalog.get("opportunities"), list)
        else []
    )
    issues: list[dict[str, str]] = []
    if catalog.get("global_complete") is not False:
        add_issue(
            issues,
            "error",
            "catalog.global_complete_must_be_false",
            "global_complete",
            "the reviewed catalog cannot claim global completeness",
        )

    source_state = audit_event_sources(
        sources,
        issues=issues,
        as_of=now,
        stale_after_days=stale_after_days,
    )
    opportunity_state = audit_event_opportunities(
        opportunities,
        dealers,
        source_by_id=source_state.source_by_id,
        issues=issues,
        as_of=now,
        stale_after_days=stale_after_days,
    )
    dealer_state = audit_dealer_locations(
        dealers,
        issues=issues,
        as_of=now,
        stale_after_days=stale_after_days,
    )

    from app.domains.source_passport_snapshot import build_source_snapshot

    snapshot = build_source_snapshot(catalog, dealers, generated_at=now)
    change_detection = compare_source_snapshots(snapshot, previous_snapshot)
    if change_detection["identity_drift"]:
        add_issue(
            issues,
            "warning",
            "snapshot.identity_drift_detected",
            "change_detection.identity_drift",
            "stable entity keys changed one or more source identity fields",
        )

    severity_counts = Counter(item["severity"] for item in issues)
    source_count = len(sources)
    opportunity_count = len(opportunities)
    dealer_count = len(dealers)
    local_readiness = _local_import_ready(
        source_state=source_state,
        opportunity_state=opportunity_state,
        dealer_state=dealer_state,
        source_count=source_count,
        opportunity_count=opportunity_count,
        dealer_count=dealer_count,
        error_count=severity_counts["error"],
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
        "ok": severity_counts["error"] == 0,
        "quality_status": (
            "source_passports_ready_descriptive"
            if local_readiness
            else "source_passports_incomplete"
        ),
        "claim_status": "descriptive_only",
        "local_import_readiness": {
            "ready": local_readiness,
            "does_not_prove_global_completeness": True,
        },
        "event_sources": _event_source_section(source_state, source_count),
        "event_opportunities": _event_opportunity_section(
            opportunity_state,
            opportunity_count,
        ),
        "dealer_locations": _dealer_location_section(dealer_state, dealer_count),
        "coverage_truth": _coverage_truth(
            sources,
            source_state=source_state,
            dealer_state=dealer_state,
            dealer_count=dealer_count,
        ),
        "change_detection": change_detection,
        "snapshot": snapshot,
        "claim_boundaries": _claim_boundaries(),
        "issue_counts": {
            "errors": severity_counts["error"],
            "warnings": severity_counts["warning"],
        },
        "issues": sorted(
            issues,
            key=lambda item: (item["severity"], item["code"], item["path"]),
        ),
    }


__all__ = [
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "DEFAULT_STALE_AFTER_DAYS",
    "PUBLISHER_TIERS",
    "SNAPSHOT_VERSION",
    "build_source_passport_quality_audit",
    "canonical_source_url",
    "compare_source_snapshots",
    "source_url_identity",
]
