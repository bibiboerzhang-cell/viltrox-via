"""Event-source row aggregation for the offline source-passport audit."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.domains.source_passport_core import (
    PRIMARY_PUBLISHER_TIERS,
    SECONDARY_PUBLISHER_TIERS,
    SOURCE_ID_RE,
    add_issue,
    freshness,
    publisher_passport,
)
from app.domains.source_passport_urls import source_url_identity


@dataclass
class EventSourceAudit:
    """Counters and identities collected from reviewed event-source rows."""

    source_by_id: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)
    canonical_urls: list[str] = field(default_factory=list)
    publisher_tiers: Counter[str] = field(default_factory=Counter)
    valid_urls: int = 0
    publisher_declared: int = 0
    publisher_verified: int = 0
    publisher_primary: int = 0
    publisher_secondary: int = 0
    fresh_rows: int = 0

    @property
    def duplicate_ids(self) -> list[str]:
        return _duplicate_values(self.source_ids)

    @property
    def duplicate_urls(self) -> list[str]:
        return _duplicate_values(self.canonical_urls)


def _duplicate_values(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if value and count > 1)


def _audit_source_row(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: EventSourceAudit,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    source_id = str(raw.get("id") or "").strip()
    state.source_ids.append(source_id)
    if not SOURCE_ID_RE.fullmatch(source_id):
        add_issue(
            issues,
            "error",
            "source.id_invalid",
            f"{path}.id",
            "stable source id is required",
        )
    elif source_id not in state.source_by_id:
        state.source_by_id[source_id] = raw

    url_identity = source_url_identity(raw.get("canonical_url"))
    if url_identity["valid"]:
        state.valid_urls += 1
        state.canonical_urls.append(str(url_identity["canonical_url"]))
    else:
        add_issue(
            issues,
            "error",
            "source.canonical_url_invalid",
            f"{path}.canonical_url",
            "credential-free canonical HTTPS URL is required",
        )

    passport = publisher_passport(raw, as_of=as_of, stale_after_days=stale_after_days)
    state.publisher_tiers[str(passport["publisher_tier"])] += 1
    if passport["declared"]:
        state.publisher_declared += 1
    else:
        add_issue(
            issues,
            "warning",
            "source.publisher_tier_missing",
            f"{path}.publisher_tier",
            "publisher relationship tier is not declared",
        )
    if passport["verified"]:
        state.publisher_verified += 1
        if passport["publisher_tier"] in PRIMARY_PUBLISHER_TIERS:
            state.publisher_primary += 1
        elif passport["publisher_tier"] in SECONDARY_PUBLISHER_TIERS:
            state.publisher_secondary += 1
    elif passport["declared"]:
        add_issue(
            issues,
            "warning",
            "source.publisher_identity_unverified",
            f"{path}.publisher_identity_evidence",
            "declared publisher tier lacks current structured identity evidence",
        )

    row_freshness = freshness(
        raw.get("verified_at") or raw.get("source_checked_at"),
        as_of=as_of,
        stale_after_days=stale_after_days,
    )
    if row_freshness["status"] == "fresh":
        state.fresh_rows += 1
    else:
        add_issue(
            issues,
            "warning",
            "source.verification_not_fresh",
            f"{path}.verified_at",
            "source row has no current timezone-aware verification anchor",
        )


def _add_duplicate_issues(
    state: EventSourceAudit,
    issues: list[dict[str, str]],
) -> None:
    for value in state.duplicate_ids:
        add_issue(
            issues,
            "error",
            "source.id_duplicate",
            "sources",
            f"duplicate source id: {value}",
        )
    for value in state.duplicate_urls:
        add_issue(
            issues,
            "error",
            "source.url_identity_duplicate",
            "sources",
            f"duplicate canonical source URL identity: {value}",
        )


def audit_event_sources(
    sources: list[Any],
    *,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> EventSourceAudit:
    """Validate reviewed source rows and retain exact-link identities."""
    state = EventSourceAudit()
    for index, raw in enumerate(sources):
        path = f"sources[{index}]"
        if not isinstance(raw, Mapping):
            add_issue(issues, "error", "source.row_invalid", path, "source must be an object")
            continue
        _audit_source_row(
            raw,
            path=path,
            state=state,
            issues=issues,
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
    _add_duplicate_issues(state, issues)
    return state


__all__ = ["EventSourceAudit", "audit_event_sources"]
