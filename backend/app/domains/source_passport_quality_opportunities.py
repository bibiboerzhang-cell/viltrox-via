"""Event-opportunity aggregation for the offline source-passport audit."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.domains.source_passport_core import (
    DEALER_LOCAL_LANES,
    STABLE_LOCATION_RE,
    add_issue,
    evidence_result,
)
from app.domains.source_passport_urls import canonical_source_url


@dataclass
class EventOpportunityAudit:
    """Counters and exact identities collected from opportunity rows."""

    ids: list[str] = field(default_factory=list)
    canonical_keys: list[str] = field(default_factory=list)
    external_keys: list[tuple[str, str]] = field(default_factory=list)
    source_links: int = 0
    valid_urls: int = 0
    fresh_evidence: int = 0
    dealer_local_rows: int = 0
    dealer_location_links: int = 0


def _audit_opportunity_row(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: EventOpportunityAudit,
    source_by_id: Mapping[str, Mapping[str, Any]],
    reviewed_dealer_location_keys: set[str],
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    opportunity_id = str(raw.get("id") or "").strip()
    canonical_key = str(raw.get("canonical_key") or "").strip()
    source_id = str(raw.get("source_id") or "").strip()
    external_key = str(raw.get("external_event_key") or "").strip()
    state.ids.append(opportunity_id)
    state.canonical_keys.append(canonical_key)
    state.external_keys.append((source_id, external_key))

    if source_id in source_by_id:
        state.source_links += 1
    else:
        add_issue(
            issues,
            "error",
            "opportunity.source_orphan",
            f"{path}.source_id",
            "opportunity source id does not resolve exactly",
        )
    if canonical_source_url(raw.get("official_url")):
        state.valid_urls += 1
    else:
        add_issue(
            issues,
            "error",
            "opportunity.official_url_invalid",
            f"{path}.official_url",
            "credential-free canonical HTTPS activity URL is required",
        )

    evidence = evidence_result(
        raw.get("activity_evidence"),
        expected_scope="event_official_listing",
        as_of=as_of,
        stale_after_days=stale_after_days,
    )
    if evidence["valid"]:
        state.fresh_evidence += 1
    else:
        add_issue(
            issues,
            "warning",
            "opportunity.activity_evidence_incomplete",
            f"{path}.activity_evidence",
            "activity requires current structured evidence with publisher tier",
        )

    lane = str(raw.get("lane") or "").strip()
    if lane not in DEALER_LOCAL_LANES:
        return
    state.dealer_local_rows += 1
    location_key = str(raw.get("dealer_stable_location_key") or "").strip()
    if location_key in reviewed_dealer_location_keys:
        state.dealer_location_links += 1
        return
    issue_code = (
        "opportunity.dealer_location_key_unresolved"
        if STABLE_LOCATION_RE.fullmatch(location_key)
        else "opportunity.dealer_location_key_missing"
    )
    add_issue(
        issues,
        "warning",
        issue_code,
        f"{path}.dealer_stable_location_key",
        "dealer/local activity lacks a resolved exact reviewed Dealer location key",
    )


def _add_duplicate_identity_issues(
    state: EventOpportunityAudit,
    issues: list[dict[str, str]],
) -> None:
    groups = (
        ("opportunity.id_duplicate", state.ids),
        ("opportunity.canonical_key_duplicate", state.canonical_keys),
        ("opportunity.external_key_duplicate", state.external_keys),
    )
    for code, values in groups:
        for value, count in Counter(values).items():
            if value not in ("", ("", "")) and count > 1:
                add_issue(
                    issues,
                    "error",
                    code,
                    "opportunities",
                    f"duplicate exact identity: {value!r}",
                )


def _reviewed_location_keys(dealers: list[Any]) -> set[str]:
    keys: set[str] = set()
    for item in dealers:
        if not isinstance(item, Mapping):
            continue
        value = str(item.get("stable_location_key") or "").strip()
        if STABLE_LOCATION_RE.fullmatch(value):
            keys.add(value)
    return keys


def audit_event_opportunities(
    opportunities: list[Any],
    dealers: list[Any],
    *,
    source_by_id: Mapping[str, Mapping[str, Any]],
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> EventOpportunityAudit:
    """Validate opportunity evidence and exact source/dealer linkages."""
    state = EventOpportunityAudit()
    reviewed_location_keys = _reviewed_location_keys(dealers)
    for index, raw in enumerate(opportunities):
        path = f"opportunities[{index}]"
        if not isinstance(raw, Mapping):
            add_issue(
                issues,
                "error",
                "opportunity.row_invalid",
                path,
                "opportunity must be an object",
            )
            continue
        _audit_opportunity_row(
            raw,
            path=path,
            state=state,
            source_by_id=source_by_id,
            reviewed_dealer_location_keys=reviewed_location_keys,
            issues=issues,
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
    _add_duplicate_identity_issues(state, issues)
    return state


__all__ = ["EventOpportunityAudit", "audit_event_opportunities"]
