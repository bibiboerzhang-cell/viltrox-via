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
    COUNTRY_RE as _COUNTRY_RE,
    DEALER_LOCAL_LANES,
    DEFAULT_STALE_AFTER_DAYS,
    PRIMARY_PUBLISHER_TIERS,
    PUBLISHER_TIERS,
    SNAPSHOT_VERSION,
    SOCIAL_PLATFORMS,
    SOURCE_ID_RE as _SOURCE_ID_RE,
    STABLE_LOCATION_RE as _STABLE_LOCATION_RE,
    STABLE_ORG_RE as _STABLE_ORG_RE,
    add_issue as _issue,
    as_utc as _as_utc,
    evidence_result as _evidence_result,
    freshness as _freshness,
    publisher_passport as _publisher_passport,
    rate as _rate,
)
from app.domains.source_passport_urls import canonical_source_url, source_url_identity


def compare_source_snapshots(
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Lazy wrapper avoids coupling core validation to snapshot persistence."""
    from app.domains.source_passport_snapshot import compare_source_snapshots as compare

    return compare(current, previous)


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
    now = _as_utc(as_of)
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
        _issue(
            issues,
            "error",
            "catalog.global_complete_must_be_false",
            "global_complete",
            "the reviewed catalog cannot claim global completeness",
        )

    source_ids: list[str] = []
    canonical_source_urls: list[str] = []
    valid_source_urls = 0
    publisher_declared = 0
    publisher_verified = 0
    publisher_primary = 0
    publisher_secondary = 0
    fresh_source_rows = 0
    source_by_id: dict[str, Mapping[str, Any]] = {}
    publisher_tiers: Counter[str] = Counter()

    for index, raw in enumerate(sources):
        path = f"sources[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "error", "source.row_invalid", path, "source must be an object")
            continue
        source_id = str(raw.get("id") or "").strip()
        source_ids.append(source_id)
        if not _SOURCE_ID_RE.fullmatch(source_id):
            _issue(
                issues,
                "error",
                "source.id_invalid",
                f"{path}.id",
                "stable source id is required",
            )
        elif source_id not in source_by_id:
            source_by_id[source_id] = raw
        url_identity = source_url_identity(raw.get("canonical_url"))
        if url_identity["valid"]:
            valid_source_urls += 1
            canonical_source_urls.append(str(url_identity["canonical_url"]))
        else:
            _issue(
                issues,
                "error",
                "source.canonical_url_invalid",
                f"{path}.canonical_url",
                "credential-free canonical HTTPS URL is required",
            )
        passport = _publisher_passport(
            raw,
            as_of=now,
            stale_after_days=stale_after_days,
        )
        publisher_tiers[str(passport["publisher_tier"])] += 1
        if passport["declared"]:
            publisher_declared += 1
        else:
            _issue(
                issues,
                "warning",
                "source.publisher_tier_missing",
                f"{path}.publisher_tier",
                "publisher relationship tier is not declared",
            )
        if passport["verified"]:
            publisher_verified += 1
            if passport["publisher_tier"] in PRIMARY_PUBLISHER_TIERS:
                publisher_primary += 1
            elif passport["publisher_tier"] in SECONDARY_PUBLISHER_TIERS:
                publisher_secondary += 1
        elif passport["declared"]:
            _issue(
                issues,
                "warning",
                "source.publisher_identity_unverified",
                f"{path}.publisher_identity_evidence",
                "declared publisher tier lacks current structured identity evidence",
            )
        source_freshness = _freshness(
            raw.get("verified_at") or raw.get("source_checked_at"),
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if source_freshness["status"] == "fresh":
            fresh_source_rows += 1
        else:
            _issue(
                issues,
                "warning",
                "source.verification_not_fresh",
                f"{path}.verified_at",
                "source row has no current timezone-aware verification anchor",
            )

    duplicate_source_ids = sorted(
        value for value, count in Counter(source_ids).items() if value and count > 1
    )
    duplicate_source_urls = sorted(
        value
        for value, count in Counter(canonical_source_urls).items()
        if value and count > 1
    )
    for value in duplicate_source_ids:
        _issue(
            issues,
            "error",
            "source.id_duplicate",
            "sources",
            f"duplicate source id: {value}",
        )
    for value in duplicate_source_urls:
        _issue(
            issues,
            "error",
            "source.url_identity_duplicate",
            "sources",
            f"duplicate canonical source URL identity: {value}",
        )

    opportunity_source_links = 0
    valid_opportunity_urls = 0
    fresh_opportunity_evidence = 0
    dealer_local_count = 0
    dealer_location_links = 0
    reviewed_dealer_location_key_set = {
        str(item.get("stable_location_key") or "").strip()
        for item in dealers
        if isinstance(item, Mapping)
        and _STABLE_LOCATION_RE.fullmatch(
            str(item.get("stable_location_key") or "").strip()
        )
    }
    opportunity_ids: list[str] = []
    canonical_keys: list[str] = []
    external_keys: list[tuple[str, str]] = []
    for index, raw in enumerate(opportunities):
        path = f"opportunities[{index}]"
        if not isinstance(raw, Mapping):
            _issue(
                issues,
                "error",
                "opportunity.row_invalid",
                path,
                "opportunity must be an object",
            )
            continue
        opportunity_id = str(raw.get("id") or "").strip()
        canonical_key = str(raw.get("canonical_key") or "").strip()
        source_id = str(raw.get("source_id") or "").strip()
        external_key = str(raw.get("external_event_key") or "").strip()
        opportunity_ids.append(opportunity_id)
        canonical_keys.append(canonical_key)
        external_keys.append((source_id, external_key))
        if source_id in source_by_id:
            opportunity_source_links += 1
        else:
            _issue(
                issues,
                "error",
                "opportunity.source_orphan",
                f"{path}.source_id",
                "opportunity source id does not resolve exactly",
            )
        if canonical_source_url(raw.get("official_url")):
            valid_opportunity_urls += 1
        else:
            _issue(
                issues,
                "error",
                "opportunity.official_url_invalid",
                f"{path}.official_url",
                "credential-free canonical HTTPS activity URL is required",
            )
        evidence = _evidence_result(
            raw.get("activity_evidence"),
            expected_scope="event_official_listing",
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if evidence["valid"]:
            fresh_opportunity_evidence += 1
        else:
            _issue(
                issues,
                "warning",
                "opportunity.activity_evidence_incomplete",
                f"{path}.activity_evidence",
                "activity requires current structured evidence with publisher tier",
            )
        lane = str(raw.get("lane") or "").strip()
        if lane in DEALER_LOCAL_LANES:
            dealer_local_count += 1
            location_key = str(raw.get("dealer_stable_location_key") or "").strip()
            if location_key in reviewed_dealer_location_key_set:
                dealer_location_links += 1
            else:
                issue_code = (
                    "opportunity.dealer_location_key_unresolved"
                    if _STABLE_LOCATION_RE.fullmatch(location_key)
                    else "opportunity.dealer_location_key_missing"
                )
                _issue(
                    issues,
                    "warning",
                    issue_code,
                    f"{path}.dealer_stable_location_key",
                    "dealer/local activity lacks a resolved exact reviewed Dealer location key",
                )

    for code, values, path in (
        ("opportunity.id_duplicate", opportunity_ids, "opportunities"),
        ("opportunity.canonical_key_duplicate", canonical_keys, "opportunities"),
        ("opportunity.external_key_duplicate", external_keys, "opportunities"),
    ):
        for value, count in Counter(values).items():
            if value not in ("", ("", "")) and count > 1:
                _issue(issues, "error", code, path, f"duplicate exact identity: {value!r}")

    dealer_source_ids: list[str] = []
    dealer_stable_org_keys: list[str] = []
    dealer_stable_location_keys: list[str] = []
    dealer_natural_keys: list[tuple[str, str, str]] = []
    dealer_location_urls: list[str] = []
    valid_dealer_location_urls = 0
    valid_dealer_product_urls = 0
    dealer_publisher_declared = 0
    dealer_publisher_verified = 0
    fresh_dealer_rows = 0
    contact_values = 0
    contact_evidence_current = 0
    social_values = 0
    social_evidence_current = 0
    viltrox_product_evidence_current = 0
    dealer_activity_evidence_current = 0

    for index, raw in enumerate(dealers):
        path = f"dealers[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "error", "dealer.row_invalid", path, "dealer must be an object")
            continue
        source_id = str(raw.get("source_id") or "").strip()
        stable_org_key = str(raw.get("stable_org_key") or "").strip()
        stable_location_key = str(raw.get("stable_location_key") or "").strip()
        country = str(raw.get("country_code") or raw.get("country") or "").strip().upper()
        dealer_source_ids.append(source_id)
        dealer_stable_org_keys.append(stable_org_key)
        dealer_stable_location_keys.append(stable_location_key)
        dealer_natural_keys.append(
            (
                str(raw.get("name") or "").strip().casefold(),
                str(raw.get("address") or "").strip().casefold(),
                country,
            )
        )
        if source_id and not _SOURCE_ID_RE.fullmatch(source_id):
            _issue(
                issues,
                "error",
                "dealer.source_id_invalid",
                f"{path}.source_id",
                "Dealer source id does not satisfy the stable identity contract",
            )
        if stable_org_key and not _STABLE_ORG_RE.fullmatch(stable_org_key):
            _issue(
                issues,
                "error",
                "dealer.stable_org_key_invalid",
                f"{path}.stable_org_key",
                "Dealer organization key is invalid",
            )
        if stable_location_key and not _STABLE_LOCATION_RE.fullmatch(stable_location_key):
            _issue(
                issues,
                "error",
                "dealer.stable_location_key_invalid",
                f"{path}.stable_location_key",
                "Dealer location key is invalid",
            )
        if country and not _COUNTRY_RE.fullmatch(country):
            _issue(
                issues,
                "error",
                "dealer.country_invalid",
                f"{path}.country",
                "Dealer country must be ISO alpha-2",
            )
        location_url = canonical_source_url(raw.get("location_source_url"))
        if location_url:
            valid_dealer_location_urls += 1
            dealer_location_urls.append(location_url)
        else:
            _issue(
                issues,
                "error",
                "dealer.location_source_url_invalid",
                f"{path}.location_source_url",
                "Dealer location requires a canonical public HTTPS source",
            )
        if canonical_source_url(raw.get("brand_listing_url")):
            valid_dealer_product_urls += 1
        passport = _publisher_passport(
            raw,
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if passport["declared"]:
            dealer_publisher_declared += 1
        else:
            _issue(
                issues,
                "warning",
                "dealer.publisher_tier_missing",
                f"{path}.publisher_tier",
                "Dealer publisher relationship tier is not declared",
            )
        if passport["verified"]:
            dealer_publisher_verified += 1
        elif passport["declared"]:
            _issue(
                issues,
                "warning",
                "dealer.publisher_identity_unverified",
                f"{path}.publisher_identity_evidence",
                "declared Dealer publisher tier lacks current identity evidence",
            )
        row_freshness = _freshness(
            raw.get("verified_at") or raw.get("source_checked_at"),
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if row_freshness["status"] == "fresh":
            fresh_dealer_rows += 1
        else:
            _issue(
                issues,
                "warning",
                "dealer.verification_not_fresh",
                f"{path}.verified_at",
                "Dealer location has no current timezone-aware verification anchor",
            )
        contact_map = raw.get("contact_evidence")
        if not isinstance(contact_map, Mapping):
            contact_map = {}
        for field in CONTACT_FIELDS:
            if raw.get(field) not in (None, ""):
                contact_values += 1
                contact_result = _evidence_result(
                    contact_map.get(field),
                    expected_scope="dealer_contact_field",
                    as_of=now,
                    stale_after_days=stale_after_days,
                )
                if contact_result["valid"]:
                    contact_evidence_current += 1
                else:
                    _issue(
                        issues,
                        "warning",
                        "dealer.contact_evidence_incomplete",
                        f"{path}.contact_evidence.{field}",
                        "populated contact field lacks current field-level evidence",
                    )
        social_map = raw.get("social_evidence")
        if not isinstance(social_map, Mapping):
            social_map = {}
        for platform in SOCIAL_PLATFORMS:
            evidence = social_map.get(platform)
            if isinstance(evidence, Mapping) and canonical_source_url(evidence.get("source_url")):
                social_values += 1
            if _evidence_result(
                evidence,
                expected_scope="dealer_social_profile",
                as_of=now,
                stale_after_days=stale_after_days,
            )["valid"]:
                social_evidence_current += 1
        product_result = _evidence_result(
            raw.get("viltrox_product_evidence"),
            expected_scope="dealer_viltrox_product_page",
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if product_result["valid"]:
            viltrox_product_evidence_current += 1
        else:
            _issue(
                issues,
                "warning",
                "dealer.viltrox_product_evidence_incomplete",
                f"{path}.viltrox_product_evidence",
                "declared product URL is not current structured Viltrox page evidence",
            )
        activity_result = _evidence_result(
            raw.get("activity_evidence"),
            expected_scope="dealer_activity_page",
            as_of=now,
            stale_after_days=stale_after_days,
        )
        if activity_result["valid"]:
            dealer_activity_evidence_current += 1
        else:
            _issue(
                issues,
                "warning",
                "dealer.activity_evidence_incomplete",
                f"{path}.activity_evidence",
                "Dealer activity page has not been evidenced at this location grain",
            )
        if sum(
            1
            for platform in SOCIAL_PLATFORMS
            if _evidence_result(
                social_map.get(platform),
                expected_scope="dealer_social_profile",
                as_of=now,
                stale_after_days=stale_after_days,
            )["valid"]
        ) < len(SOCIAL_PLATFORMS):
            _issue(
                issues,
                "warning",
                "dealer.social_evidence_incomplete",
                f"{path}.social_evidence",
                "one or more social-platform slots lack current profile evidence",
            )

    duplicate_dealer_source_ids = sorted(
        value
        for value, count in Counter(dealer_source_ids).items()
        if value and count > 1
    )
    duplicate_dealer_location_keys = sorted(
        value
        for value, count in Counter(dealer_stable_location_keys).items()
        if value and count > 1
    )
    duplicate_dealer_natural_keys = sorted(
        value
        for value, count in Counter(dealer_natural_keys).items()
        if value != ("", "", "") and count > 1
    )
    shared_dealer_source_urls = {
        value: count
        for value, count in sorted(Counter(dealer_location_urls).items())
        if value and count > 1
    }
    for code, values in (
        ("dealer.source_id_duplicate", duplicate_dealer_source_ids),
        ("dealer.stable_location_key_duplicate", duplicate_dealer_location_keys),
        ("dealer.natural_key_duplicate", duplicate_dealer_natural_keys),
    ):
        for value in values:
            _issue(issues, "error", code, "dealers", f"duplicate exact identity: {value!r}")

    from app.domains.source_passport_snapshot import build_source_snapshot

    snapshot = build_source_snapshot(catalog, dealers, generated_at=now)
    change_detection = compare_source_snapshots(snapshot, previous_snapshot)
    if change_detection["identity_drift"]:
        _issue(
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
    local_readiness = bool(
        source_count
        and valid_source_urls == source_count
        and publisher_verified == source_count
        and fresh_source_rows == source_count
        and opportunity_source_links == opportunity_count
        and valid_opportunity_urls == opportunity_count
        and fresh_opportunity_evidence == opportunity_count
        and dealer_count
        and valid_dealer_location_urls == dealer_count
        and dealer_publisher_verified == dealer_count
        and fresh_dealer_rows == dealer_count
        and viltrox_product_evidence_current == dealer_count
        and dealer_location_links == dealer_local_count
        and severity_counts["error"] == 0
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
        "event_sources": {
            "counts": {
                "rows": source_count,
                "valid_canonical_urls": valid_source_urls,
                "publisher_tier_declared": publisher_declared,
                "publisher_identity_verified": publisher_verified,
                "verified_primary_publishers": publisher_primary,
                "verified_secondary_publishers": publisher_secondary,
                "fresh_source_rows": fresh_source_rows,
            },
            "coverage": {
                "canonical_url_identity": _rate(valid_source_urls, source_count),
                "publisher_tier_declaration": _rate(publisher_declared, source_count),
                "publisher_identity_verification": _rate(publisher_verified, source_count),
                "row_freshness": _rate(fresh_source_rows, source_count),
            },
            "publisher_tiers": dict(sorted(publisher_tiers.items())),
            "deduplication": {
                "duplicate_source_ids": duplicate_source_ids,
                "duplicate_canonical_url_identities": duplicate_source_urls,
            },
        },
        "event_opportunities": {
            "counts": {
                "rows": opportunity_count,
                "exact_source_links": opportunity_source_links,
                "valid_official_urls": valid_opportunity_urls,
                "fresh_activity_evidence": fresh_opportunity_evidence,
                "dealer_or_local_rows": dealer_local_count,
                "exact_dealer_location_links": dealer_location_links,
            },
            "coverage": {
                "exact_source_linkage": _rate(opportunity_source_links, opportunity_count),
                "official_url_identity": _rate(valid_opportunity_urls, opportunity_count),
                "activity_evidence": _rate(fresh_opportunity_evidence, opportunity_count),
                "exact_dealer_location_linkage": _rate(
                    dealer_location_links, dealer_local_count
                ),
            },
        },
        "dealer_locations": {
            "counts": {
                "rows": dealer_count,
                "explicit_source_ids": sum(
                    1 for value in dealer_source_ids if _SOURCE_ID_RE.fullmatch(value)
                ),
                "stable_org_keys": sum(
                    1 for value in dealer_stable_org_keys if _STABLE_ORG_RE.fullmatch(value)
                ),
                "stable_location_keys": sum(
                    1
                    for value in dealer_stable_location_keys
                    if _STABLE_LOCATION_RE.fullmatch(value)
                ),
                "valid_location_urls": valid_dealer_location_urls,
                "valid_declared_viltrox_product_urls": valid_dealer_product_urls,
                "publisher_tier_declared": dealer_publisher_declared,
                "publisher_identity_verified": dealer_publisher_verified,
                "fresh_rows": fresh_dealer_rows,
                "populated_contact_fields": contact_values,
                "current_contact_evidence": contact_evidence_current,
                "declared_social_profiles": social_values,
                "current_social_evidence": social_evidence_current,
                "current_viltrox_product_evidence": viltrox_product_evidence_current,
                "current_activity_evidence": dealer_activity_evidence_current,
            },
            "coverage": {
                "source_id": _rate(
                    sum(1 for value in dealer_source_ids if _SOURCE_ID_RE.fullmatch(value)),
                    dealer_count,
                ),
                "stable_location_identity": _rate(
                    sum(
                        1
                        for value in dealer_stable_location_keys
                        if _STABLE_LOCATION_RE.fullmatch(value)
                    ),
                    dealer_count,
                ),
                "location_url_identity": _rate(valid_dealer_location_urls, dealer_count),
                "publisher_identity_verification": _rate(
                    dealer_publisher_verified, dealer_count
                ),
                "row_freshness": _rate(fresh_dealer_rows, dealer_count),
                "contact_evidence": _rate(
                    contact_evidence_current, dealer_count * len(CONTACT_FIELDS)
                ),
                "social_evidence": _rate(
                    social_evidence_current, dealer_count * len(SOCIAL_PLATFORMS)
                ),
                "viltrox_product_evidence": _rate(
                    viltrox_product_evidence_current, dealer_count
                ),
                "activity_evidence": _rate(dealer_activity_evidence_current, dealer_count),
            },
            "deduplication": {
                "duplicate_source_ids": duplicate_dealer_source_ids,
                "duplicate_stable_location_keys": duplicate_dealer_location_keys,
                "duplicate_natural_keys": [list(value) for value in duplicate_dealer_natural_keys],
                "shared_location_source_urls": shared_dealer_source_urls,
                "shared_url_policy": (
                    "allowed_only_as_a_shared_listing_page; exact stable location keys remain required"
                ),
            },
        },
        "coverage_truth": {
            "observed_source_rows": source_count,
            "observed_dealer_location_rows": dealer_count,
            "observed_countries": country_codes,
            "observed_regions": region_names,
            "global_event_source_coverage": _rate(
                publisher_verified,
                None,
                reason="reviewed_global_event_source_universe_manifest_unavailable",
            ),
            "global_dealer_location_coverage": _rate(
                dealer_publisher_verified,
                None,
                reason="reviewed_global_dealer_location_universe_manifest_unavailable",
            ),
            "global_country_coverage": _rate(
                len(country_codes),
                None,
                reason="target_country_universe_manifest_unavailable",
            ),
            "global_full_coverage_claim_allowed": False,
        },
        "change_detection": change_detection,
        "snapshot": snapshot,
        "claim_boundaries": {
            "retailer_owned_means_viltrox_authorized": False,
            "viltrox_product_page_means_current_stock": False,
            "contact_page_means_response_or_sales": False,
            "activity_page_means_attendance_or_local_impact": False,
            "source_count_means_global_coverage": False,
        },
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
