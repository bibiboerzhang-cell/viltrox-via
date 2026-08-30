"""Leaf implementation for the pure Dealer candidate quality audit."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping

from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
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


Issue = Callable[[str, str, str, str], None]


@dataclass(frozen=True)
class DealerIdentity:
    name: str
    address: str
    country: str
    stable_org_key: str
    stable_location_key: str
    valid: bool


@dataclass(frozen=True)
class DealerSourceEvidence:
    source_id: str
    location_url: str
    source_checked_at: Any
    freshness: Mapping[str, Any]
    source_status: str
    contract_valid: bool
    covered: bool


@dataclass
class DealerAuditState:
    rows: list[Any]
    issues: list[dict[str, str]]
    issue: Issue
    source_ids: list[str] = field(default_factory=list)
    source_identity: dict[str, tuple[str, str]] = field(default_factory=dict)
    stable_org_keys: list[str] = field(default_factory=list)
    stable_location_keys: list[str] = field(default_factory=list)
    normalized_natural_keys: list[tuple[str, str]] = field(default_factory=list)
    identity_covered: int = 0
    source_evidence_covered: int = 0
    product_evidence_covered: int = 0
    contact_evidence_covered: int = 0
    social_evidence_covered: int = 0
    activity_evidence_covered: int = 0
    verified_location_entities: set[str] = field(default_factory=set)
    proposals: list[dict[str, str]] = field(default_factory=list)
    evidence_records: list[dict[str, Any]] = field(default_factory=list)


def _invalid_evidence_record(index: int) -> dict[str, Any]:
    return {
        "candidate_index": index, "source_id": None,
        "declared_stable_location_key": None, "exact_stable_location_key": None,
        "source_url": None, "observed_at": None,
        "declared_review_status": None, "review_status": "invalid_candidate_row",
        "reviewer_id": None, "evidence_scope": None, "value_status": None,
        "association_status": "unlinked_no_fuzzy_match",
        "import_eligible": False,
    }

def _required_identity_fields(
    row: Mapping[str, Any],
    *,
    path: str,
    state: DealerAuditState,
) -> tuple[str, str, str]:
    name = str(row.get("name") or "").strip()
    address = str(row.get("address") or "").strip()
    country = str(row.get("country_code") or row.get("country") or "").strip().upper()
    if not name:
        state.issue("error", "dealer.name_missing", f"{path}.name", "dealer name is required")
    if not address:
        state.issue(
            "error", "dealer.address_missing", f"{path}.address",
            "dealer address is required",
        )
    if not _COUNTRY_RE.fullmatch(country):
        state.issue(
            "error", "dealer.country_invalid", f"{path}.country",
            "country must be ISO alpha-2",
        )
    state.normalized_natural_keys.append((name.casefold(), address.casefold()))
    return name, address, country

def _source_identity_conflict(
    source: DealerSourceEvidence,
    *,
    path: str,
    state: DealerAuditState,
) -> None:
    if not source.source_id:
        return
    identity = (source.location_url, str(source.source_checked_at or ""))
    previous = state.source_identity.get(source.source_id)
    if previous is not None and previous != identity:
        state.issue(
            "error",
            "dealer.source_id_conflict",
            f"{path}.source_id",
            "one source_id maps to conflicting source URL/check time",
        )
        return
    state.source_identity[source.source_id] = identity

def _audit_source_evidence(
    row: Mapping[str, Any],
    *,
    path: str,
    now: datetime,
    stale_after_days: int,
    state: DealerAuditState,
) -> DealerSourceEvidence:
    source_id = str(row.get("source_id") or "").strip()
    state.source_ids.append(source_id)
    location_url = str(row.get("location_source_url") or "").strip()
    source_checked_at = row.get("source_checked_at")
    if not _SOURCE_ID_RE.fullmatch(source_id):
        state.issue(
            "error", "dealer.source_id_missing_or_invalid", f"{path}.source_id",
            "stable source_id is required",
        )
    if not _is_https_url(location_url):
        state.issue(
            "error", "dealer.location_source_url_invalid",
            f"{path}.location_source_url",
            "official HTTPS location source is required",
        )
    freshness = _freshness(
        source_checked_at,
        as_of=now,
        stale_after_days=stale_after_days,
    )
    if freshness["status"] != "fresh":
        state.issue(
            "error",
            (
                "dealer.source_freshness_unavailable"
                if freshness["status"] == "unavailable"
                else "dealer.source_not_fresh"
            ),
            f"{path}.source_checked_at",
            "a recent timezone-aware source_checked_at is required for import",
        )
    source_status = str(row.get("source_status") or "").casefold()
    if source_status != "public_listing_verified":
        state.issue(
            "error", "dealer.source_status_not_verified", f"{path}.source_status",
            "source_status must explicitly be public_listing_verified",
        )
    contract_valid = _evidence_contract_valid(
        row,
        expected_scope="dealer_location_listing",
    )
    if not contract_valid:
        state.issue(
            "error",
            "dealer.source_evidence_contract_invalid",
            f"{path}.reviewer_id",
            "Dealer source evidence requires safe reviewer_id, evidence_scope=dealer_location_listing, and value_status=observed",
        )
    covered = bool(
        _SOURCE_ID_RE.fullmatch(source_id)
        and _is_https_url(location_url)
        and freshness["status"] == "fresh"
        and source_status == "public_listing_verified"
        and contract_valid
    )
    if covered:
        state.source_evidence_covered += 1
    source = DealerSourceEvidence(
        source_id=source_id,
        location_url=location_url,
        source_checked_at=source_checked_at,
        freshness=freshness,
        source_status=source_status,
        contract_valid=contract_valid,
        covered=covered,
    )
    _source_identity_conflict(source, path=path, state=state)
    return source


def _identity_proposals(
    row: Mapping[str, Any],
    *,
    name: str,
    location_url: str,
    state: DealerAuditState,
) -> tuple[str, str]:
    try:
        proposed_org, proposed_location = _identity_proposal(row)
    except ValueError:
        proposed_org, proposed_location = "", ""
    state.proposals.append(
        {
            "name": name,
            "proposed_source_id": _source_id_proposal(location_url),
            "proposed_stable_org_key": proposed_org,
            "proposed_stable_location_key": proposed_location,
            "proposal_status": "candidate_only_not_persisted",
        }
    )
    return proposed_org, proposed_location


def _audit_stable_identity(
    row: Mapping[str, Any],
    *,
    path: str,
    name: str,
    address: str,
    country: str,
    location_url: str,
    state: DealerAuditState,
) -> DealerIdentity:
    stable_org_key = str(row.get("stable_org_key") or "").strip()
    stable_location_key = str(row.get("stable_location_key") or "").strip()
    state.stable_org_keys.append(stable_org_key)
    state.stable_location_keys.append(stable_location_key)
    proposed_org, proposed_location = _identity_proposals(
        row,
        name=name,
        location_url=location_url,
        state=state,
    )
    valid = bool(name and address and _COUNTRY_RE.fullmatch(country))
    if not _STABLE_ORG_RE.fullmatch(stable_org_key):
        valid = False
        state.issue(
            "error", "dealer.stable_org_key_missing_or_invalid",
            f"{path}.stable_org_key",
            "accepted stable organization key is required",
        )
    elif proposed_org and stable_org_key != proposed_org:
        valid = False
        state.issue(
            "error", "dealer.stable_org_key_mismatch", f"{path}.stable_org_key",
            "stable organization key does not match the v1 exact identity contract",
        )
    if not _STABLE_LOCATION_RE.fullmatch(stable_location_key):
        valid = False
        state.issue(
            "error", "dealer.stable_location_key_missing_or_invalid",
            f"{path}.stable_location_key",
            "accepted stable location key is required",
        )
    elif proposed_location and stable_location_key != proposed_location:
        valid = False
        state.issue(
            "error", "dealer.stable_location_key_mismatch",
            f"{path}.stable_location_key",
            "stable location key does not match the v1 exact identity contract",
        )
    if valid:
        state.identity_covered += 1
    return DealerIdentity(
        name=name,
        address=address,
        country=country,
        stable_org_key=stable_org_key,
        stable_location_key=stable_location_key,
        valid=valid,
    )


def _audit_contact_evidence(
    row: Mapping[str, Any],
    *,
    source: DealerSourceEvidence,
    now: datetime,
    stale_after_days: int,
    state: DealerAuditState,
) -> None:
    contact_evidence = row.get("contact_evidence")
    if not isinstance(contact_evidence, dict):
        contact_evidence = {}
    for contact_field in _CONTACT_FIELDS:
        evidence = contact_evidence.get(contact_field)
        if row.get(contact_field) not in (None, "") and _evidence_covered(
            evidence,
            as_of=now,
            stale_after_days=stale_after_days,
            fallback_checked_at=source.source_checked_at,
            fallback_url=source.location_url,
            expected_scope="dealer_contact_field",
        ):
            state.contact_evidence_covered += 1


def _audit_social_evidence(
    row: Mapping[str, Any],
    *,
    source: DealerSourceEvidence,
    now: datetime,
    stale_after_days: int,
    state: DealerAuditState,
) -> None:
    social_evidence = row.get("social_evidence")
    if not isinstance(social_evidence, dict):
        social_evidence = {}
    for platform in _SOCIAL_PLATFORMS:
        if _evidence_covered(
            social_evidence.get(platform),
            as_of=now,
            stale_after_days=stale_after_days,
            fallback_checked_at=source.source_checked_at,
            expected_scope="dealer_social_profile",
        ):
            state.social_evidence_covered += 1


def _audit_product_and_activity_evidence(
    row: Mapping[str, Any],
    *,
    path: str,
    source: DealerSourceEvidence,
    now: datetime,
    stale_after_days: int,
    state: DealerAuditState,
) -> bool:
    product_covered = _evidence_covered(
        row.get("viltrox_product_evidence"),
        as_of=now,
        stale_after_days=stale_after_days,
        fallback_checked_at=source.source_checked_at,
        fallback_url=row.get("brand_listing_url"),
        expected_scope="dealer_viltrox_product_page",
    )
    if product_covered:
        state.product_evidence_covered += 1
    else:
        state.issue(
            "error",
            "dealer.viltrox_product_evidence_missing_or_stale",
            f"{path}.viltrox_product_evidence",
            "a current structured page-presence evidence object is required for import",
        )
    if _evidence_covered(
        row.get("activity_evidence"),
        as_of=now,
        stale_after_days=stale_after_days,
        fallback_checked_at=source.source_checked_at,
        expected_scope="dealer_activity_page",
    ):
        state.activity_evidence_covered += 1
    return product_covered


def _evidence_record(
    row: Mapping[str, Any],
    *,
    index: int,
    identity: DealerIdentity,
    source: DealerSourceEvidence,
    product_covered: bool,
) -> dict[str, Any]:
    reviewer_id = str(row.get("reviewer_id") or "").strip()
    return {
        "candidate_index": index,
        "source_id": source.source_id or None,
        "declared_stable_location_key": identity.stable_location_key or None,
        "exact_stable_location_key": (
            identity.stable_location_key if identity.valid else None
        ),
        "source_url": (
            source.location_url if _is_https_url(source.location_url) else None
        ),
        "observed_at": source.freshness["checked_at"],
        "declared_review_status": source.source_status or None,
        "review_status": _review_status(
            declared_reviewed=source.source_status == "public_listing_verified",
            evidence_contract_valid=source.contract_valid,
            freshness_status=str(source.freshness["status"]),
        ),
        "reviewer_id": (
            reviewer_id if _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id) else None
        ),
        "evidence_scope": str(row.get("evidence_scope") or "").strip() or None,
        "value_status": (
            str(row.get("value_status") or "").strip().casefold() or None
        ),
        "association_status": (
            "exact_reviewed_location"
            if identity.valid
            else "unlinked_no_fuzzy_match"
        ),
        "import_eligible": bool(source.covered and identity.valid and product_covered),
    }


def _audit_claim_boundaries(
    row: Mapping[str, Any],
    *,
    path: str,
    state: DealerAuditState,
) -> None:
    authorization_status = str(
        row.get("authorization_status") or "unknown"
    ).strip().casefold()
    if authorization_status not in _NONPOSITIVE_TEXT:
        state.issue(
            "error", "dealer.unsupported_authorization_claim",
            f"{path}.authorization_status",
            "public source evidence cannot establish Viltrox authorization",
        )
    for claim_field in sorted(_UNSUPPORTED_POSITIVE_CLAIMS):
        if claim_field in row and _positive_claim(row.get(claim_field)):
            state.issue(
                "error", "dealer.unsupported_business_claim",
                f"{path}.{claim_field}",
                f"unsupported positive business claim: {claim_field}",
            )


def _audit_candidate_row(
    row: Mapping[str, Any],
    *,
    index: int,
    now: datetime,
    stale_after_days: int,
    state: DealerAuditState,
) -> None:
    path = f"dealers[{index}]"
    name, address, country = _required_identity_fields(
        row,
        path=path,
        state=state,
    )
    source = _audit_source_evidence(
        row,
        path=path,
        now=now,
        stale_after_days=stale_after_days,
        state=state,
    )
    identity = _audit_stable_identity(
        row,
        path=path,
        name=name,
        address=address,
        country=country,
        location_url=source.location_url,
        state=state,
    )
    _audit_contact_evidence(
        row,
        source=source,
        now=now,
        stale_after_days=stale_after_days,
        state=state,
    )
    _audit_social_evidence(
        row,
        source=source,
        now=now,
        stale_after_days=stale_after_days,
        state=state,
    )
    product_covered = _audit_product_and_activity_evidence(
        row,
        path=path,
        source=source,
        now=now,
        stale_after_days=stale_after_days,
        state=state,
    )
    if source.covered and identity.valid and product_covered:
        state.verified_location_entities.add(identity.stable_location_key)
    state.evidence_records.append(
        _evidence_record(
            row,
            index=index,
            identity=identity,
            source=source,
            product_covered=product_covered,
        )
    )
    _audit_claim_boundaries(row, path=path, state=state)


def _audit_rows(
    *,
    state: DealerAuditState,
    now: datetime,
    stale_after_days: int,
) -> None:
    for index, raw in enumerate(state.rows):
        path = f"dealers[{index}]"
        if not isinstance(raw, dict):
            state.issue(
                "error", "dealer.row_type", path,
                "dealer candidate must be an object",
            )
            state.evidence_records.append(_invalid_evidence_record(index))
            continue
        _audit_candidate_row(
            raw,
            index=index,
            now=now,
            stale_after_days=stale_after_days,
            state=state,
        )


def _duplicate_values(values: list[Any]) -> list[Any]:
    counts = Counter(value for value in values if value not in (None, "", ("", "")))
    return sorted(value for value, count in counts.items() if count > 1)


def _audit_duplicates(state: DealerAuditState) -> dict[str, list[Any]]:
    duplicate_sources = _duplicate_values(state.source_ids)
    duplicate_locations = _duplicate_values(state.stable_location_keys)
    duplicate_natural_keys = _duplicate_values(state.normalized_natural_keys)
    for value in duplicate_locations:
        state.issue(
            "error", "dealer.stable_location_key_duplicate", "dealers",
            f"duplicate stable location key: {value}",
        )
    for value in duplicate_natural_keys:
        state.issue(
            "error", "dealer.exact_natural_key_duplicate", "dealers",
            f"duplicate exact normalized name/address: {value!r}",
        )
    return {
        "source_ids": duplicate_sources,
        "locations": duplicate_locations,
        "natural_keys": duplicate_natural_keys,
    }


def _observed_location_inventory(rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "entity_id": str(raw.get("stable_location_key") or "").strip(),
            "source_id": str(raw.get("source_id") or "").strip(),
            "canonical_url": str(raw.get("location_source_url") or "").strip(),
        }
        if isinstance(raw, dict)
        else {"entity_id": None, "source_id": None, "canonical_url": None}
        for raw in rows
    ]


def _dealer_coverage(
    state: DealerAuditState,
    *,
    known_location_universe_denominator: Any,
) -> dict[str, Any]:
    row_count = len(state.rows)
    return {
        "source_evidence": _rate(state.source_evidence_covered, row_count),
        "stable_identity": _rate(state.identity_covered, row_count),
        "contact_fields": _rate(
            state.contact_evidence_covered,
            row_count * len(_CONTACT_FIELDS),
        ),
        "social_profiles": _rate(
            state.social_evidence_covered,
            row_count * len(_SOCIAL_PLATFORMS),
        ),
        "viltrox_product_page_evidence": _rate(
            state.product_evidence_covered,
            row_count,
        ),
        "dealer_activity_evidence": _rate(
            state.activity_evidence_covered,
            row_count,
        ),
        "global_location_coverage": _global_coverage(
            len(state.verified_location_entities),
            known_location_universe_denominator,
            observed_inventory=_observed_location_inventory(state.rows),
            issue=state.issue,
            code="dealer.global_location_coverage",
            path="known_location_universe_denominator",
            expected_scope="dealer_locations",
        ),
    }


def _append_completeness_issues(
    state: DealerAuditState,
    *,
    known_location_universe_denominator: Any,
) -> None:
    row_count = len(state.rows)
    if state.contact_evidence_covered < row_count * len(_CONTACT_FIELDS):
        state.issue(
            "warning", "dealer.contact_evidence_incomplete", "dealers",
            "missing contact values or field-level provenance are not covered",
        )
    if state.social_evidence_covered < row_count * len(_SOCIAL_PLATFORMS):
        state.issue(
            "warning", "dealer.social_evidence_incomplete", "dealers",
            "unknown or unavailable social profiles are not covered",
        )
    if state.activity_evidence_covered < row_count:
        state.issue(
            "warning", "dealer.activity_evidence_incomplete", "dealers",
            "dealer activity evidence is incomplete",
        )
    if known_location_universe_denominator is None:
        state.issue(
            "warning", "dealer.global_denominator_unavailable",
            "known_location_universe_denominator",
            "global Dealer coverage cannot be calculated",
        )


def _import_allowed(state: DealerAuditState, counts: Mapping[str, int]) -> bool:
    row_count = len(state.rows)
    return bool(
        counts["errors"] == 0
        and row_count > 0
        and state.source_evidence_covered == row_count
        and state.identity_covered == row_count
        and state.product_evidence_covered == row_count
    )


def _optional_evidence_complete(
    state: DealerAuditState,
    coverage: Mapping[str, Any],
) -> bool:
    row_count = len(state.rows)
    return bool(
        state.contact_evidence_covered == row_count * len(_CONTACT_FIELDS)
        and state.social_evidence_covered == row_count * len(_SOCIAL_PLATFORMS)
        and state.activity_evidence_covered == row_count
        and coverage["global_location_coverage"]["manifest_status"] == "accepted"
    )


def _quality_status(*, import_allowed: bool, optional_complete: bool) -> str:
    if not import_allowed:
        return "blocked_for_import"
    return "verified_descriptive" if optional_complete else "partial_descriptive"


def _dealer_report(
    state: DealerAuditState,
    *,
    now: datetime,
    stale_after_days: int,
    coverage: dict[str, Any],
    duplicates: Mapping[str, list[Any]],
    counts: dict[str, int],
    import_allowed: bool,
    optional_complete: bool,
) -> dict[str, Any]:
    return {
        "contract": {
            "id": CONTRACT_ID,
            "version": CONTRACT_VERSION,
            "scope": "dealer_candidates",
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
        "counts": {
            "candidate_locations": len(state.rows),
            "explicit_source_ids": len({value for value in state.source_ids if value}),
            "explicit_stable_org_keys": len(
                {value for value in state.stable_org_keys if value}
            ),
            "explicit_stable_location_keys": len(
                {value for value in state.stable_location_keys if value}
            ),
            "verified_location_entities": len(state.verified_location_entities),
        },
        "coverage": coverage,
        "evidence_records": {
            "grain": "one_record_per_input_candidate",
            "association_policy": "stable_location_key_exact_only_no_fuzzy_merge",
            "items": state.evidence_records,
        },
        "deduplication": {
            "mode": "exact_keys_only_no_fuzzy_auto_merge",
            "source_id_key": "source_id",
            "entity_key": "stable_location_key",
            "natural_key_guard": ["casefold(name)", "casefold(address)"],
            "duplicate_source_ids": duplicates["source_ids"],
            "duplicate_location_keys": duplicates["locations"],
            "duplicate_natural_keys": [
                list(value) for value in duplicates["natural_keys"]
            ],
        },
        "identity_proposals": state.proposals,
        "import_gate": {
            "allowed": import_allowed,
            "reason": (
                "quality_contract_passed"
                if import_allowed
                else "explicit_identity_or_current_evidence_missing"
            ),
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
        "issues": sorted(
            state.issues,
            key=lambda item: (item["severity"], item["code"], item["path"]),
        ),
    }


def audit_dealer_candidates_impl(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None,
    stale_after_days: int,
    known_location_universe_denominator: Any,
) -> dict[str, Any]:
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    stale_after_days = int(stale_after_days)
    rows = deepcopy(candidates or [])
    issues: list[dict[str, str]] = []
    state = DealerAuditState(
        rows=rows,
        issues=issues,
        issue=_issue_factory(issues),
    )
    _audit_rows(state=state, now=now, stale_after_days=stale_after_days)
    duplicates = _audit_duplicates(state)
    coverage = _dealer_coverage(
        state,
        known_location_universe_denominator=known_location_universe_denominator,
    )
    _append_completeness_issues(
        state,
        known_location_universe_denominator=known_location_universe_denominator,
    )
    counts = _issue_counts(issues)
    import_allowed = _import_allowed(state, counts)
    optional_complete = _optional_evidence_complete(state, coverage)
    return _dealer_report(
        state,
        now=now,
        stale_after_days=stale_after_days,
        coverage=coverage,
        duplicates=duplicates,
        counts=counts,
        import_allowed=import_allowed,
        optional_complete=optional_complete,
    )


__all__ = ["audit_dealer_candidates_impl"]
