"""Deterministic Dealer remediation preview builders."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domains.events.radar_quality_audits import audit_dealer_candidates
from app.domains.events.radar_quality_core import (
    _CONTACT_FIELDS,
    _COUNTRY_RE,
    _SOCIAL_PLATFORMS,
    _SOURCE_ID_RE,
    _STABLE_LOCATION_RE,
    _STABLE_ORG_RE,
    _append_unmapped_issue_tasks,
    _evidence_contract_valid,
    _evidence_covered,
    _freshness,
    _identity_proposal,
    _is_https_url,
    _queue_envelope,
    _source_id_proposal,
    _task,
    _universe_coverage_descriptor,
)


DEALER_HANDLED_CODES = {
    "dealer.row_type",
    "dealer.name_missing",
    "dealer.address_missing",
    "dealer.country_invalid",
    "dealer.source_id_missing_or_invalid",
    "dealer.location_source_url_invalid",
    "dealer.source_freshness_unavailable",
    "dealer.source_not_fresh",
    "dealer.source_status_not_verified",
    "dealer.source_evidence_contract_invalid",
    "dealer.stable_org_key_missing_or_invalid",
    "dealer.stable_org_key_mismatch",
    "dealer.stable_location_key_missing_or_invalid",
    "dealer.stable_location_key_mismatch",
    "dealer.viltrox_product_evidence_missing_or_stale",
    "dealer.contact_evidence_incomplete",
    "dealer.social_evidence_incomplete",
    "dealer.activity_evidence_incomplete",
    "dealer.global_denominator_unavailable",
    "dealer.global_location_coverage.manifest_required",
    "dealer.global_location_coverage.manifest_invalid",
    "dealer.global_location_coverage.denominator_below_observed",
}


@dataclass(frozen=True)
class DealerRow:
    raw: dict[str, Any]
    location_url: Any
    checked_at: Any
    supplied_source_id: str
    proposed_org: str
    proposed_location: str
    proposed_source: str
    entity_id: str
    source_id: str
    now: datetime
    stale_after_days: int


def _dealer_row(raw: dict[str, Any], *, now: datetime, stale_after_days: int) -> DealerRow:
    location_url = raw.get("location_source_url")
    checked_at = raw.get("source_checked_at")
    supplied_source_id = str(raw.get("source_id") or "").strip()
    try:
        proposed_org, proposed_location = _identity_proposal(raw)
    except ValueError:
        proposed_org, proposed_location = "", ""
    proposed_source = _source_id_proposal(location_url)
    material = "|".join(
        [
            str(raw.get("name") or ""),
            str(raw.get("address") or ""),
            str(location_url or ""),
        ]
    )
    entity_id = (
        str(raw.get("stable_location_key") or "").strip()
        or proposed_location
        or f"dealer_candidate_{hashlib.sha256(material.encode()).hexdigest()[:16]}"
    )
    return DealerRow(
        raw=raw,
        location_url=location_url,
        checked_at=checked_at,
        supplied_source_id=supplied_source_id,
        proposed_org=proposed_org,
        proposed_location=proposed_location,
        proposed_source=proposed_source,
        entity_id=entity_id,
        source_id=supplied_source_id or proposed_source,
        now=now,
        stale_after_days=stale_after_days,
    )


def _dealer_identity_tasks(row: DealerRow) -> list[dict[str, Any]]:
    raw = row.raw
    tasks: list[dict[str, Any]] = []
    for field, present, code, acceptance in (
        ("name", bool(str(raw.get("name") or "").strip()), "dealer.name_missing", "Record the public location name."),
        ("address", bool(str(raw.get("address") or "").strip()), "dealer.address_missing", "Record the public street address."),
        ("country", bool(_COUNTRY_RE.fullmatch(str(raw.get("country_code") or raw.get("country") or "").strip().upper())), "dealer.country_invalid", "Record an ISO alpha-2 country code."),
    ):
        if not present:
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                    source_id=row.source_id, field=field, issue_code=code, severity="high",
                    required_fields=[field, "source_url", "checked_at"],
                    acceptance_rule=acceptance, source_url=row.location_url,
                    checked_at=row.checked_at, as_of=row.now,
                    stale_after_days=row.stale_after_days, blocks_import=True,
                )
            )
    if not _SOURCE_ID_RE.fullmatch(row.supplied_source_id):
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                source_id=row.source_id, field="source_id",
                issue_code="dealer.source_id_missing_or_invalid", severity="high",
                required_fields=["source_id", "location_source_url", "reviewer"],
                acceptance_rule=f"Accept a stable source id; deterministic candidate is {row.proposed_source or 'unavailable until URL is valid'}.",
                source_url=row.location_url, checked_at=row.checked_at, as_of=row.now,
                stale_after_days=row.stale_after_days, blocks_import=True,
            )
        )
    if not _is_https_url(row.location_url):
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                source_id=row.source_id, field="location_source_url",
                issue_code="dealer.location_source_url_invalid", severity="high",
                required_fields=["location_source_url"],
                acceptance_rule="Record one official credential-free HTTPS location source URL.",
                source_url=row.location_url, checked_at=row.checked_at, as_of=row.now,
                stale_after_days=row.stale_after_days, blocks_import=True,
            )
        )
    if (
        _freshness(
            row.checked_at, as_of=row.now, stale_after_days=row.stale_after_days
        )["status"]
        != "fresh"
        or not _evidence_contract_valid(raw, expected_scope="dealer_location_listing")
    ):
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                source_id=row.source_id, field="source_checked_at",
                issue_code="dealer.source_check_missing_or_stale", severity="high",
                required_fields=[
                    "location_source_url", "source_checked_at", "source_status",
                    "reviewer_id", "evidence_scope", "value_status",
                ],
                acceptance_rule="Inspect the exact official location source and record a current check with safe reviewer_id, dealer_location_listing scope, and observed value status.",
                source_url=row.location_url, checked_at=row.checked_at, as_of=row.now,
                stale_after_days=row.stale_after_days, blocks_import=True,
            )
        )
    if str(raw.get("source_status") or "").casefold() != "public_listing_verified":
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                source_id=row.source_id, field="source_status",
                issue_code="dealer.source_status_not_verified", severity="high",
                required_fields=[
                    "source_status", "location_source_url", "source_checked_at", "reviewer"
                ],
                acceptance_rule="Set public_listing_verified only after a current human review of the location page.",
                source_url=row.location_url, checked_at=row.checked_at, as_of=row.now,
                stale_after_days=row.stale_after_days, blocks_import=True,
                proof_boundaries=[
                    "A public location listing does not prove Viltrox authorization."
                ],
            )
        )
    supplied_org = str(raw.get("stable_org_key") or "").strip()
    if not _STABLE_ORG_RE.fullmatch(supplied_org) or (
        row.proposed_org and supplied_org != row.proposed_org
    ):
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_organization",
                entity_id=row.proposed_org or row.entity_id, source_id=row.source_id,
                field="stable_org_key",
                issue_code="dealer.stable_org_key_missing_or_invalid", severity="high",
                required_fields=[
                    "stable_org_key", "organization_name", "official_domain", "reviewer"
                ],
                acceptance_rule=f"Review and accept the exact organization key candidate {row.proposed_org or 'after source identity is complete'}.",
                source_url=row.location_url, checked_at=row.checked_at, as_of=row.now,
                stale_after_days=row.stale_after_days, blocks_import=True,
            )
        )
    supplied_location = str(raw.get("stable_location_key") or "").strip()
    if not _STABLE_LOCATION_RE.fullmatch(supplied_location) or (
        row.proposed_location and supplied_location != row.proposed_location
    ):
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                source_id=row.source_id, field="stable_location_key",
                issue_code="dealer.stable_location_key_missing_or_invalid", severity="high",
                required_fields=[
                    "stable_location_key", "address", "postal_code", "reviewer"
                ],
                acceptance_rule=f"Review and accept the exact location key candidate {row.proposed_location or 'after location identity is complete'}.",
                source_url=row.location_url, checked_at=row.checked_at, as_of=row.now,
                stale_after_days=row.stale_after_days, blocks_import=True,
            )
        )
    return tasks


def _dealer_product_tasks(row: DealerRow) -> list[dict[str, Any]]:
    evidence = row.raw.get("viltrox_product_evidence")
    if _evidence_covered(
        evidence,
        as_of=row.now,
        stale_after_days=row.stale_after_days,
        fallback_checked_at=row.checked_at,
        fallback_url=row.raw.get("brand_listing_url"),
        expected_scope="dealer_viltrox_product_page",
    ):
        return []
    return [
        _task(
            scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
            source_id=row.source_id, field="viltrox_product_evidence",
            issue_code="dealer.viltrox_product_evidence_missing_or_stale", severity="high",
            required_fields=[
                "status", "source_url", "checked_at", "reviewer_id",
                "evidence_scope", "value_status",
            ],
            acceptance_rule="Record a current structured page-presence observation from the retailer's public Viltrox/product page.",
            source_url=(
                (evidence or {}).get("source_url")
                if isinstance(evidence, dict)
                else row.raw.get("brand_listing_url")
            ),
            checked_at=(
                (evidence or {}).get("checked_at")
                if isinstance(evidence, dict)
                else row.checked_at
            ),
            as_of=row.now, stale_after_days=row.stale_after_days, blocks_import=True,
            proof_boundaries=[
                "Product-page presence does not prove authorization, current stock, inventory quantity, or sales."
            ],
        )
    ]


def _dealer_contact_tasks(row: DealerRow) -> list[dict[str, Any]]:
    raw = row.raw
    evidence_by_field = (
        raw.get("contact_evidence")
        if isinstance(raw.get("contact_evidence"), dict)
        else {}
    )
    tasks: list[dict[str, Any]] = []
    for field in _CONTACT_FIELDS:
        evidence = evidence_by_field.get(field)
        if raw.get(field) in (None, "") or not _evidence_covered(
            evidence,
            as_of=row.now,
            stale_after_days=row.stale_after_days,
            fallback_checked_at=row.checked_at,
            fallback_url=row.location_url,
            expected_scope="dealer_contact_field",
        ):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                    source_id=row.source_id, field=f"contact_evidence.{field}",
                    issue_code="dealer.contact_evidence_missing_or_stale", severity="medium",
                    required_fields=[
                        field, "status", "source_url", "checked_at", "reviewer_id",
                        "evidence_scope", "value_status",
                    ],
                    acceptance_rule="Record the public value with current field-level provenance, or an explicit unavailable observation; unknown is not coverage.",
                    source_url=(
                        (evidence or {}).get("source_url")
                        if isinstance(evidence, dict)
                        else row.location_url
                    ),
                    checked_at=(
                        (evidence or {}).get("checked_at")
                        if isinstance(evidence, dict)
                        else row.checked_at
                    ),
                    as_of=row.now, stale_after_days=row.stale_after_days,
                    blocks_import=False,
                )
            )
    return tasks


def _dealer_social_tasks(row: DealerRow) -> list[dict[str, Any]]:
    raw = row.raw
    evidence_by_platform = (
        raw.get("social_evidence")
        if isinstance(raw.get("social_evidence"), dict)
        else {}
    )
    tasks: list[dict[str, Any]] = []
    for platform in _SOCIAL_PLATFORMS:
        evidence = evidence_by_platform.get(platform)
        if not _evidence_covered(
            evidence,
            as_of=row.now,
            stale_after_days=row.stale_after_days,
            expected_scope="dealer_social_profile",
        ):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
                    source_id=row.source_id, field=f"social_evidence.{platform}",
                    issue_code="dealer.social_evidence_missing_or_stale", severity="medium",
                    required_fields=[
                        "platform", "status", "source_url", "checked_at", "reviewer_id",
                        "evidence_scope", "value_status",
                    ],
                    acceptance_rule="Record the exact public profile with current provenance, or an explicit unavailable observation; unknown is not coverage.",
                    source_url=(
                        (evidence or {}).get("source_url")
                        if isinstance(evidence, dict)
                        else None
                    ),
                    checked_at=(
                        (evidence or {}).get("checked_at")
                        if isinstance(evidence, dict)
                        else None
                    ),
                    as_of=row.now, stale_after_days=row.stale_after_days,
                    blocks_import=False,
                )
            )
    return tasks


def _dealer_activity_tasks(row: DealerRow) -> list[dict[str, Any]]:
    evidence = row.raw.get("activity_evidence")
    if _evidence_covered(
        evidence,
        as_of=row.now,
        stale_after_days=row.stale_after_days,
        expected_scope="dealer_activity_page",
    ):
        return []
    return [
        _task(
            scope="dealer", entity_type="dealer_location", entity_id=row.entity_id,
            source_id=row.source_id, field="activity_evidence",
            issue_code="dealer.activity_evidence_missing_or_stale", severity="medium",
            required_fields=[
                "status", "source_url", "checked_at", "reviewer_id",
                "evidence_scope", "value_status",
            ],
            acceptance_rule="Record one current dealer activity observation or explicit unavailable result with provenance.",
            source_url=(
                (evidence or {}).get("source_url")
                if isinstance(evidence, dict)
                else None
            ),
            checked_at=(
                (evidence or {}).get("checked_at")
                if isinstance(evidence, dict)
                else None
            ),
            as_of=row.now, stale_after_days=row.stale_after_days,
            blocks_import=False,
            proof_boundaries=[
                "An activity page does not prove attendance, local impact, ROI, or sales."
            ],
        )
    ]


def _invalid_dealer_row_task(
    index: int, *, now: datetime, stale_after_days: int
) -> dict[str, Any]:
    return _task(
        scope="dealer", entity_type="dealer_location",
        entity_id=f"invalid_dealer_row_{index}", source_id="", field="row",
        issue_code="dealer.row_type", severity="high", required_fields=["object"],
        acceptance_rule="Replace the row with one structured Dealer location object.",
        source_url=None, checked_at=None, as_of=now,
        stale_after_days=stale_after_days, blocks_import=True,
    )


def _dealer_row_tasks(
    raw: Any,
    *,
    index: int,
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return [
            _invalid_dealer_row_task(
                index, now=now, stale_after_days=stale_after_days
            )
        ]
    row = _dealer_row(raw, now=now, stale_after_days=stale_after_days)
    return [
        *_dealer_identity_tasks(row),
        *_dealer_product_tasks(row),
        *_dealer_contact_tasks(row),
        *_dealer_social_tasks(row),
        *_dealer_activity_tasks(row),
    ]


def _dealer_tasks(
    rows: list[Any], *, now: datetime, stale_after_days: int
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        tasks.extend(
            _dealer_row_tasks(
                raw, index=index, now=now, stale_after_days=stale_after_days
            )
        )
    return tasks


def _dealer_denominator_tasks(
    report: dict[str, Any],
    *,
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    if report["coverage"]["global_location_coverage"]["manifest_status"] == "accepted":
        return []
    return [
        _task(
            scope="dealer", entity_type="dealer_location_universe",
            entity_id="global_dealer_location_universe", source_id="",
            field="known_location_universe_denominator",
            issue_code="dealer.global_denominator_unavailable", severity="medium",
            required_fields=[
                "manifest_version", "scope", "denominator", "entity_ids_sha256",
                "source_inventory_sha256", "methodology", "as_of", "reviewer_id",
            ],
            acceptance_rule="Approve a bounded reproducible dealer_locations manifest with hashed entity/source inventories and a denominator not below observed entities.",
            source_url=None, checked_at=None, as_of=now,
            stale_after_days=stale_after_days, blocks_import=False,
            proof_boundaries=[
                "Do not publish a global Dealer coverage rate before this denominator is accepted."
            ],
        )
    ]


def build_dealer_queue(
    rows: list[Any],
    *,
    now: datetime,
    stale_after_days: int,
    known_location_universe_denominator: Any,
) -> dict[str, Any]:
    report = audit_dealer_candidates(
        rows,
        as_of=now,
        stale_after_days=stale_after_days,
        known_location_universe_denominator=known_location_universe_denominator,
    )
    tasks = _dealer_tasks(rows, now=now, stale_after_days=stale_after_days)
    tasks.extend(
        _dealer_denominator_tasks(
            report, now=now, stale_after_days=stale_after_days
        )
    )
    _append_unmapped_issue_tasks(
        tasks,
        report,
        handled_codes=DEALER_HANDLED_CODES,
        scope="dealer",
        as_of=now,
        stale_after_days=stale_after_days,
    )
    coverage = report["coverage"]
    return _queue_envelope(
        tasks=tasks,
        as_of=now,
        scope="dealer",
        evidence_gaps={
            "source_current_check": coverage["source_evidence"],
            "stable_identity": coverage["stable_identity"],
            "contact_fields": coverage["contact_fields"],
            "social_profiles": coverage["social_profiles"],
            "viltrox_product_page_evidence": coverage[
                "viltrox_product_page_evidence"
            ],
            "dealer_activity_evidence": coverage["dealer_activity_evidence"],
        },
        universe_coverage={
            "dealer_location_universe": _universe_coverage_descriptor(
                coverage["global_location_coverage"]
            )
        },
    )
