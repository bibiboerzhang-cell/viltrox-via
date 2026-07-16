"""Deterministic preview-only remediation queues for Event and Dealer evidence."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime
from typing import Any

from app.domains.events.radar_quality_core import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    DEFAULT_STALE_AFTER_DAYS,
    REMEDIATION_QUEUE_ID,
    REMEDIATION_QUEUE_VERSION,
    _CONTACT_FIELDS,
    _COUNTRY_RE,
    _CURRENT_EVIDENCE_STATUSES,
    _NONACTIVE_SOURCE_STATUSES,
    _NONPOSITIVE_TEXT,
    _POSITIVE_VILTROX_STATUSES,
    _SAFE_REVIEWER_ID_RE,
    _SOCIAL_PLATFORMS,
    _SOURCE_ID_RE,
    _STABLE_LOCATION_RE,
    _STABLE_ORG_RE,
    _UNKNOWN_EVIDENCE_STATUSES,
    _UNSUPPORTED_POSITIVE_CLAIMS,
    _append_unmapped_issue_tasks,
    _as_utc,
    _evidence_contract_valid,
    _evidence_covered,
    _freshness,
    _global_coverage,
    _identity_proposal,
    _is_https_url,
    _issue_counts,
    _issue_factory,
    _parse_timestamp,
    _positive_claim,
    _queue_envelope,
    _rate,
    _source_id_proposal,
    _task,
    _universe_coverage_descriptor,
)
from app.domains.events.radar_quality_audits import (
    audit_dealer_candidates,
    audit_event_catalog,
)


def build_event_remediation_queue(
    catalog: dict[str, Any],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_source_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Expand Event quality gaps into deterministic, preview-only work items."""
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    stale_after_days = int(stale_after_days)
    payload = deepcopy(catalog or {})
    report = audit_event_catalog(
        payload,
        as_of=now,
        stale_after_days=stale_after_days,
        known_source_universe_denominator=known_source_universe_denominator,
    )
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    opportunities = (
        payload.get("opportunities") if isinstance(payload.get("opportunities"), list) else []
    )
    source_by_id = {
        str(item.get("id") or "").strip(): item
        for item in sources
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    tasks: list[dict[str, Any]] = []

    for index, raw in enumerate(sources):
        if not isinstance(raw, dict):
            entity_id = f"invalid_source_row_{index}"
            tasks.append(
                _task(
                    scope="event",
                    entity_type="event_source",
                    entity_id=entity_id,
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
            )
            continue
        source_id = str(raw.get("id") or "").strip()
        source_url = raw.get("canonical_url")
        source_key_material = "|".join(
            [str(raw.get("name") or ""), str(source_url or ""), str(raw.get("country_code") or "")]
        )
        entity_id = source_id or f"event_source_candidate_{hashlib.sha256(source_key_material.encode()).hexdigest()[:16]}"
        checked_at = raw.get("source_checked_at")
        if not _SOURCE_ID_RE.fullmatch(source_id):
            tasks.append(
                _task(
                    scope="event", entity_type="event_source", entity_id=entity_id,
                    source_id=source_id, field="id", issue_code="event.source_id_missing_or_invalid",
                    severity="high", required_fields=["id"],
                    acceptance_rule="Assign one accepted stable source id matching the source identity contract.",
                    source_url=source_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        if not _is_https_url(source_url):
            tasks.append(
                _task(
                    scope="event", entity_type="event_source", entity_id=entity_id,
                    source_id=source_id, field="canonical_url", issue_code="event.source_url_invalid",
                    severity="high", required_fields=["canonical_url"],
                    acceptance_rule="Record one credential-free official HTTPS source URL.",
                    source_url=source_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        if (
            _freshness(checked_at, as_of=now, stale_after_days=stale_after_days)["status"] != "fresh"
            or not _evidence_contract_valid(raw, expected_scope="event_source_listing")
        ):
            tasks.append(
                _task(
                    scope="event", entity_type="event_source", entity_id=entity_id,
                    source_id=source_id, field="source_checked_at",
                    issue_code="event.source_check_missing_or_stale", severity="high",
                    required_fields=["canonical_url", "source_checked_at", "status", "reviewer_id", "evidence_scope", "value_status"],
                    acceptance_rule="A human must inspect this exact source URL and record a current check with safe reviewer_id, event_source_listing scope, and observed value status.",
                    source_url=source_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                    proof_boundaries=["A current source check does not prove global source coverage."],
                )
            )
        if str(raw.get("status") or "").casefold() in _NONACTIVE_SOURCE_STATUSES and raw.get("enabled") is True:
            tasks.append(
                _task(
                    scope="event", entity_type="event_source", entity_id=entity_id,
                    source_id=source_id, field="enabled", issue_code="event.nonactive_source_enabled",
                    severity="high", required_fields=["status", "enabled", "review_note"],
                    acceptance_rule="Disable a non-active source or explicitly review and restore it to active status.",
                    source_url=source_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )

    for index, raw in enumerate(opportunities):
        if not isinstance(raw, dict):
            tasks.append(
                _task(
                    scope="event", entity_type="event_opportunity",
                    entity_id=f"invalid_opportunity_row_{index}", source_id="", field="row",
                    issue_code="event.opportunity_type", severity="high",
                    required_fields=["object"], acceptance_rule="Replace the row with one structured opportunity object.",
                    source_url=None, checked_at=None, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
            continue
        opportunity_id = str(raw.get("id") or "").strip()
        source_id = str(raw.get("source_id") or "").strip()
        official_url = raw.get("official_url")
        entity_material = "|".join(
            [source_id, str(raw.get("external_event_key") or ""), str(raw.get("title") or ""), str(official_url or "")]
        )
        entity_id = opportunity_id or f"event_candidate_{hashlib.sha256(entity_material.encode()).hexdigest()[:16]}"
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
                    source_id=source_id, field="official_url", issue_code="event.official_url_invalid",
                    severity="high", required_fields=["official_url"],
                    acceptance_rule="Record the credential-free official HTTPS activity URL.",
                    source_url=official_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        activity_covered = bool(
            str(raw.get("verification_status") or "").casefold() == "verified"
            and _is_https_url(official_url)
            and _freshness(checked_at, as_of=now, stale_after_days=stale_after_days)["status"] == "fresh"
            and _evidence_contract_valid(raw, expected_scope="event_official_listing")
        )
        if not activity_covered:
            tasks.append(
                _task(
                    scope="event", entity_type="event_opportunity", entity_id=entity_id,
                    source_id=source_id, field="activity_evidence",
                    issue_code="event.activity_evidence_missing_or_stale", severity="high",
                    required_fields=["official_url", "verification_status", "source_checked_at", "reviewer_id", "evidence_scope", "value_status"],
                    acceptance_rule="Inspect the official activity page and record verified status, a current check, safe reviewer_id, event_official_listing scope, and observed value status.",
                    source_url=official_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                    proof_boundaries=["An activity listing does not prove attendance, sales, ROI, or local impact."],
                )
            )

        presence = str(raw.get("viltrox_presence_status") or "unknown").strip().casefold()
        viltrox_observation = raw.get("viltrox_evidence")
        if not isinstance(viltrox_observation, dict):
            viltrox_observation = {
                "status": "verified" if presence in _POSITIVE_VILTROX_STATUSES | {"not_found"} else "unknown",
                "source_url": raw.get("viltrox_evidence_url"),
                "checked_at": raw.get("source_checked_at"),
                "reviewer_id": raw.get("viltrox_reviewer_id"),
                "evidence_scope": raw.get("viltrox_evidence_scope"),
                "value_status": raw.get("viltrox_value_status"),
            }
        viltrox_observed = bool(
            presence in _POSITIVE_VILTROX_STATUSES | {"not_found"}
            and _evidence_covered(
                viltrox_observation,
                as_of=now,
                stale_after_days=stale_after_days,
                expected_scope="event_viltrox_presence",
                allowed_value_statuses={"observed", "not_found"},
            )
        )
        if not viltrox_observed:
            tasks.append(
                _task(
                    scope="event", entity_type="event_opportunity", entity_id=entity_id,
                    source_id=source_id, field="viltrox_presence_evidence",
                    issue_code="event.viltrox_presence_evidence_missing_or_stale", severity="medium",
                    required_fields=["viltrox_presence_status", "viltrox_evidence_url", "source_checked_at", "reviewer_id", "evidence_scope", "value_status"],
                    acceptance_rule="Record a current source-backed observation as brand_listed, confirmed_exhibitor, or explicit not_found; unknown is not completion.",
                    source_url=raw.get("viltrox_evidence_url") or official_url,
                    checked_at=raw.get("source_checked_at"), as_of=now,
                    stale_after_days=stale_after_days, blocks_import=False,
                    proof_boundaries=["A listing does not prove Viltrox attendance, sponsorship, inventory, or sales."],
                )
            )

        source_kind = str(source_row.get("source_kind") or "").strip().casefold()
        lane = str(raw.get("lane") or "").strip().casefold()
        dealer_link_applicable = bool(
            lane == "dealer_event"
            or (lane == "local_activity" and source_kind == "dealer_event")
        )
        if dealer_link_applicable and not str(raw.get("dealer_stable_location_key") or "").strip():
            tasks.append(
                _task(
                    scope="event", entity_type="event_opportunity", entity_id=entity_id,
                    source_id=source_id, field="dealer_stable_location_key",
                    issue_code="event.dealer_location_linkage_missing", severity="medium",
                    required_fields=["dealer_stable_location_key", "match_evidence", "reviewer"],
                    acceptance_rule="Resolve the activity to one accepted Dealer location key; a name-only hint is not sufficient.",
                    source_url=official_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=False,
                )
            )

    event_universe_observed = int(
        report["coverage"]["global_source_coverage"].get("covered") or 0
    )
    if report["coverage"]["global_source_coverage"]["manifest_status"] != "accepted":
        tasks.append(
            _task(
                scope="event", entity_type="event_source_universe", entity_id="global_event_source_universe",
                source_id="", field="known_source_universe_denominator",
                issue_code="event.global_denominator_unavailable", severity="medium",
                required_fields=["manifest_version", "scope", "denominator", "entity_ids_sha256", "source_inventory_sha256", "methodology", "as_of", "reviewer_id"],
                acceptance_rule="Approve a bounded reproducible event_sources manifest with hashed entity/source inventories and a denominator not below observed entities.",
                source_url=None, checked_at=None, as_of=now,
                stale_after_days=stale_after_days, blocks_import=False,
                proof_boundaries=["Do not publish a global coverage rate before this denominator is accepted."],
            )
        )

    _append_unmapped_issue_tasks(
        tasks,
        report,
        handled_codes={
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
        },
        scope="event",
        as_of=now,
        stale_after_days=stale_after_days,
    )

    coverage = report["coverage"]
    gaps = {
        "source_current_check": coverage["source_review_evidence"],
        "activity_evidence": coverage["activity_evidence"],
        "viltrox_presence_evidence": coverage["viltrox_presence_evidence"],
        "exact_dealer_location_linkage": coverage["exact_dealer_location_linkage"],
    }
    return _queue_envelope(
        tasks=tasks,
        as_of=now,
        scope="event",
        evidence_gaps=gaps,
        universe_coverage={
            "event_source_universe": _universe_coverage_descriptor(
                coverage["global_source_coverage"]
            )
        },
    )
def build_dealer_remediation_queue(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Expand Dealer candidate gaps into deterministic, preview-only tasks."""
    now = _as_utc(as_of)
    if isinstance(stale_after_days, bool) or int(stale_after_days) <= 0:
        raise ValueError("stale_after_days must be a positive integer")
    stale_after_days = int(stale_after_days)
    rows = deepcopy(candidates or [])
    report = audit_dealer_candidates(
        rows,
        as_of=now,
        stale_after_days=stale_after_days,
        known_location_universe_denominator=known_location_universe_denominator,
    )
    tasks: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=f"invalid_dealer_row_{index}",
                    source_id="", field="row", issue_code="dealer.row_type", severity="high",
                    required_fields=["object"], acceptance_rule="Replace the row with one structured Dealer location object.",
                    source_url=None, checked_at=None, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
            continue
        location_url = raw.get("location_source_url")
        checked_at = raw.get("source_checked_at")
        supplied_source_id = str(raw.get("source_id") or "").strip()
        try:
            proposed_org, proposed_location = _identity_proposal(raw)
        except ValueError:
            proposed_org, proposed_location = "", ""
        proposed_source = _source_id_proposal(location_url)
        material = "|".join(
            [str(raw.get("name") or ""), str(raw.get("address") or ""), str(location_url or "")]
        )
        entity_id = (
            str(raw.get("stable_location_key") or "").strip()
            or proposed_location
            or f"dealer_candidate_{hashlib.sha256(material.encode()).hexdigest()[:16]}"
        )
        source_id = supplied_source_id or proposed_source
        for field, present, code, acceptance in (
            ("name", bool(str(raw.get("name") or "").strip()), "dealer.name_missing", "Record the public location name."),
            ("address", bool(str(raw.get("address") or "").strip()), "dealer.address_missing", "Record the public street address."),
            ("country", bool(_COUNTRY_RE.fullmatch(str(raw.get("country_code") or raw.get("country") or "").strip().upper())), "dealer.country_invalid", "Record an ISO alpha-2 country code."),
        ):
            if not present:
                tasks.append(
                    _task(
                        scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                        source_id=source_id, field=field, issue_code=code, severity="high",
                        required_fields=[field, "source_url", "checked_at"], acceptance_rule=acceptance,
                        source_url=location_url, checked_at=checked_at, as_of=now,
                        stale_after_days=stale_after_days, blocks_import=True,
                    )
                )
        if not _SOURCE_ID_RE.fullmatch(supplied_source_id):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="source_id", issue_code="dealer.source_id_missing_or_invalid",
                    severity="high", required_fields=["source_id", "location_source_url", "reviewer"],
                    acceptance_rule=f"Accept a stable source id; deterministic candidate is {proposed_source or 'unavailable until URL is valid'}.",
                    source_url=location_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        if not _is_https_url(location_url):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="location_source_url", issue_code="dealer.location_source_url_invalid",
                    severity="high", required_fields=["location_source_url"],
                    acceptance_rule="Record one official credential-free HTTPS location source URL.",
                    source_url=location_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        if (
            _freshness(checked_at, as_of=now, stale_after_days=stale_after_days)["status"] != "fresh"
            or not _evidence_contract_valid(raw, expected_scope="dealer_location_listing")
        ):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="source_checked_at",
                    issue_code="dealer.source_check_missing_or_stale", severity="high",
                    required_fields=["location_source_url", "source_checked_at", "source_status", "reviewer_id", "evidence_scope", "value_status"],
                    acceptance_rule="Inspect the exact official location source and record a current check with safe reviewer_id, dealer_location_listing scope, and observed value status.",
                    source_url=location_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        if str(raw.get("source_status") or "").casefold() != "public_listing_verified":
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="source_status", issue_code="dealer.source_status_not_verified",
                    severity="high", required_fields=["source_status", "location_source_url", "source_checked_at", "reviewer"],
                    acceptance_rule="Set public_listing_verified only after a current human review of the location page.",
                    source_url=location_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                    proof_boundaries=["A public location listing does not prove Viltrox authorization."],
                )
            )
        supplied_org = str(raw.get("stable_org_key") or "").strip()
        if not _STABLE_ORG_RE.fullmatch(supplied_org) or (proposed_org and supplied_org != proposed_org):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_organization", entity_id=proposed_org or entity_id,
                    source_id=source_id, field="stable_org_key", issue_code="dealer.stable_org_key_missing_or_invalid",
                    severity="high", required_fields=["stable_org_key", "organization_name", "official_domain", "reviewer"],
                    acceptance_rule=f"Review and accept the exact organization key candidate {proposed_org or 'after source identity is complete'}.",
                    source_url=location_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        supplied_location = str(raw.get("stable_location_key") or "").strip()
        if not _STABLE_LOCATION_RE.fullmatch(supplied_location) or (
            proposed_location and supplied_location != proposed_location
        ):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="stable_location_key",
                    issue_code="dealer.stable_location_key_missing_or_invalid", severity="high",
                    required_fields=["stable_location_key", "address", "postal_code", "reviewer"],
                    acceptance_rule=f"Review and accept the exact location key candidate {proposed_location or 'after location identity is complete'}.",
                    source_url=location_url, checked_at=checked_at, as_of=now,
                    stale_after_days=stale_after_days, blocks_import=True,
                )
            )
        product_evidence = raw.get("viltrox_product_evidence")
        if not _evidence_covered(
            product_evidence,
            as_of=now,
            stale_after_days=stale_after_days,
            fallback_checked_at=checked_at,
            fallback_url=raw.get("brand_listing_url"),
            expected_scope="dealer_viltrox_product_page",
        ):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="viltrox_product_evidence",
                    issue_code="dealer.viltrox_product_evidence_missing_or_stale", severity="high",
                    required_fields=["status", "source_url", "checked_at", "reviewer_id", "evidence_scope", "value_status"],
                    acceptance_rule="Record a current structured page-presence observation from the retailer's public Viltrox/product page.",
                    source_url=(product_evidence or {}).get("source_url") if isinstance(product_evidence, dict) else raw.get("brand_listing_url"),
                    checked_at=(product_evidence or {}).get("checked_at") if isinstance(product_evidence, dict) else checked_at,
                    as_of=now, stale_after_days=stale_after_days, blocks_import=True,
                    proof_boundaries=["Product-page presence does not prove authorization, current stock, inventory quantity, or sales."],
                )
            )

        contact_evidence = raw.get("contact_evidence") if isinstance(raw.get("contact_evidence"), dict) else {}
        for field in _CONTACT_FIELDS:
            evidence = contact_evidence.get(field)
            if raw.get(field) in (None, "") or not _evidence_covered(
                evidence,
                as_of=now,
                stale_after_days=stale_after_days,
                fallback_checked_at=checked_at,
                fallback_url=location_url,
                expected_scope="dealer_contact_field",
            ):
                tasks.append(
                    _task(
                        scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                        source_id=source_id, field=f"contact_evidence.{field}",
                        issue_code="dealer.contact_evidence_missing_or_stale", severity="medium",
                        required_fields=[field, "status", "source_url", "checked_at", "reviewer_id", "evidence_scope", "value_status"],
                        acceptance_rule="Record the public value with current field-level provenance, or an explicit unavailable observation; unknown is not coverage.",
                        source_url=(evidence or {}).get("source_url") if isinstance(evidence, dict) else location_url,
                        checked_at=(evidence or {}).get("checked_at") if isinstance(evidence, dict) else checked_at,
                        as_of=now, stale_after_days=stale_after_days, blocks_import=False,
                    )
                )
        social_evidence = raw.get("social_evidence") if isinstance(raw.get("social_evidence"), dict) else {}
        for platform in _SOCIAL_PLATFORMS:
            evidence = social_evidence.get(platform)
            if not _evidence_covered(
                evidence,
                as_of=now,
                stale_after_days=stale_after_days,
                expected_scope="dealer_social_profile",
            ):
                tasks.append(
                    _task(
                        scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                        source_id=source_id, field=f"social_evidence.{platform}",
                        issue_code="dealer.social_evidence_missing_or_stale", severity="medium",
                        required_fields=["platform", "status", "source_url", "checked_at", "reviewer_id", "evidence_scope", "value_status"],
                        acceptance_rule="Record the exact public profile with current provenance, or an explicit unavailable observation; unknown is not coverage.",
                        source_url=(evidence or {}).get("source_url") if isinstance(evidence, dict) else None,
                        checked_at=(evidence or {}).get("checked_at") if isinstance(evidence, dict) else None,
                        as_of=now, stale_after_days=stale_after_days, blocks_import=False,
                    )
                )
        activity_evidence = raw.get("activity_evidence")
        if not _evidence_covered(
            activity_evidence,
            as_of=now,
            stale_after_days=stale_after_days,
            expected_scope="dealer_activity_page",
        ):
            tasks.append(
                _task(
                    scope="dealer", entity_type="dealer_location", entity_id=entity_id,
                    source_id=source_id, field="activity_evidence",
                    issue_code="dealer.activity_evidence_missing_or_stale", severity="medium",
                    required_fields=["status", "source_url", "checked_at", "reviewer_id", "evidence_scope", "value_status"],
                    acceptance_rule="Record one current dealer activity observation or explicit unavailable result with provenance.",
                    source_url=(activity_evidence or {}).get("source_url") if isinstance(activity_evidence, dict) else None,
                    checked_at=(activity_evidence or {}).get("checked_at") if isinstance(activity_evidence, dict) else None,
                    as_of=now, stale_after_days=stale_after_days, blocks_import=False,
                    proof_boundaries=["An activity page does not prove attendance, local impact, ROI, or sales."],
                )
            )

    dealer_universe_observed = int(
        report["coverage"]["global_location_coverage"].get("covered") or 0
    )
    if report["coverage"]["global_location_coverage"]["manifest_status"] != "accepted":
        tasks.append(
            _task(
                scope="dealer", entity_type="dealer_location_universe", entity_id="global_dealer_location_universe",
                source_id="", field="known_location_universe_denominator",
                issue_code="dealer.global_denominator_unavailable", severity="medium",
                required_fields=["manifest_version", "scope", "denominator", "entity_ids_sha256", "source_inventory_sha256", "methodology", "as_of", "reviewer_id"],
                acceptance_rule="Approve a bounded reproducible dealer_locations manifest with hashed entity/source inventories and a denominator not below observed entities.",
                source_url=None, checked_at=None, as_of=now,
                stale_after_days=stale_after_days, blocks_import=False,
                proof_boundaries=["Do not publish a global Dealer coverage rate before this denominator is accepted."],
            )
        )

    _append_unmapped_issue_tasks(
        tasks,
        report,
        handled_codes={
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
        },
        scope="dealer",
        as_of=now,
        stale_after_days=stale_after_days,
    )

    coverage = report["coverage"]
    gaps = {
        "source_current_check": coverage["source_evidence"],
        "stable_identity": coverage["stable_identity"],
        "contact_fields": coverage["contact_fields"],
        "social_profiles": coverage["social_profiles"],
        "viltrox_product_page_evidence": coverage["viltrox_product_page_evidence"],
        "dealer_activity_evidence": coverage["dealer_activity_evidence"],
    }
    return _queue_envelope(
        tasks=tasks,
        as_of=now,
        scope="dealer",
        evidence_gaps=gaps,
        universe_coverage={
            "dealer_location_universe": _universe_coverage_descriptor(
                coverage["global_location_coverage"]
            )
        },
    )


def build_event_dealer_remediation_queue(
    catalog: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    known_event_source_universe_denominator: Any = None,
    known_dealer_location_universe_denominator: Any = None,
) -> dict[str, Any]:
    """Return the combined deterministic queue without HTTP, SQL, or workers."""
    now = _as_utc(as_of)
    event = build_event_remediation_queue(
        catalog,
        as_of=now,
        stale_after_days=stale_after_days,
        known_source_universe_denominator=known_event_source_universe_denominator,
    )
    dealer = build_dealer_remediation_queue(
        candidates,
        as_of=now,
        stale_after_days=stale_after_days,
        known_location_universe_denominator=known_dealer_location_universe_denominator,
    )
    return _queue_envelope(
        tasks=[*event["tasks"], *dealer["tasks"]],
        as_of=now,
        scope="event_dealer",
        evidence_gaps={"event": event["evidence_gaps"], "dealer": dealer["evidence_gaps"]},
        universe_coverage={
            **event["universe_coverage"],
            **dealer["universe_coverage"],
        },
    )
