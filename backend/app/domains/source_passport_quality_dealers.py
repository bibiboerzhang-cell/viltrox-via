"""Dealer-location aggregation for the offline source-passport audit."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.domains.source_passport_core import (
    CONTACT_FIELDS,
    COUNTRY_RE,
    SOCIAL_PLATFORMS,
    SOURCE_ID_RE,
    STABLE_LOCATION_RE,
    STABLE_ORG_RE,
    add_issue,
    evidence_result,
    freshness,
    publisher_passport,
)
from app.domains.source_passport_urls import canonical_source_url


@dataclass
class DealerLocationAudit:
    """Counters and identities collected from reviewed Dealer locations."""

    source_ids: list[str] = field(default_factory=list)
    stable_org_keys: list[str] = field(default_factory=list)
    stable_location_keys: list[str] = field(default_factory=list)
    natural_keys: list[tuple[str, str, str]] = field(default_factory=list)
    location_urls: list[str] = field(default_factory=list)
    valid_location_urls: int = 0
    valid_product_urls: int = 0
    publisher_declared: int = 0
    publisher_verified: int = 0
    fresh_rows: int = 0
    contact_values: int = 0
    current_contact_evidence: int = 0
    social_values: int = 0
    current_social_evidence: int = 0
    current_product_evidence: int = 0
    current_activity_evidence: int = 0

    @property
    def duplicate_source_ids(self) -> list[str]:
        return _duplicates(self.source_ids, empty="")

    @property
    def duplicate_location_keys(self) -> list[str]:
        return _duplicates(self.stable_location_keys, empty="")

    @property
    def duplicate_natural_keys(self) -> list[tuple[str, str, str]]:
        return _duplicates(self.natural_keys, empty=("", "", ""))

    @property
    def shared_location_urls(self) -> dict[str, int]:
        return {
            value: count
            for value, count in sorted(Counter(self.location_urls).items())
            if value and count > 1
        }


def _duplicates(values: list[Any], *, empty: Any) -> list[Any]:
    return sorted(value for value, count in Counter(values).items() if value != empty and count > 1)


def _collect_and_validate_identity(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
) -> None:
    source_id = str(raw.get("source_id") or "").strip()
    stable_org_key = str(raw.get("stable_org_key") or "").strip()
    stable_location_key = str(raw.get("stable_location_key") or "").strip()
    country = str(raw.get("country_code") or raw.get("country") or "").strip().upper()
    state.source_ids.append(source_id)
    state.stable_org_keys.append(stable_org_key)
    state.stable_location_keys.append(stable_location_key)
    state.natural_keys.append(
        (
            str(raw.get("name") or "").strip().casefold(),
            str(raw.get("address") or "").strip().casefold(),
            country,
        )
    )
    validations = (
        (
            bool(source_id) and not SOURCE_ID_RE.fullmatch(source_id),
            "dealer.source_id_invalid",
            f"{path}.source_id",
            "Dealer source id does not satisfy the stable identity contract",
        ),
        (
            bool(stable_org_key) and not STABLE_ORG_RE.fullmatch(stable_org_key),
            "dealer.stable_org_key_invalid",
            f"{path}.stable_org_key",
            "Dealer organization key is invalid",
        ),
        (
            bool(stable_location_key) and not STABLE_LOCATION_RE.fullmatch(stable_location_key),
            "dealer.stable_location_key_invalid",
            f"{path}.stable_location_key",
            "Dealer location key is invalid",
        ),
        (
            bool(country) and not COUNTRY_RE.fullmatch(country),
            "dealer.country_invalid",
            f"{path}.country",
            "Dealer country must be ISO alpha-2",
        ),
    )
    for invalid, code, issue_path, message in validations:
        if invalid:
            add_issue(issues, "error", code, issue_path, message)


def _audit_location_and_publisher(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    location_url = canonical_source_url(raw.get("location_source_url"))
    if location_url:
        state.valid_location_urls += 1
        state.location_urls.append(location_url)
    else:
        add_issue(
            issues,
            "error",
            "dealer.location_source_url_invalid",
            f"{path}.location_source_url",
            "Dealer location requires a canonical public HTTPS source",
        )
    if canonical_source_url(raw.get("brand_listing_url")):
        state.valid_product_urls += 1

    passport = publisher_passport(raw, as_of=as_of, stale_after_days=stale_after_days)
    if passport["declared"]:
        state.publisher_declared += 1
    else:
        add_issue(
            issues,
            "warning",
            "dealer.publisher_tier_missing",
            f"{path}.publisher_tier",
            "Dealer publisher relationship tier is not declared",
        )
    if passport["verified"]:
        state.publisher_verified += 1
    elif passport["declared"]:
        add_issue(
            issues,
            "warning",
            "dealer.publisher_identity_unverified",
            f"{path}.publisher_identity_evidence",
            "declared Dealer publisher tier lacks current identity evidence",
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
            "dealer.verification_not_fresh",
            f"{path}.verified_at",
            "Dealer location has no current timezone-aware verification anchor",
        )


def _audit_contact_evidence(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    contact_map = raw.get("contact_evidence")
    if not isinstance(contact_map, Mapping):
        contact_map = {}
    for field_name in CONTACT_FIELDS:
        if raw.get(field_name) in (None, ""):
            continue
        state.contact_values += 1
        result = evidence_result(
            contact_map.get(field_name),
            expected_scope="dealer_contact_field",
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
        if result["valid"]:
            state.current_contact_evidence += 1
        else:
            add_issue(
                issues,
                "warning",
                "dealer.contact_evidence_incomplete",
                f"{path}.contact_evidence.{field_name}",
                "populated contact field lacks current field-level evidence",
            )


def _audit_social_evidence(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    social_map = raw.get("social_evidence")
    if not isinstance(social_map, Mapping):
        social_map = {}
    valid_slots = 0
    for platform in SOCIAL_PLATFORMS:
        evidence = social_map.get(platform)
        if isinstance(evidence, Mapping) and canonical_source_url(evidence.get("source_url")):
            state.social_values += 1
        result = evidence_result(
            evidence,
            expected_scope="dealer_social_profile",
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
        if result["valid"]:
            state.current_social_evidence += 1
            valid_slots += 1
    if valid_slots < len(SOCIAL_PLATFORMS):
        add_issue(
            issues,
            "warning",
            "dealer.social_evidence_incomplete",
            f"{path}.social_evidence",
            "one or more social-platform slots lack current profile evidence",
        )


def _audit_page_evidence(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    checks = (
        (
            "viltrox_product_evidence",
            "dealer_viltrox_product_page",
            "current_product_evidence",
            "dealer.viltrox_product_evidence_incomplete",
            "declared product URL is not current structured Viltrox page evidence",
        ),
        (
            "activity_evidence",
            "dealer_activity_page",
            "current_activity_evidence",
            "dealer.activity_evidence_incomplete",
            "Dealer activity page has not been evidenced at this location grain",
        ),
    )
    for field_name, scope, counter_name, code, message in checks:
        result = evidence_result(
            raw.get(field_name),
            expected_scope=scope,
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
        if result["valid"]:
            setattr(state, counter_name, getattr(state, counter_name) + 1)
        else:
            add_issue(issues, "warning", code, f"{path}.{field_name}", message)


def _audit_dealer_row(
    raw: Mapping[str, Any],
    *,
    path: str,
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> None:
    common = {
        "path": path,
        "state": state,
        "issues": issues,
        "as_of": as_of,
        "stale_after_days": stale_after_days,
    }
    _collect_and_validate_identity(raw, path=path, state=state, issues=issues)
    _audit_location_and_publisher(raw, **common)
    _audit_contact_evidence(raw, **common)
    _audit_social_evidence(raw, **common)
    _audit_page_evidence(raw, **common)


def _add_duplicate_issues(
    state: DealerLocationAudit,
    issues: list[dict[str, str]],
) -> None:
    groups = (
        ("dealer.source_id_duplicate", state.duplicate_source_ids),
        ("dealer.stable_location_key_duplicate", state.duplicate_location_keys),
        ("dealer.natural_key_duplicate", state.duplicate_natural_keys),
    )
    for code, values in groups:
        for value in values:
            add_issue(issues, "error", code, "dealers", f"duplicate exact identity: {value!r}")


def audit_dealer_locations(
    dealers: list[Any],
    *,
    issues: list[dict[str, str]],
    as_of: datetime,
    stale_after_days: int,
) -> DealerLocationAudit:
    """Validate reviewed Dealer rows without changing the caller's values."""
    state = DealerLocationAudit()
    for index, raw in enumerate(dealers):
        path = f"dealers[{index}]"
        if not isinstance(raw, Mapping):
            add_issue(issues, "error", "dealer.row_invalid", path, "dealer must be an object")
            continue
        _audit_dealer_row(
            raw,
            path=path,
            state=state,
            issues=issues,
            as_of=as_of,
            stale_after_days=stale_after_days,
        )
    _add_duplicate_issues(state, issues)
    return state


__all__ = ["DealerLocationAudit", "audit_dealer_locations"]
