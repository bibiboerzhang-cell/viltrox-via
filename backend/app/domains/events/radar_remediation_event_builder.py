"""Deterministic Event remediation preview builders."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.domains.events.radar_quality_audits import audit_event_catalog
from app.domains.events.radar_quality_core import (
    _NONACTIVE_SOURCE_STATUSES,
    _POSITIVE_VILTROX_STATUSES,
    _SOURCE_ID_RE,
    _append_unmapped_issue_tasks,
    _evidence_contract_valid,
    _evidence_covered,
    _freshness,
    _is_https_url,
    _queue_envelope,
    _task,
    _universe_coverage_descriptor,
)


EVENT_HANDLED_CODES = {
    "event.source_type",
    "event.source_id_missing_or_invalid",
    "event.source_url_invalid",
    "event.nonactive_source_enabled",
    "event.source_freshness_incomplete",
    "event.source_evidence_contract_invalid",
    "event.source_review_evidence_incomplete",
    "event.opportunity_type",
    "event.opportunity_id_missing",
    "event.canonical_key_missing",
    "event.source_orphan",
    "event.external_event_key_missing",
    "event.official_url_invalid",
    "event.activity_evidence_contract_invalid",
    "event.activity_observed_at_missing_or_stale",
    "event.viltrox_presence_without_evidence",
    "event.dealer_location_linkage_incomplete",
    "event.global_denominator_unavailable",
    "event.global_source_coverage.manifest_required",
    "event.global_source_coverage.manifest_invalid",
    "event.global_source_coverage.denominator_below_observed",
    "event.viltrox_presence_evidence_incomplete",
}


def _event_source_tasks(
    raw: Any,
    *,
    index: int,
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return [
            _task(
                scope="event",
                entity_type="event_source",
                entity_id=f"invalid_source_row_{index}",
                source_id="",
                field="row",
                issue_code="event.source_type",
                severity="high",
                required_fields=["object"],
                acceptance_rule="Replace the row with one structured source object.",
                source_url=None,
                checked_at=None,
                as_of=now,
                stale_after_days=stale_after_days,
                blocks_import=True,
            )
        ]
    source_id = str(raw.get("id") or "").strip()
    source_url = raw.get("canonical_url")
    source_key_material = "|".join(
        [
            str(raw.get("name") or ""),
            str(source_url or ""),
            str(raw.get("country_code") or ""),
        ]
    )
    entity_id = source_id or (
        f"event_source_candidate_{hashlib.sha256(source_key_material.encode()).hexdigest()[:16]}"
    )
    checked_at = raw.get("source_checked_at")
    if not _SOURCE_ID_RE.fullmatch(source_id):
        tasks.append(
            _task(
                scope="event", entity_type="event_source", entity_id=entity_id,
                source_id=source_id, field="id",
                issue_code="event.source_id_missing_or_invalid", severity="high",
                required_fields=["id"],
                acceptance_rule="Assign one accepted stable source id matching the source identity contract.",
                source_url=source_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
            )
        )
    if not _is_https_url(source_url):
        tasks.append(
            _task(
                scope="event", entity_type="event_source", entity_id=entity_id,
                source_id=source_id, field="canonical_url",
                issue_code="event.source_url_invalid", severity="high",
                required_fields=["canonical_url"],
                acceptance_rule="Record one credential-free official HTTPS source URL.",
                source_url=source_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
            )
        )
    if (
        _freshness(checked_at, as_of=now, stale_after_days=stale_after_days)["status"]
        != "fresh"
        or not _evidence_contract_valid(raw, expected_scope="event_source_listing")
    ):
        tasks.append(
            _task(
                scope="event", entity_type="event_source", entity_id=entity_id,
                source_id=source_id, field="source_checked_at",
                issue_code="event.source_check_missing_or_stale", severity="high",
                required_fields=[
                    "canonical_url", "source_checked_at", "status", "reviewer_id",
                    "evidence_scope", "value_status",
                ],
                acceptance_rule="A human must inspect this exact source URL and record a current check with safe reviewer_id, event_source_listing scope, and observed value status.",
                source_url=source_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
                proof_boundaries=[
                    "A current source check does not prove global source coverage."
                ],
            )
        )
    if (
        str(raw.get("status") or "").casefold() in _NONACTIVE_SOURCE_STATUSES
        and raw.get("enabled") is True
    ):
        tasks.append(
            _task(
                scope="event", entity_type="event_source", entity_id=entity_id,
                source_id=source_id, field="enabled",
                issue_code="event.nonactive_source_enabled", severity="high",
                required_fields=["status", "enabled", "review_note"],
                acceptance_rule="Disable a non-active source or explicitly review and restore it to active status.",
                source_url=source_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
            )
        )
    return tasks


def _event_viltrox_tasks(
    raw: dict[str, Any],
    *,
    entity_id: str,
    source_id: str,
    official_url: Any,
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    presence = str(raw.get("viltrox_presence_status") or "unknown").strip().casefold()
    observation = raw.get("viltrox_evidence")
    if not isinstance(observation, dict):
        observation = {
            "status": (
                "verified"
                if presence in _POSITIVE_VILTROX_STATUSES | {"not_found"}
                else "unknown"
            ),
            "source_url": raw.get("viltrox_evidence_url"),
            "checked_at": raw.get("source_checked_at"),
            "reviewer_id": raw.get("viltrox_reviewer_id"),
            "evidence_scope": raw.get("viltrox_evidence_scope"),
            "value_status": raw.get("viltrox_value_status"),
        }
    observed = bool(
        presence in _POSITIVE_VILTROX_STATUSES | {"not_found"}
        and _evidence_covered(
            observation,
            as_of=now,
            stale_after_days=stale_after_days,
            expected_scope="event_viltrox_presence",
            allowed_value_statuses={"observed", "not_found"},
        )
    )
    if observed:
        return []
    return [
        _task(
            scope="event", entity_type="event_opportunity", entity_id=entity_id,
            source_id=source_id, field="viltrox_presence_evidence",
            issue_code="event.viltrox_presence_evidence_missing_or_stale",
            severity="medium",
            required_fields=[
                "viltrox_presence_status", "viltrox_evidence_url", "source_checked_at",
                "reviewer_id", "evidence_scope", "value_status",
            ],
            acceptance_rule="Record a current source-backed observation as brand_listed, confirmed_exhibitor, or explicit not_found; unknown is not completion.",
            source_url=raw.get("viltrox_evidence_url") or official_url,
            checked_at=raw.get("source_checked_at"), as_of=now,
            stale_after_days=stale_after_days, blocks_import=False,
            proof_boundaries=[
                "A listing does not prove Viltrox attendance, sponsorship, inventory, or sales."
            ],
        )
    ]


def _event_dealer_link_tasks(
    raw: dict[str, Any],
    *,
    entity_id: str,
    source_id: str,
    official_url: Any,
    source_row: dict[str, Any],
    checked_at: Any,
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    source_kind = str(source_row.get("source_kind") or "").strip().casefold()
    lane = str(raw.get("lane") or "").strip().casefold()
    applicable = bool(
        lane == "dealer_event"
        or (lane == "local_activity" and source_kind == "dealer_event")
    )
    if not applicable or str(raw.get("dealer_stable_location_key") or "").strip():
        return []
    return [
        _task(
            scope="event", entity_type="event_opportunity", entity_id=entity_id,
            source_id=source_id, field="dealer_stable_location_key",
            issue_code="event.dealer_location_linkage_missing", severity="medium",
            required_fields=["dealer_stable_location_key", "match_evidence", "reviewer"],
            acceptance_rule="Resolve the activity to one accepted Dealer location key; a name-only hint is not sufficient.",
            source_url=official_url, checked_at=checked_at, as_of=now,
            stale_after_days=stale_after_days, blocks_import=False,
        )
    ]


def _event_opportunity_tasks(
    raw: Any,
    *,
    index: int,
    source_by_id: dict[str, dict[str, Any]],
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return [
            _task(
                scope="event", entity_type="event_opportunity",
                entity_id=f"invalid_opportunity_row_{index}", source_id="", field="row",
                issue_code="event.opportunity_type", severity="high",
                required_fields=["object"],
                acceptance_rule="Replace the row with one structured opportunity object.",
                source_url=None, checked_at=None, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
            )
        ]
    opportunity_id = str(raw.get("id") or "").strip()
    source_id = str(raw.get("source_id") or "").strip()
    official_url = raw.get("official_url")
    entity_material = "|".join(
        [
            source_id,
            str(raw.get("external_event_key") or ""),
            str(raw.get("title") or ""),
            str(official_url or ""),
        ]
    )
    entity_id = opportunity_id or (
        f"event_candidate_{hashlib.sha256(entity_material.encode()).hexdigest()[:16]}"
    )
    source_row = source_by_id.get(source_id) or {}
    checked_at = raw.get("source_checked_at")
    for field, code, value, acceptance in (
        ("id", "event.opportunity_id_missing", opportunity_id, "Assign one stable opportunity id."),
        ("canonical_key", "event.canonical_key_missing", raw.get("canonical_key"), "Assign one canonical entity key."),
        ("external_event_key", "event.external_event_key_missing", raw.get("external_event_key"), "Record the source-native event key."),
    ):
        if not str(value or "").strip():
            tasks.append(
                _task(
                    scope="event", entity_type="event_opportunity", entity_id=entity_id,
                    source_id=source_id, field=field, issue_code=code, severity="high",
                    required_fields=[field], acceptance_rule=acceptance,
                    source_url=official_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
    if source_id not in source_by_id:
        tasks.append(
            _task(
                scope="event", entity_type="event_opportunity", entity_id=entity_id,
                source_id=source_id, field="source_id", issue_code="event.source_orphan",
                severity="high", required_fields=["source_id"],
                acceptance_rule="Link the opportunity to one accepted Event source id.",
                source_url=official_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
            )
        )
    if not _is_https_url(official_url):
        tasks.append(
            _task(
                scope="event", entity_type="event_opportunity", entity_id=entity_id,
                source_id=source_id, field="official_url",
                issue_code="event.official_url_invalid", severity="high",
                required_fields=["official_url"],
                acceptance_rule="Record the credential-free official HTTPS activity URL.",
                source_url=official_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
            )
        )
    activity_covered = bool(
        str(raw.get("verification_status") or "").casefold() == "verified"
        and _is_https_url(official_url)
        and _freshness(checked_at, as_of=now, stale_after_days=stale_after_days)[
            "status"
        ]
        == "fresh"
        and _evidence_contract_valid(raw, expected_scope="event_official_listing")
    )
    if not activity_covered:
        tasks.append(
            _task(
                scope="event", entity_type="event_opportunity", entity_id=entity_id,
                source_id=source_id, field="activity_evidence",
                issue_code="event.activity_evidence_missing_or_stale", severity="high",
                required_fields=[
                    "official_url", "verification_status", "source_checked_at", "reviewer_id",
                    "evidence_scope", "value_status",
                ],
                acceptance_rule="Inspect the official activity page and record verified status, a current check, safe reviewer_id, event_official_listing scope, and observed value status.",
                source_url=official_url, checked_at=checked_at, as_of=now,
                stale_after_days=stale_after_days, blocks_import=True,
                proof_boundaries=[
                    "An activity listing does not prove attendance, sales, ROI, or local impact."
                ],
            )
        )

    tasks.extend(
        _event_viltrox_tasks(
            raw,
            entity_id=entity_id,
            source_id=source_id,
            official_url=official_url,
            now=now,
            stale_after_days=stale_after_days,
        )
    )
    tasks.extend(
        _event_dealer_link_tasks(
            raw,
            entity_id=entity_id,
            source_id=source_id,
            official_url=official_url,
            source_row=source_row,
            checked_at=checked_at,
            now=now,
            stale_after_days=stale_after_days,
        )
    )
    return tasks


def _event_tasks(
    sources: list[Any],
    opportunities: list[Any],
    *,
    source_by_id: dict[str, dict[str, Any]],
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(sources):
        tasks.extend(
            _event_source_tasks(
                raw, index=index, now=now, stale_after_days=stale_after_days
            )
        )
    for index, raw in enumerate(opportunities):
        tasks.extend(
            _event_opportunity_tasks(
                raw,
                index=index,
                source_by_id=source_by_id,
                now=now,
                stale_after_days=stale_after_days,
            )
        )
    return tasks


def _event_denominator_tasks(
    report: dict[str, Any],
    *,
    now: datetime,
    stale_after_days: int,
) -> list[dict[str, Any]]:
    if report["coverage"]["global_source_coverage"]["manifest_status"] == "accepted":
        return []
    return [
        _task(
            scope="event", entity_type="event_source_universe",
            entity_id="global_event_source_universe", source_id="",
            field="known_source_universe_denominator",
            issue_code="event.global_denominator_unavailable", severity="medium",
            required_fields=[
                "manifest_version", "scope", "denominator", "entity_ids_sha256",
                "source_inventory_sha256", "methodology", "as_of", "reviewer_id",
            ],
            acceptance_rule="Approve a bounded reproducible event_sources manifest with hashed entity/source inventories and a denominator not below observed entities.",
            source_url=None, checked_at=None, as_of=now,
            stale_after_days=stale_after_days, blocks_import=False,
            proof_boundaries=[
                "Do not publish a global coverage rate before this denominator is accepted."
            ],
        )
    ]


def build_event_queue(
    payload: dict[str, Any],
    *,
    now: datetime,
    stale_after_days: int,
    known_source_universe_denominator: Any,
) -> dict[str, Any]:
    report = audit_event_catalog(
        payload,
        as_of=now,
        stale_after_days=stale_after_days,
        known_source_universe_denominator=known_source_universe_denominator,
    )
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    opportunities = (
        payload.get("opportunities")
        if isinstance(payload.get("opportunities"), list)
        else []
    )
    source_by_id = {
        str(item.get("id") or "").strip(): item
        for item in sources
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    tasks = _event_tasks(
        sources,
        opportunities,
        source_by_id=source_by_id,
        now=now,
        stale_after_days=stale_after_days,
    )
    tasks.extend(
        _event_denominator_tasks(
            report, now=now, stale_after_days=stale_after_days
        )
    )
    _append_unmapped_issue_tasks(
        tasks,
        report,
        handled_codes=EVENT_HANDLED_CODES,
        scope="event",
        as_of=now,
        stale_after_days=stale_after_days,
    )
    coverage = report["coverage"]
    return _queue_envelope(
        tasks=tasks,
        as_of=now,
        scope="event",
        evidence_gaps={
            "source_current_check": coverage["source_review_evidence"],
            "activity_evidence": coverage["activity_evidence"],
            "viltrox_presence_evidence": coverage["viltrox_presence_evidence"],
            "exact_dealer_location_linkage": coverage[
                "exact_dealer_location_linkage"
            ],
        },
        universe_coverage={
            "event_source_universe": _universe_coverage_descriptor(
                coverage["global_source_coverage"]
            )
        },
    )
