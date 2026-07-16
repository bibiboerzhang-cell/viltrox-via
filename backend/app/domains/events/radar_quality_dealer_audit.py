"""Pure, fail-closed quality audit for reviewed Dealer candidates."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    _CONTACT_FIELDS,
    _COUNTRY_RE,
    _NONPOSITIVE_TEXT,
    _SAFE_REVIEWER_ID_RE,
    _SOCIAL_PLATFORMS,
    _SOURCE_ID_RE,
    _STABLE_LOCATION_RE,
    _STABLE_ORG_RE,
    _UNSUPPORTED_POSITIVE_CLAIMS,
    _as_utc,
    _evidence_contract_valid,
    _evidence_covered,
    _freshness,
    _global_coverage,
    _identity_proposal,
    _is_https_url,
    _issue_counts,
    _issue_factory,
    _positive_claim,
    _rate,
    _review_status,
    _source_id_proposal,
)


def audit_dealer_candidates(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Audit Dealer candidate rows without DB or network access.

    Explicit persisted-looking keys are required for import eligibility.  A
    deterministic proposal is returned to help remediation, but a proposal is
    not counted as covered until the row carries the accepted key itself.
    """
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    stale_after_days = int(stale_after_days)
    rows = deepcopy(candidates or [])
    issues: list[dict[str, str]] = []
    issue = _issue_factory(issues)

    source_ids: list[str] = []
    source_identity: dict[str, tuple[str, str]] = {}
    stable_org_keys: list[str] = []
    stable_location_keys: list[str] = []
    normalized_natural_keys: list[tuple[str, str]] = []
    identity_covered = 0
    source_evidence_covered = 0
    product_evidence_covered = 0
    contact_evidence_covered = 0
    social_evidence_covered = 0
    activity_evidence_covered = 0
    verified_location_entities: set[str] = set()
    proposals: list[dict[str, str]] = []
    evidence_records: list[dict[str, Any]] = []

    for index, raw in enumerate(rows):
        path = f"dealers[{index}]"
        if not isinstance(raw, dict):
            issue("error", "dealer.row_type", path, "dealer candidate must be an object")
            evidence_records.append(
                {
                    "candidate_index": index,
                    "source_id": None,
                    "declared_stable_location_key": None,
                    "exact_stable_location_key": None,
                    "source_url": None,
                    "observed_at": None,
                    "declared_review_status": None,
                    "review_status": "invalid_candidate_row",
                    "reviewer_id": None,
                    "evidence_scope": None,
                    "value_status": None,
                    "association_status": "unlinked_no_fuzzy_match",
                    "import_eligible": False,
                }
            )
            continue
        row = raw
        name = str(row.get("name") or "").strip()
        address = str(row.get("address") or "").strip()
        country = str(row.get("country_code") or row.get("country") or "").strip().upper()
        if not name:
            issue("error", "dealer.name_missing", f"{path}.name", "dealer name is required")
        if not address:
            issue("error", "dealer.address_missing", f"{path}.address", "dealer address is required")
        if not _COUNTRY_RE.fullmatch(country):
            issue("error", "dealer.country_invalid", f"{path}.country", "country must be ISO alpha-2")
        normalized_natural_keys.append((name.casefold(), address.casefold()))

        source_id = str(row.get("source_id") or "").strip()
        source_ids.append(source_id)
        location_url = str(row.get("location_source_url") or "").strip()
        source_checked_at = row.get("source_checked_at")
        if not _SOURCE_ID_RE.fullmatch(source_id):
            issue("error", "dealer.source_id_missing_or_invalid", f"{path}.source_id", "stable source_id is required")
        if not _is_https_url(location_url):
            issue("error", "dealer.location_source_url_invalid", f"{path}.location_source_url", "official HTTPS location source is required")
        freshness = _freshness(source_checked_at, as_of=now, stale_after_days=stale_after_days)
        if freshness["status"] != "fresh":
            issue(
                "error",
                "dealer.source_freshness_unavailable" if freshness["status"] == "unavailable" else "dealer.source_not_fresh",
                f"{path}.source_checked_at",
                "a recent timezone-aware source_checked_at is required for import",
            )
        source_status = str(row.get("source_status") or "").casefold()
        if source_status != "public_listing_verified":
            issue(
                "error",
                "dealer.source_status_not_verified",
                f"{path}.source_status",
                "source_status must explicitly be public_listing_verified",
            )
        source_contract_valid = _evidence_contract_valid(
            row,
            expected_scope="dealer_location_listing",
        )
        if not source_contract_valid:
            issue(
                "error",
                "dealer.source_evidence_contract_invalid",
                f"{path}.reviewer_id",
                "Dealer source evidence requires safe reviewer_id, evidence_scope=dealer_location_listing, and value_status=observed",
            )
        source_covered = bool(
            _SOURCE_ID_RE.fullmatch(source_id)
            and _is_https_url(location_url)
            and freshness["status"] == "fresh"
            and source_status == "public_listing_verified"
            and source_contract_valid
        )
        if source_covered:
            source_evidence_covered += 1
        if source_id:
            identity = (location_url, str(source_checked_at or ""))
            previous = source_identity.get(source_id)
            if previous is not None and previous != identity:
                issue(
                    "error",
                    "dealer.source_id_conflict",
                    f"{path}.source_id",
                    "one source_id maps to conflicting source URL/check time",
                )
            else:
                source_identity[source_id] = identity

        stable_org_key = str(row.get("stable_org_key") or "").strip()
        stable_location_key = str(row.get("stable_location_key") or "").strip()
        stable_org_keys.append(stable_org_key)
        stable_location_keys.append(stable_location_key)
        try:
            proposed_org, proposed_location = _identity_proposal(row)
        except ValueError:
            proposed_org, proposed_location = "", ""
        proposals.append(
            {
                "name": name,
                "proposed_source_id": _source_id_proposal(location_url),
                "proposed_stable_org_key": proposed_org,
                "proposed_stable_location_key": proposed_location,
                "proposal_status": "candidate_only_not_persisted",
            }
        )
        valid_identity = bool(name and address and _COUNTRY_RE.fullmatch(country))
        if not _STABLE_ORG_RE.fullmatch(stable_org_key):
            valid_identity = False
            issue("error", "dealer.stable_org_key_missing_or_invalid", f"{path}.stable_org_key", "accepted stable organization key is required")
        elif proposed_org and stable_org_key != proposed_org:
            valid_identity = False
            issue("error", "dealer.stable_org_key_mismatch", f"{path}.stable_org_key", "stable organization key does not match the v1 exact identity contract")
        if not _STABLE_LOCATION_RE.fullmatch(stable_location_key):
            valid_identity = False
            issue("error", "dealer.stable_location_key_missing_or_invalid", f"{path}.stable_location_key", "accepted stable location key is required")
        elif proposed_location and stable_location_key != proposed_location:
            valid_identity = False
            issue("error", "dealer.stable_location_key_mismatch", f"{path}.stable_location_key", "stable location key does not match the v1 exact identity contract")
        if valid_identity:
            identity_covered += 1

        contact_evidence = row.get("contact_evidence")
        if not isinstance(contact_evidence, dict):
            contact_evidence = {}
        for field in _CONTACT_FIELDS:
            evidence = contact_evidence.get(field)
            if row.get(field) not in (None, "") and _evidence_covered(
                evidence,
                as_of=now,
                stale_after_days=stale_after_days,
                fallback_checked_at=source_checked_at,
                fallback_url=location_url,
                expected_scope="dealer_contact_field",
            ):
                contact_evidence_covered += 1

        social_evidence = row.get("social_evidence")
        if not isinstance(social_evidence, dict):
            social_evidence = {}
        for platform in _SOCIAL_PLATFORMS:
            if _evidence_covered(
                social_evidence.get(platform),
                as_of=now,
                stale_after_days=stale_after_days,
                fallback_checked_at=source_checked_at,
                expected_scope="dealer_social_profile",
            ):
                social_evidence_covered += 1

        product_evidence = row.get("viltrox_product_evidence")
        product_covered = _evidence_covered(
            product_evidence,
            as_of=now,
            stale_after_days=stale_after_days,
            fallback_checked_at=source_checked_at,
            fallback_url=row.get("brand_listing_url"),
            expected_scope="dealer_viltrox_product_page",
        )
        if product_covered:
            product_evidence_covered += 1
        else:
            issue(
                "error",
                "dealer.viltrox_product_evidence_missing_or_stale",
                f"{path}.viltrox_product_evidence",
                "a current structured page-presence evidence object is required for import",
            )

        activity_evidence = row.get("activity_evidence")
        if _evidence_covered(
            activity_evidence,
            as_of=now,
            stale_after_days=stale_after_days,
            fallback_checked_at=source_checked_at,
            expected_scope="dealer_activity_page",
        ):
            activity_evidence_covered += 1

        if source_covered and valid_identity and product_covered:
            verified_location_entities.add(stable_location_key)

        reviewer_id = str(row.get("reviewer_id") or "").strip()
        evidence_records.append(
            {
                "candidate_index": index,
                "source_id": source_id or None,
                "declared_stable_location_key": stable_location_key or None,
                "exact_stable_location_key": stable_location_key if valid_identity else None,
                "source_url": location_url if _is_https_url(location_url) else None,
                "observed_at": freshness["checked_at"],
                "declared_review_status": source_status or None,
                "review_status": _review_status(
                    declared_reviewed=source_status == "public_listing_verified",
                    evidence_contract_valid=source_contract_valid,
                    freshness_status=str(freshness["status"]),
                ),
                "reviewer_id": (
                    reviewer_id if _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id) else None
                ),
                "evidence_scope": str(row.get("evidence_scope") or "").strip() or None,
                "value_status": str(row.get("value_status") or "").strip().casefold() or None,
                "association_status": (
                    "exact_reviewed_location" if valid_identity else "unlinked_no_fuzzy_match"
                ),
                "import_eligible": bool(source_covered and valid_identity and product_covered),
            }
        )

        authorization_status = str(row.get("authorization_status") or "unknown").strip().casefold()
        if authorization_status not in _NONPOSITIVE_TEXT:
            issue(
                "error",
                "dealer.unsupported_authorization_claim",
                f"{path}.authorization_status",
                "public source evidence cannot establish Viltrox authorization",
            )
        for field in sorted(_UNSUPPORTED_POSITIVE_CLAIMS):
            if field in row and _positive_claim(row.get(field)):
                issue(
                    "error",
                    "dealer.unsupported_business_claim",
                    f"{path}.{field}",
                    f"unsupported positive business claim: {field}",
                )

    def duplicate_values(values: list[Any]) -> list[Any]:
        counts = Counter(value for value in values if value not in (None, "", ("", "")))
        return sorted(value for value, count in counts.items() if count > 1)

    duplicate_sources = duplicate_values(source_ids)
    duplicate_locations = duplicate_values(stable_location_keys)
    duplicate_natural_keys = duplicate_values(normalized_natural_keys)
    for value in duplicate_locations:
        issue("error", "dealer.stable_location_key_duplicate", "dealers", f"duplicate stable location key: {value}")
    for value in duplicate_natural_keys:
        issue("error", "dealer.exact_natural_key_duplicate", "dealers", f"duplicate exact normalized name/address: {value!r}")

    row_count = len(rows)
    unique_location_count = len({value for value in stable_location_keys if value})
    verified_location_count = len(verified_location_entities)
    observed_location_inventory = [
        {
            "entity_id": str(raw.get("stable_location_key") or "").strip(),
            "source_id": str(raw.get("source_id") or "").strip(),
            "canonical_url": str(raw.get("location_source_url") or "").strip(),
        }
        if isinstance(raw, dict)
        else {"entity_id": None, "source_id": None, "canonical_url": None}
        for raw in rows
    ]
    coverage = {
        "source_evidence": _rate(source_evidence_covered, row_count),
        "stable_identity": _rate(identity_covered, row_count),
        "contact_fields": _rate(contact_evidence_covered, row_count * len(_CONTACT_FIELDS)),
        "social_profiles": _rate(social_evidence_covered, row_count * len(_SOCIAL_PLATFORMS)),
        "viltrox_product_page_evidence": _rate(product_evidence_covered, row_count),
        "dealer_activity_evidence": _rate(activity_evidence_covered, row_count),
        "global_location_coverage": _global_coverage(
            verified_location_count,
            known_location_universe_denominator,
            observed_inventory=observed_location_inventory,
            issue=issue,
            code="dealer.global_location_coverage",
            path="known_location_universe_denominator",
            expected_scope="dealer_locations",
        ),
    }
    if contact_evidence_covered < row_count * len(_CONTACT_FIELDS):
        issue("warning", "dealer.contact_evidence_incomplete", "dealers", "missing contact values or field-level provenance are not covered")
    if social_evidence_covered < row_count * len(_SOCIAL_PLATFORMS):
        issue("warning", "dealer.social_evidence_incomplete", "dealers", "unknown or unavailable social profiles are not covered")
    if activity_evidence_covered < row_count:
        issue("warning", "dealer.activity_evidence_incomplete", "dealers", "dealer activity evidence is incomplete")
    if known_location_universe_denominator is None:
        issue("warning", "dealer.global_denominator_unavailable", "known_location_universe_denominator", "global Dealer coverage cannot be calculated")
    counts = _issue_counts(issues)
    import_allowed = (
        counts["errors"] == 0
        and row_count > 0
        and source_evidence_covered == row_count
        and identity_covered == row_count
        and product_evidence_covered == row_count
    )
    optional_evidence_complete = (
        contact_evidence_covered == row_count * len(_CONTACT_FIELDS)
        and social_evidence_covered == row_count * len(_SOCIAL_PLATFORMS)
        and activity_evidence_covered == row_count
        and coverage["global_location_coverage"]["manifest_status"] == "accepted"
    )
    quality_status = (
        "blocked_for_import"
        if not import_allowed
        else "verified_descriptive"
        if optional_evidence_complete
        else "partial_descriptive"
    )
    return {
        "contract": {"id": CONTRACT_ID, "version": CONTRACT_VERSION, "scope": "dealer_candidates"},
        "ok": counts["errors"] == 0,
        "quality_status": quality_status,
        "claim_status": "descriptive_only",
        "read_only": True,
        "network_accessed": False,
        "database_accessed": False,
        "business_rows_written": 0,
        "as_of": now.isoformat(),
        "stale_after_days": stale_after_days,
        "counts": {
            "candidate_locations": row_count,
            "explicit_source_ids": len({value for value in source_ids if value}),
            "explicit_stable_org_keys": len({value for value in stable_org_keys if value}),
            "explicit_stable_location_keys": unique_location_count,
            "verified_location_entities": verified_location_count,
        },
        "coverage": coverage,
        "evidence_records": {
            "grain": "one_record_per_input_candidate",
            "association_policy": "stable_location_key_exact_only_no_fuzzy_merge",
            "items": evidence_records,
        },
        "deduplication": {
            "mode": "exact_keys_only_no_fuzzy_auto_merge",
            "source_id_key": "source_id",
            "entity_key": "stable_location_key",
            "natural_key_guard": ["casefold(name)", "casefold(address)"],
            "duplicate_source_ids": duplicate_sources,
            "duplicate_location_keys": duplicate_locations,
            "duplicate_natural_keys": [list(value) for value in duplicate_natural_keys],
        },
        "identity_proposals": proposals,
        "import_gate": {
            "allowed": import_allowed,
            "reason": "quality_contract_passed" if import_allowed else "explicit_identity_or_current_evidence_missing",
            "does_not_prove_global_coverage": True,
        },
        "claim_boundaries": {
            "global_full_coverage_claim_allowed": False,
            "product_page_proves_authorization": False,
            "product_page_proves_current_inventory": False,
            "public_contact_proves_response_or_sales": False,
            "activity_page_proves_attendance_or_local_impact": False,
        },
        "issue_counts": counts,
        "issues": sorted(issues, key=lambda item: (item["severity"], item["code"], item["path"])),
    }
