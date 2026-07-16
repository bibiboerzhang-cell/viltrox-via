"""Shared truth primitives for Dealer/Event source passports."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from app.domains.source_passport_urls import source_url_identity


CONTRACT_ID = "vkpi.dealer_event.source_passport_quality"
CONTRACT_VERSION = 1
SNAPSHOT_VERSION = 1
DEFAULT_STALE_AFTER_DAYS = 30

PUBLISHER_TIERS = frozenset(
    {
        "organizer_owned",
        "retailer_owned",
        "venue_owned",
        "brand_owned",
        "platform_hosted_profile",
        "third_party_listing",
        "unknown",
    }
)
PRIMARY_PUBLISHER_TIERS = frozenset(
    {"organizer_owned", "retailer_owned", "venue_owned", "brand_owned"}
)
SECONDARY_PUBLISHER_TIERS = frozenset(
    {"platform_hosted_profile", "third_party_listing"}
)

CONTACT_FIELDS = ("phone", "contact_email", "store_hours", "public_services")
SOCIAL_PLATFORMS = ("instagram", "facebook", "youtube", "tiktok", "x")
DEALER_LOCAL_LANES = frozenset({"dealer_event", "local_activity"})

SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{5,127}$")
STABLE_ORG_RE = re.compile(r"^dealer_org_[a-z0-9]{8,64}$")
STABLE_LOCATION_RE = re.compile(r"^dealer_loc_[a-z0-9]{8,64}$")
REVIEWER_ID_RE = re.compile(r"^staff_[1-9][0-9]{0,18}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def freshness(value: Any, *, as_of: datetime, stale_after_days: int) -> dict[str, Any]:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return {"status": "unavailable", "verified_at": None, "age_days": None}
    age_days = (as_of - parsed).total_seconds() / 86400
    if age_days < 0:
        status = "invalid_future"
    elif age_days > stale_after_days:
        status = "stale"
    else:
        status = "fresh"
    return {
        "status": status,
        "verified_at": parsed.isoformat(),
        "age_days": round(age_days, 3),
    }


def rate(covered: int, denominator: int | None, *, reason: str = "") -> dict[str, Any]:
    covered = max(0, int(covered or 0))
    if denominator is None:
        return {
            "covered": covered,
            "denominator": None,
            "rate": None,
            "status": "unavailable",
            "reason": reason or "denominator_unavailable",
        }
    denominator = max(0, int(denominator))
    return {
        "covered": covered,
        "denominator": denominator,
        "rate": round(covered / denominator, 4) if denominator else None,
        "status": "measured" if denominator else "unavailable",
        "reason": "" if denominator else "denominator_not_positive",
    }


def evidence_result(
    value: Any,
    *,
    expected_scope: str,
    as_of: datetime,
    stale_after_days: int,
    require_publisher_tier: bool = True,
) -> dict[str, Any]:
    evidence = value if isinstance(value, Mapping) else {}
    tier = str(
        evidence.get("publisher_tier") or evidence.get("source_tier") or "unknown"
    ).strip()
    timestamp = evidence.get("verified_at") or evidence.get("observed_at") or evidence.get(
        "checked_at"
    )
    freshness_result = freshness(
        timestamp,
        as_of=as_of,
        stale_after_days=stale_after_days,
    )
    source_identity = source_url_identity(evidence.get("source_url"))
    reviewer_id = str(evidence.get("reviewer_id") or "").strip()
    status = str(evidence.get("status") or "").strip().casefold()
    value_status = str(evidence.get("value_status") or "").strip().casefold()
    scope = str(evidence.get("evidence_scope") or "").strip()
    tier_valid = tier in PUBLISHER_TIERS and tier != "unknown"
    valid = bool(
        status in {"verified", "observed", "public_listing_observed"}
        and value_status == "observed"
        and scope == expected_scope
        and REVIEWER_ID_RE.fullmatch(reviewer_id)
        and source_identity["valid"]
        and freshness_result["status"] == "fresh"
        and (tier_valid or not require_publisher_tier)
    )
    return {
        "valid": valid,
        "status": status or "unavailable",
        "publisher_tier": tier if tier in PUBLISHER_TIERS else "invalid",
        "publisher_class": (
            "primary"
            if tier in PRIMARY_PUBLISHER_TIERS
            else "secondary"
            if tier in SECONDARY_PUBLISHER_TIERS
            else "unresolved"
        ),
        "source_url_valid": bool(source_identity["valid"]),
        "freshness": freshness_result,
        "reviewer_valid": bool(REVIEWER_ID_RE.fullmatch(reviewer_id)),
        "scope_valid": scope == expected_scope,
        "value_status_valid": value_status == "observed",
    }


def publisher_passport(
    row: Mapping[str, Any],
    *,
    as_of: datetime,
    stale_after_days: int,
) -> dict[str, Any]:
    declared_tier = str(row.get("publisher_tier") or "unknown").strip()
    evidence = row.get("publisher_identity_evidence")
    result = evidence_result(
        evidence,
        expected_scope="publisher_identity",
        as_of=as_of,
        stale_after_days=stale_after_days,
    )
    evidence_tier = result["publisher_tier"]
    declared_tier_valid = declared_tier in PUBLISHER_TIERS and declared_tier != "unknown"
    tier = (
        declared_tier
        if declared_tier_valid
        else evidence_tier
        if evidence_tier not in {"unknown", "invalid"}
        else declared_tier
    )
    declared = tier in PUBLISHER_TIERS and tier != "unknown"
    if declared and isinstance(evidence, Mapping):
        result["valid"] = bool(
            result["valid"]
            and evidence_tier == tier
            and (not declared_tier_valid or declared_tier == evidence_tier)
        )
    else:
        result["valid"] = False
    return {
        **result,
        "publisher_tier": tier if tier in PUBLISHER_TIERS else "invalid",
        "declared": declared,
        "verified": bool(result["valid"]),
    }


def add_issue(
    issues: list[dict[str, str]],
    severity: str,
    code: str,
    path: str,
    message: str,
) -> None:
    issues.append(
        {"severity": severity, "code": code, "path": path, "message": message}
    )


__all__ = [
    "CONTACT_FIELDS",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "COUNTRY_RE",
    "DEALER_LOCAL_LANES",
    "DEFAULT_STALE_AFTER_DAYS",
    "PRIMARY_PUBLISHER_TIERS",
    "PUBLISHER_TIERS",
    "REVIEWER_ID_RE",
    "SECONDARY_PUBLISHER_TIERS",
    "SNAPSHOT_VERSION",
    "SOCIAL_PLATFORMS",
    "SOURCE_ID_RE",
    "STABLE_LOCATION_RE",
    "STABLE_ORG_RE",
    "add_issue",
    "as_utc",
    "canonical_json_sha256",
    "evidence_result",
    "freshness",
    "publisher_passport",
    "rate",
]
