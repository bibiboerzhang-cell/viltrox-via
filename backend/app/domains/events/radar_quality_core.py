"""Shared pure primitives for Event and Dealer evidence-quality contracts."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.domains.commerce.dealer_identity import (
    normalize_official_domain,
    propose_stable_location_key,
    propose_stable_org_key,
)

CONTRACT_ID = "vkpi.event_dealer.quality"
CONTRACT_VERSION = 2
REMEDIATION_QUEUE_ID = "vkpi.event_dealer.remediation"
REMEDIATION_QUEUE_VERSION = 1
DEFAULT_STALE_AFTER_DAYS = 30

_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{5,127}$")
_STABLE_ORG_RE = re.compile(r"^dealer_org_[a-z0-9]{8,64}$")
_STABLE_LOCATION_RE = re.compile(r"^dealer_loc_[a-z0-9]{8,64}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_SAFE_REVIEWER_ID_RE = re.compile(r"^staff_[1-9][0-9]{0,18}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNIVERSE_MANIFEST_VERSION = 1

_CURRENT_EVIDENCE_STATUSES = {"verified", "observed", "public_listing_observed"}
_UNKNOWN_EVIDENCE_STATUSES = {
    "", "unknown", "unavailable", "unverified", "not_checked", "not_reviewed", "pending",
}
_SOCIAL_PLATFORMS = ("instagram", "facebook", "youtube", "tiktok", "x")
_CONTACT_FIELDS = ("phone", "contact_email", "store_hours", "public_services")
_NONACTIVE_SOURCE_STATUSES = {"hold", "blocked", "retired"}
_POSITIVE_VILTROX_STATUSES = {"brand_listed", "confirmed_exhibitor"}

_UNSUPPORTED_POSITIVE_CLAIMS = {
    "authorized",
    "is_authorized",
    "official_dealer",
    "official_viltrox_dealer",
    "in_stock",
    "inventory",
    "inventory_quantity",
    "sales",
    "sales_attribution",
    "gmv",
    "roi",
    "attendance",
    "attendee_count",
    "local_impact",
    "business_outcome",
}
_NONPOSITIVE_TEXT = {
    "", "unknown", "unavailable", "unverified", "not_measured", "not measured", "pending",
    "none", "n/a", "needs_viltrox_confirmation",
}


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return value.astimezone(timezone.utc)


def _review_status(
    *,
    declared_reviewed: bool,
    evidence_contract_valid: bool,
    freshness_status: str,
) -> str:
    """Describe review truth without upgrading missing or stale evidence."""
    if not declared_reviewed:
        return "not_reviewed"
    if not evidence_contract_valid:
        return "review_contract_invalid"
    if freshness_status == "fresh":
        return "reviewed_current"
    if freshness_status == "stale":
        return "reviewed_stale"
    if freshness_status == "invalid_future":
        return "review_timestamp_invalid"
    return "review_timestamp_unavailable"


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


def _is_https_url(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or any(character.isspace() for character in text):
        return False
    parsed = urlsplit(text)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def _rate(numerator: int, denominator: int | None, *, unavailable_reason: str = "") -> dict[str, Any]:
    numerator = max(0, int(numerator or 0))
    if denominator is None:
        return {
            "covered": numerator,
            "denominator": None,
            "rate": None,
            "status": "unavailable",
            "reason": unavailable_reason or "denominator_unavailable",
        }
    denominator = int(denominator)
    if denominator <= 0:
        return {
            "covered": numerator,
            "denominator": denominator,
            "rate": None,
            "status": "unavailable",
            "reason": "denominator_not_positive",
        }
    return {
        "covered": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4),
        "status": "measured",
        "reason": "",
    }


def _reviewed_location_key_universe(value: Any) -> frozenset[str] | None:
    """Normalize an explicitly supplied reviewed Dealer-location universe."""
    if value is None:
        return None
    if isinstance(value, (str, bytes, Mapping)):
        raise ValueError("reviewed_dealer_location_keys must be a collection of exact keys")
    try:
        keys = frozenset(str(item or "").strip() for item in value)
    except TypeError as exc:
        raise ValueError("reviewed_dealer_location_keys must be iterable") from exc
    if any(not _STABLE_LOCATION_RE.fullmatch(key) for key in keys):
        raise ValueError("reviewed_dealer_location_keys contains an invalid exact key")
    return keys


def _exact_linkage_coverage(
    resolved: int,
    denominator: int,
    reviewed_universe: frozenset[str] | None,
) -> dict[str, Any]:
    if reviewed_universe is not None:
        return _rate(resolved, denominator)
    return {
        "covered": 0,
        "denominator": denominator,
        "rate": None,
        "status": "unavailable",
        "reason": "reviewed_dealer_universe_not_supplied",
    }


def _canonical_source_url(value: Any) -> str:
    """Return a stable public-source URL or an empty string when unsafe.

    Universe manifests are content-addressed, so cosmetic URL differences must
    not change their digest.  Fragments and path traversal are rejected rather
    than silently folded into a supposedly stable source identity.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not _is_https_url(text):
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return ""
    if parsed.fragment:
        return ""
    hostname = str(parsed.hostname or "").rstrip(".")
    try:
        hostname = hostname.encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError):
        return ""
    if not hostname:
        return ""
    path = parsed.path or "/"
    if any(segment in {".", ".."} for segment in path.split("/")):
        return ""
    path = path.rstrip("/") or "/"
    try:
        query = urlencode(
            sorted(parse_qsl(parsed.query, keep_blank_values=True)),
            doseq=True,
        )
    except ValueError:
        return ""
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port not in (None, 443):
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, path, query, ""))


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_manifest_entity_ids(
    value: Any,
    *,
    expected_scope: str,
) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    validator = _SOURCE_ID_RE if expected_scope == "event_sources" else _STABLE_LOCATION_RE
    canonical: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        entity_id = item.strip()
        if not validator.fullmatch(entity_id):
            return None
        canonical.append(entity_id)
    if len(set(canonical)) != len(canonical):
        return None
    return sorted(canonical)


def _canonical_manifest_sources(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list) or not value:
        return None
    canonical: list[dict[str, str]] = []
    source_ids: set[str] = set()
    source_urls: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"source_id", "canonical_url"}:
            return None
        raw_source_id = item.get("source_id")
        if not isinstance(raw_source_id, str):
            return None
        source_id = raw_source_id.strip()
        canonical_url = _canonical_source_url(item.get("canonical_url"))
        if (
            not _SOURCE_ID_RE.fullmatch(source_id)
            or not canonical_url
            or source_id in source_ids
            or canonical_url in source_urls
        ):
            return None
        source_ids.add(source_id)
        source_urls.add(canonical_url)
        canonical.append({"source_id": source_id, "canonical_url": canonical_url})
    return sorted(canonical, key=lambda item: (item["source_id"], item["canonical_url"]))


def _canonical_observed_inventory(
    value: Any,
    *,
    expected_scope: str,
) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    entity_validator = _SOURCE_ID_RE if expected_scope == "event_sources" else _STABLE_LOCATION_RE
    canonical: list[dict[str, str]] = []
    entity_ids: set[str] = set()
    source_ids: set[str] = set()
    source_urls: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return None
        raw_entity_id = item.get("entity_id")
        raw_source_id = item.get("source_id")
        if not isinstance(raw_entity_id, str) or not isinstance(raw_source_id, str):
            return None
        entity_id = raw_entity_id.strip()
        source_id = raw_source_id.strip()
        canonical_url = _canonical_source_url(item.get("canonical_url"))
        if (
            not entity_validator.fullmatch(entity_id)
            or not _SOURCE_ID_RE.fullmatch(source_id)
            or not canonical_url
            or entity_id in entity_ids
            or source_id in source_ids
            or canonical_url in source_urls
            or (expected_scope == "event_sources" and entity_id != source_id)
        ):
            return None
        entity_ids.add(entity_id)
        source_ids.add(source_id)
        source_urls.add(canonical_url)
        canonical.append(
            {
                "entity_id": entity_id,
                "source_id": source_id,
                "canonical_url": canonical_url,
            }
        )
    return sorted(
        canonical,
        key=lambda item: (item["entity_id"], item["source_id"], item["canonical_url"]),
    )


def _global_coverage(
    observed: int,
    manifest: Any,
    *,
    observed_inventory: Any,
    issue,
    code: str,
    path: str,
    expected_scope: str,
) -> dict[str, Any]:
    if manifest is None:
        return {
            **_rate(
                observed,
                None,
                unavailable_reason="known_universe_denominator_unavailable",
            ),
            "manifest_status": "unavailable",
            "manifest": None,
        }
    if not isinstance(manifest, Mapping):
        issue(
            "error",
            f"{code}.manifest_required",
            path,
            "a structured reviewed universe manifest is required; a bare denominator is not accepted",
        )
        return {
            **_rate(observed, None, unavailable_reason="universe_manifest_required"),
            "manifest_status": "invalid",
            "manifest": None,
        }

    def invalid_manifest(message: str) -> dict[str, Any]:
        issue("error", f"{code}.manifest_invalid", path, message)
        return {
            **_rate(observed, None, unavailable_reason="invalid_universe_manifest"),
            "manifest_status": "invalid",
            "manifest": None,
        }

    denominator = manifest.get("denominator")
    manifest_version = manifest.get("manifest_version")
    reviewer_id = manifest.get("reviewer_id")
    entity_ids_sha256 = manifest.get("entity_ids_sha256")
    source_inventory_sha256 = manifest.get("source_inventory_sha256")
    scope = manifest.get("scope")
    methodology = manifest.get("methodology")
    manifest_as_of = manifest.get("as_of")
    base_valid = (
        isinstance(manifest_version, int)
        and not isinstance(manifest_version, bool)
        and manifest_version == _UNIVERSE_MANIFEST_VERSION
        and isinstance(scope, str)
        and scope == expected_scope
        and isinstance(denominator, int)
        and not isinstance(denominator, bool)
        and denominator > 0
        and isinstance(entity_ids_sha256, str)
        and bool(_SHA256_RE.fullmatch(entity_ids_sha256))
        and isinstance(source_inventory_sha256, str)
        and bool(_SHA256_RE.fullmatch(source_inventory_sha256))
        and isinstance(manifest_as_of, str)
        and _parse_timestamp(manifest_as_of) is not None
        and isinstance(methodology, str)
        and bool(methodology.strip())
        and isinstance(reviewer_id, str)
        and bool(_SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id.strip()))
    )
    if not base_valid:
        return invalid_manifest(
            "universe manifest requires an exact version/scope, reviewed metadata, complete canonical inventories, and matching SHA-256 digests"
        )

    entity_ids = _canonical_manifest_entity_ids(
        manifest.get("entity_ids"),
        expected_scope=expected_scope,
    )
    source_inventory = _canonical_manifest_sources(manifest.get("source_inventory"))
    observed_entries = _canonical_observed_inventory(
        observed_inventory,
        expected_scope=expected_scope,
    )
    if entity_ids is None or source_inventory is None or observed_entries is None:
        return invalid_manifest(
            "universe inventories and observed mappings must contain unique stable IDs and canonical HTTPS sources"
        )
    if denominator != len(entity_ids) or len(source_inventory) != len(entity_ids):
        return invalid_manifest(
            "denominator and complete source inventory must match the canonical entity inventory count"
        )
    if expected_scope == "event_sources" and {
        item["source_id"] for item in source_inventory
    } != set(entity_ids):
        return invalid_manifest(
            "event source inventory IDs must exactly match the event entity inventory"
        )

    entity_digest = _canonical_json_sha256(entity_ids)
    source_digest = _canonical_json_sha256(source_inventory)
    if (
        entity_ids_sha256 != entity_digest
        or source_inventory_sha256 != source_digest
    ):
        return invalid_manifest(
            "universe inventory digests do not match their canonical contents"
        )

    manifest_entities = set(entity_ids)
    manifest_sources = {
        item["source_id"]: item["canonical_url"] for item in source_inventory
    }
    if any(
        item["entity_id"] not in manifest_entities
        or manifest_sources.get(item["source_id"]) != item["canonical_url"]
        for item in observed_entries
    ):
        return invalid_manifest(
            "observed entity/source mappings must be an exact subset of the reviewed universe inventories"
        )
    if isinstance(observed, bool) or not isinstance(observed, int) or not (
        0 <= observed <= len(observed_entries)
    ):
        return invalid_manifest(
            "coverage numerator must contain only unique quality-validated observed entities"
        )

    measured = _rate(observed, denominator)
    return {
        **measured,
        "manifest_status": "accepted",
        "manifest": {
            "manifest_version": _UNIVERSE_MANIFEST_VERSION,
            "reviewer_id": reviewer_id.strip(),
            "entity_count": len(entity_ids),
            "source_count": len(source_inventory),
            "entity_ids_sha256": entity_digest,
            "source_inventory_sha256": source_digest,
        },
    }


def _freshness(value: Any, *, as_of: datetime, stale_after_days: int) -> dict[str, Any]:
    checked_at = _parse_timestamp(value)
    if checked_at is None:
        return {"status": "unavailable", "checked_at": None, "age_days": None}
    age_seconds = (as_of - checked_at).total_seconds()
    if age_seconds < -300:
        return {
            "status": "invalid_future",
            "checked_at": checked_at.isoformat(),
            "age_days": round(age_seconds / 86400, 3),
        }
    age_days = max(0.0, age_seconds / 86400)
    return {
        "status": "fresh" if age_days <= stale_after_days else "stale",
        "checked_at": checked_at.isoformat(),
        "age_days": round(age_days, 3),
    }


def _positive_claim(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() not in _NONPOSITIVE_TEXT


def _evidence_contract_valid(
    evidence: Any,
    *,
    expected_scope: str,
    allowed_value_statuses: set[str] | None = None,
) -> bool:
    """Validate non-PII reviewer identity plus explicit evidence semantics."""
    if not isinstance(evidence, Mapping):
        return False
    reviewer_id = str(evidence.get("reviewer_id") or "").strip()
    evidence_scope = str(evidence.get("evidence_scope") or "").strip()
    value_status = str(evidence.get("value_status") or "").strip().casefold()
    return bool(
        _SAFE_REVIEWER_ID_RE.fullmatch(reviewer_id)
        and evidence_scope == expected_scope
        and value_status in (allowed_value_statuses or {"observed"})
    )


def _evidence_covered(
    evidence: Any,
    *,
    as_of: datetime,
    stale_after_days: int,
    fallback_checked_at: Any = None,
    fallback_url: Any = None,
    expected_scope: str,
    allowed_value_statuses: set[str] | None = None,
) -> bool:
    if not isinstance(evidence, dict):
        return False
    if not _evidence_contract_valid(
        evidence,
        expected_scope=expected_scope,
        allowed_value_statuses=allowed_value_statuses,
    ):
        return False
    status = str(evidence.get("status") or "").strip().casefold()
    if status in _UNKNOWN_EVIDENCE_STATUSES or status not in _CURRENT_EVIDENCE_STATUSES:
        return False
    source_url = evidence.get("source_url") or fallback_url
    if not _is_https_url(source_url):
        return False
    freshness = _freshness(
        evidence.get("checked_at") or fallback_checked_at,
        as_of=as_of,
        stale_after_days=stale_after_days,
    )
    return freshness["status"] == "fresh"


def _identity_proposal(row: dict[str, Any]) -> tuple[str, str]:
    name = str(row.get("organization_name") or row.get("name") or "").split("·", 1)[0].strip()
    country = str(row.get("country_code") or row.get("country") or "").strip().upper()
    official_domain = str(row.get("official_domain") or "").strip()
    if not official_domain:
        official_domain = normalize_official_domain(
            row.get("location_source_url") or row.get("brand_listing_url") or ""
        )
    org_key = propose_stable_org_key(name, country_code=country, official_domain=official_domain)
    location_key = propose_stable_location_key(
        org_key,
        country_code=country,
        address=row.get("address"),
        postal_code=row.get("postal_code") or "",
    )
    return org_key, location_key


def _source_id_proposal(value: Any) -> str:
    text = str(value or "").strip()
    if not _is_https_url(text):
        return ""
    parsed = urlsplit(text)
    canonical = f"{str(parsed.hostname or '').casefold()}{parsed.path.rstrip('/') or '/'}"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"dealer_source_{digest}"


def _issue_factory(issues: list[dict[str, str]]):
    def issue(severity: str, code: str, path: str, message: str) -> None:
        issues.append(
            {
                "severity": str(severity),
                "code": str(code),
                "path": str(path),
                "message": str(message),
            }
        )

    return issue


def _issue_counts(issues: Iterable[dict[str, str]]) -> dict[str, int]:
    counter = Counter(str(item.get("severity") or "warning") for item in issues)
    return {"errors": counter.get("error", 0), "warnings": counter.get("warning", 0)}


def _stable_task_identity(*parts: Any) -> tuple[str, str]:
    """Return a human-inspectable dedupe key and deterministic task id.

    Neither value includes ``as_of`` or mutable evidence, so a task keeps the
    same identity across repeated previews until its underlying entity/field
    changes.  This function performs no I/O.
    """
    normalized = [re.sub(r"\s+", " ", str(part or "").strip().casefold()) for part in parts]
    dedupe_key = "|".join(normalized)
    digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]
    return dedupe_key, f"remediation_{digest}"


def _task_freshness(
    value: Any,
    *,
    as_of: datetime,
    stale_after_days: int,
) -> dict[str, Any]:
    freshness = _freshness(value, as_of=as_of, stale_after_days=stale_after_days)
    checked_at = _parse_timestamp(value)
    if checked_at is None:
        due_at = as_of.isoformat()
        due_status = "due_now_missing_evidence"
    elif freshness["status"] == "stale":
        due_at = (checked_at + timedelta(days=stale_after_days)).isoformat()
        due_status = "overdue"
    elif freshness["status"] == "invalid_future":
        due_at = as_of.isoformat()
        due_status = "due_now_invalid_timestamp"
    else:
        due_at = (checked_at + timedelta(days=stale_after_days)).isoformat()
        due_status = "scheduled"
    return {
        "status": freshness["status"],
        "checked_at": freshness["checked_at"],
        "age_days": freshness["age_days"],
        "stale_after_days": stale_after_days,
        "due_at": due_at,
        "due_status": due_status,
    }


def _task(
    *,
    scope: str,
    entity_type: str,
    entity_id: str,
    source_id: str,
    field: str,
    issue_code: str,
    severity: str,
    required_fields: list[str],
    acceptance_rule: str,
    source_url: Any,
    checked_at: Any,
    as_of: datetime,
    stale_after_days: int,
    blocks_import: bool,
    proof_boundaries: list[str] | None = None,
) -> dict[str, Any]:
    dedupe_key, task_id = _stable_task_identity(
        scope,
        entity_type,
        entity_id,
        source_id,
        field,
        issue_code,
    )
    url = str(source_url or "").strip()
    return {
        "task_id": task_id,
        "dedupe_key": dedupe_key,
        "scope": scope,
        "entity_type": entity_type,
        "entity_id": str(entity_id or "").strip(),
        "source_id": str(source_id or "").strip() or None,
        "field": field,
        "issue_code": issue_code,
        "severity": severity,
        "blocks_import": bool(blocks_import),
        "required_evidence": {
            "fields": list(required_fields),
            "acceptance_rule": acceptance_rule,
            "unknown_counts_as_covered": False,
            "proof_boundaries": list(proof_boundaries or []),
        },
        "source_url": url if _is_https_url(url) else None,
        "freshness": _task_freshness(
            checked_at,
            as_of=as_of,
            stale_after_days=stale_after_days,
        ),
        "manual_review_status": "pending",
        "manual_review": {
            "required": True,
            "status": "pending",
            "allowed_statuses": ["pending", "in_review", "accepted", "rejected"],
            "state_persisted": False,
            "acceptance_does_not_auto_persist_business_rows": True,
        },
        "persistence_eligible": False,
    }


def _universe_coverage_descriptor(measurement: Mapping[str, Any]) -> dict[str, Any]:
    """Never emit a rate unless a structured universe manifest was accepted."""
    covered = max(0, int(measurement.get("covered") or 0))
    denominator = measurement.get("denominator")
    if measurement.get("status") != "measured" or measurement.get("manifest_status") != "accepted":
        return {
            "covered": covered,
            "denominator": None,
            "status": "unavailable",
            "reason": str(measurement.get("reason") or "known_universe_denominator_unavailable"),
            "manifest_status": str(measurement.get("manifest_status") or "unavailable"),
            "rate_available": False,
        }
    return {
        "covered": covered,
        "denominator": int(denominator),
        "rate": measurement.get("rate"),
        "status": "measured",
        "reason": "",
        "manifest_status": "accepted",
        "manifest": measurement.get("manifest"),
        "rate_available": measurement.get("rate") is not None,
    }


def _queue_envelope(
    *,
    tasks: list[dict[str, Any]],
    as_of: datetime,
    scope: str,
    evidence_gaps: dict[str, Any],
    universe_coverage: dict[str, Any],
) -> dict[str, Any]:
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    raw_ordered = sorted(
        tasks,
        key=lambda item: (
            severity_order.get(str(item.get("severity")), 9),
            str(item.get("scope")),
            str(item.get("entity_type")),
            str(item.get("entity_id")),
            str(item.get("field")),
            str(item.get("task_id")),
        ),
    )
    # Several locations may intentionally share one organization-level task.
    # Collapse those exact dedupe keys instead of multiplying manual work.
    by_dedupe_key: dict[str, dict[str, Any]] = {}
    duplicate_occurrences_collapsed = 0
    for item in raw_ordered:
        key = str(item["dedupe_key"])
        if key in by_dedupe_key:
            duplicate_occurrences_collapsed += 1
            continue
        by_dedupe_key[key] = item
    ordered = list(by_dedupe_key.values())
    task_ids = [str(item["task_id"]) for item in ordered]
    dedupe_keys = [str(item["dedupe_key"]) for item in ordered]
    if len(task_ids) != len(set(task_ids)) or len(dedupe_keys) != len(set(dedupe_keys)):
        raise ValueError("remediation task identity collision")
    by_severity = Counter(str(item["severity"]) for item in ordered)
    by_scope = Counter(str(item["scope"]) for item in ordered)
    by_entity_type = Counter(str(item["entity_type"]) for item in ordered)
    return {
        "queue": {
            "id": REMEDIATION_QUEUE_ID,
            "version": REMEDIATION_QUEUE_VERSION,
            "scope": scope,
            "generated_at": as_of.isoformat(),
            "read_only": True,
            "preview_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
        },
        "status": "action_required" if ordered else "clear",
        "claim_status": "descriptive_only",
        "task_count": len(ordered),
        "task_counts": {
            "blocking_import": sum(1 for item in ordered if item["blocks_import"]),
            "nonblocking_quality": sum(1 for item in ordered if not item["blocks_import"]),
            "duplicate_occurrences_collapsed": duplicate_occurrences_collapsed,
            "by_severity": dict(sorted(by_severity.items())),
            "by_scope": dict(sorted(by_scope.items())),
            "by_entity_type": dict(sorted(by_entity_type.items())),
        },
        "evidence_gaps": evidence_gaps,
        "universe_coverage": universe_coverage,
        "persistence_policy": {
            "queue_tasks_are_business_rows": False,
            "queue_preview_can_write": False,
            "manual_review_state_is_persisted": False,
            "accepted_task_auto_imports_catalog": False,
            "unreviewed_catalog_import_allowed": False,
            "import_requires_separate_quality_gate": True,
        },
        "ordinary_crud_scope": {
            "changed_by_this_queue": False,
            "dealer_manual_crud": "existing staff-authorized CRUD; outside remediation preview and not evidence-qualified by it",
            "event_decision_crud": "existing organization-scoped opportunity decision workflow; outside remediation preview",
            "event_promotion": "existing verified scheduled opportunity workflow; outside remediation preview",
            "batch_imports": "quality-gated separately; queue acceptance never bypasses import validation",
        },
        "tasks": ordered,
    }


def query_remediation_queue(
    queue: Mapping[str, Any],
    *,
    scope: str | None = None,
    entity_type: str | None = None,
    field: str | None = None,
    issue_code: str | None = None,
    severity: str | None = None,
    freshness_status: str | None = None,
    due_status: str | None = None,
    blocks_import: bool | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Return one bounded task view without changing global queue truth.

    An empty filtered page never rewrites the source queue's ``status`` or
    ``task_count``.  This prevents a narrow Dealer-activity view from being
    mistaken for evidence that the complete remediation queue is clear.
    """
    if not isinstance(queue, Mapping):
        raise ValueError("remediation queue must be an object")
    raw_tasks = queue.get("tasks")
    if not isinstance(raw_tasks, list) or not all(isinstance(item, Mapping) for item in raw_tasks):
        raise ValueError("remediation queue tasks must be an object array")
    declared_count = queue.get("task_count")
    if (
        not isinstance(declared_count, int)
        or isinstance(declared_count, bool)
        or declared_count != len(raw_tasks)
    ):
        raise ValueError("remediation queue task_count must match the complete task array")
    global_status = str(queue.get("status") or "").strip().lower()
    expected_status = "action_required" if declared_count else "clear"
    if global_status != expected_status:
        raise ValueError("remediation queue status must agree with the complete task count")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("limit must be an integer in [1, 500]")
    if blocks_import is not None and not isinstance(blocks_import, bool):
        raise ValueError("blocks_import must be boolean when provided")

    def normalized(value: str | None) -> str | None:
        clean = str(value or "").strip().casefold()
        return clean or None

    filters = {
        "scope": normalized(scope),
        "entity_type": normalized(entity_type),
        "field": normalized(field),
        "issue_code": normalized(issue_code),
        "severity": normalized(severity),
        "freshness_status": normalized(freshness_status),
        "due_status": normalized(due_status),
        "blocks_import": blocks_import,
    }

    def matches(task: Mapping[str, Any]) -> bool:
        freshness = task.get("freshness")
        freshness = freshness if isinstance(freshness, Mapping) else {}
        values = {
            "scope": normalized(str(task.get("scope") or "")),
            "entity_type": normalized(str(task.get("entity_type") or "")),
            "field": normalized(str(task.get("field") or "")),
            "issue_code": normalized(str(task.get("issue_code") or "")),
            "severity": normalized(str(task.get("severity") or "")),
            "freshness_status": normalized(str(freshness.get("status") or "")),
            "due_status": normalized(str(freshness.get("due_status") or "")),
        }
        for key, expected in filters.items():
            if key == "blocks_import":
                if expected is not None and task.get("blocks_import") is not expected:
                    return False
            elif expected is not None and values[key] != expected:
                return False
        return True

    filtered = [item for item in raw_tasks if matches(item)]
    page = filtered[offset : offset + limit]
    filter_active = any(value is not None for value in filters.values())
    global_queue_clear = declared_count == 0 and not filter_active
    result = deepcopy(dict(queue))
    result["tasks"] = deepcopy(page)
    result["task_count_total"] = declared_count
    result["task_count_filtered"] = len(filtered)
    result["task_count_returned"] = len(page)
    result["unfiltered_total"] = declared_count
    result["filtered_total"] = len(filtered)
    result["returned"] = len(page)
    result["has_more"] = offset + len(page) < len(filtered)
    result["task_view"] = {
        "status": (
            "page_returned"
            if page
            else "global_queue_clear"
            if global_queue_clear
            else "no_filter_matches"
            if not filtered
            else "offset_out_of_range"
        ),
        "filters": {key: value for key, value in filters.items() if value is not None},
        "offset": offset,
        "limit": limit,
        "has_more": result["has_more"],
        "next_offset": offset + len(page) if offset + len(page) < len(filtered) else None,
        "global_queue_status": global_status,
        "empty_page_means_queue_clear": global_queue_clear,
        "global_task_count_preserved": True,
        "read_only": True,
    }
    return result


def _append_unmapped_issue_tasks(
    tasks: list[dict[str, Any]],
    report: dict[str, Any],
    *,
    handled_codes: set[str],
    scope: str,
    as_of: datetime,
    stale_after_days: int,
) -> None:
    """Preserve fail-closed coverage when a quality contract gains new rules.

    Known row-level gaps receive richer tasks in the dedicated builders.  Any
    remaining issue still becomes a stable contract-remediation item instead of
    disappearing from the executable queue.
    """
    for issue in report.get("issues", []):
        code = str(issue.get("code") or f"{scope}.quality_issue")
        if code in handled_codes:
            continue
        path = str(issue.get("path") or "quality_contract")
        message = str(issue.get("message") or "quality contract issue requires review")
        material = f"{code}|{path}|{message}"
        entity_id = f"{scope}_contract_{hashlib.sha256(material.encode()).hexdigest()[:16]}"
        severity = "high" if str(issue.get("severity")) == "error" else "medium"
        tasks.append(
            _task(
                scope=scope,
                entity_type=f"{scope}_quality_contract",
                entity_id=entity_id,
                source_id="",
                field=path,
                issue_code=code,
                severity=severity,
                required_fields=["corrected_value", "source_url", "checked_at", "reviewer"],
                acceptance_rule=message,
                source_url=None,
                checked_at=None,
                as_of=as_of,
                stale_after_days=stale_after_days,
                blocks_import=severity == "high",
            )
        )
