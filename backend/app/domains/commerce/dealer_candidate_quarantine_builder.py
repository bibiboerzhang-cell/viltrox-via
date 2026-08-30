"""Effect adapter and deterministic projection for Dealer quarantine artifacts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class QuarantineRuntime:
    eligible_sources: Callable[
        [Mapping[str, Any]],
        tuple[list[dict[str, Any]], list[dict[str, Any]]],
    ]
    extract_candidates: Callable[..., tuple[list[dict[str, Any]], list[str]]]
    publisher_host_bound: Callable[[str, str], bool]
    canonical_hash: Callable[[Any], str]
    contract_id: str
    contract_version: int
    claim_status: str
    max_capture_bytes: int


def _source_projection(
    preflight_row: Mapping[str, Any],
    registry_row: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        **dict(registry_row),
        "source_registry_id": str(preflight_row.get("source_registry_id") or ""),
        "publisher": preflight_row.get("publisher") or registry_row.get("publisher"),
        "source_kind": preflight_row.get("source_kind") or registry_row.get("source_kind"),
    }


def _capture_failure(
    source: Mapping[str, Any],
    *,
    canonical_url: str,
    error: Exception,
) -> dict[str, Any]:
    return {
        "source_registry_id": source.get("source_registry_id"),
        "publisher": source.get("publisher"),
        "source_kind": source.get("source_kind"),
        "canonical_url": canonical_url,
        "status": "capture_failed",
        "error": f"{error.__class__.__name__}: {str(error)[:240]}",
        "candidate_count": 0,
        "page_contains_public_physical_store_data": False,
        "legal_approval": False,
        "source_activation": False,
        "business_rows_written": 0,
    }


def _preflight_gate(preflight_row: Mapping[str, Any]) -> dict[str, Any]:
    robots = (
        preflight_row.get("robots")
        if isinstance(preflight_row.get("robots"), Mapping)
        else {}
    )
    return {
        "technical_status": preflight_row.get("technical_status"),
        "robots_status": robots.get("status"),
        "robots_fetch_allowed": robots.get("fetch_allowed") is True,
        "robots_reason": robots.get("reason"),
        "robots_sha256": robots.get("sha256"),
        "terms_legal_approval": False,
    }


def _capture_status(candidates: list[dict[str, Any]], issues: list[str]) -> str:
    if candidates:
        return "quarantined_candidates_extracted"
    if not issues:
        return "no_complete_public_us_address_detected"
    return "capture_not_extractable"


def _capture_source(
    *,
    preflight_row: Mapping[str, Any],
    source: Mapping[str, Any],
    captured_at: str,
    fetch: Callable[[str], Mapping[str, Any]],
    runtime: QuarantineRuntime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    canonical_url = str(preflight_row.get("canonical_url") or "")
    try:
        response = dict(fetch(canonical_url))
    except Exception as exc:
        return _capture_failure(source, canonical_url=canonical_url, error=exc), []
    status_code = int(response.get("status_code") or 0)
    final_url = str(response.get("final_url") or canonical_url)
    content_type = str(response.get("content_type") or "")
    content = bytes(response.get("content") or b"")
    bounded = content[: runtime.max_capture_bytes]
    capture_sha256 = hashlib.sha256(bounded).hexdigest()
    preflight_snapshot = (
        preflight_row.get("snapshot")
        if isinstance(preflight_row.get("snapshot"), Mapping)
        else {}
    )
    issues: list[str] = []
    if not (200 <= status_code < 400):
        issues.append("capture_http_status_not_successful")
    if not runtime.publisher_host_bound(canonical_url, final_url):
        issues.append("final_url_not_publisher_host_bound")
    candidates: list[dict[str, Any]] = []
    if not issues:
        candidates, extraction_issues = runtime.extract_candidates(
            source=source,
            content=bounded,
            content_type=content_type,
            captured_at=captured_at,
            final_url=final_url,
        )
        issues.extend(extraction_issues)
    return (
        {
            "source_registry_id": source.get("source_registry_id"),
            "publisher": source.get("publisher"),
            "source_kind": source.get("source_kind"),
            "canonical_url": canonical_url,
            "preflight_gate": _preflight_gate(preflight_row),
            "status": _capture_status(candidates, issues),
            "candidate_count": len(candidates),
            "page_contains_public_physical_store_data": bool(candidates),
            "snapshot": {
                "captured_at": captured_at,
                "http_status": status_code,
                "final_url": final_url,
                "content_type": content_type.split(";", 1)[0].strip().casefold() or None,
                "response_bytes": len(content),
                "captured_bytes": len(bounded),
                "truncated": len(content) > runtime.max_capture_bytes,
                "sha256": capture_sha256,
                "hash_scope": (
                    "prefix"
                    if len(content) > runtime.max_capture_bytes
                    else "complete_response"
                ),
                "preflight_sha256": preflight_snapshot.get("sha256"),
                "preflight_hash_match": capture_sha256 == preflight_snapshot.get("sha256"),
            },
            "issues": sorted(set(issues)),
            "candidates": candidates,
            "legal_approval": False,
            "source_activation": False,
            "business_rows_written": 0,
            "claim_status": runtime.claim_status,
        },
        candidates,
    )


def _duplicate_groups(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    groups: dict[str, list[str]] = {}
    for candidate in candidates:
        groups.setdefault(str(candidate["cross_source_dedupe_key"]), []).append(
            str(candidate["source_entity_key"])
        )
    duplicate_groups = [
        {
            "cross_source_dedupe_key": key,
            "source_entity_keys": values,
            "count": len(values),
        }
        for key, values in sorted(groups.items())
        if len(values) > 1
    ]
    return groups, duplicate_groups


def _near_duplicate_groups(
    candidates: list[dict[str, Any]],
    canonical_hash: Callable[[Any], str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for candidate in candidates:
        address = candidate["address"]
        house_match = re.match(r"^([0-9]{1,6})\b", str(address.get("line1") or ""))
        if not house_match:
            continue
        material = {
            "house_number": house_match.group(1),
            "city": re.sub(
                r"[^a-z0-9]",
                "",
                str(address.get("city") or "").casefold(),
            ),
            "state": str(address.get("state") or ""),
            "postal_code": str(address.get("postal_code") or "")[:5],
        }
        key = "us_address_review." + canonical_hash(material)[:24]
        groups.setdefault(key, []).append(
            {
                "source_entity_key": str(candidate["source_entity_key"]),
                "formatted_address": str(address.get("formatted") or ""),
            }
        )
    return [
        {"review_key": key, "count": len(values), "candidates": values}
        for key, values in sorted(groups.items())
        if len(values) > 1
        and len({item["formatted_address"] for item in values}) > 1
    ]


def _coverage_counts(
    candidates: list[dict[str, Any]],
    source_results: list[dict[str, Any]],
) -> dict[str, Any]:
    state_codes = sorted(
        {str(candidate["address"]["state"]) for candidate in candidates}
    )
    counts = {
        "phone": sum(bool(row["contact"].get("phone")) for row in candidates),
        "email": sum(bool(row["contact"].get("email")) for row in candidates),
        "website": sum(bool(row["contact"].get("website")) for row in candidates),
        "contact": sum(
            bool(row["contact"].get("phone") or row["contact"].get("email"))
            for row in candidates
        ),
        "coordinate": sum(
            row["map_fields"].get("latitude") is not None for row in candidates
        ),
        "manufacturer_scope": sum(
            bool(row["truth_dimensions"].get("manufacturer_authorization_scope"))
            for row in candidates
        ),
        "hash_match": sum(
            row.get("snapshot", {}).get("preflight_hash_match") is True
            for row in source_results
            if isinstance(row.get("snapshot"), Mapping)
        ),
    }
    return {"state_codes": state_codes, **counts}


def _summary(
    *,
    registry_by_id: dict[str, Any],
    preflight: Mapping[str, Any],
    eligible: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    called_ids: list[str],
    candidate_source_ids: list[str],
    candidates: list[dict[str, Any]],
    groups: dict[str, list[str]],
    duplicate_groups: list[dict[str, Any]],
    near_duplicates: list[dict[str, Any]],
    blocked_source_calls: list[str],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    candidate_count = len(candidates)

    def coverage_rate(count: int) -> float:
        return round(count / candidate_count, 6) if candidate_count else 0.0

    return {
        "registered_source_count": len(registry_by_id),
        "preflight_source_count": len(preflight.get("sources") or []),
        "eligible_source_count": len(eligible),
        "excluded_source_count": len(excluded),
        "fetched_source_count": len(called_ids),
        "sources_with_candidates": len(candidate_source_ids),
        "source_candidate_coverage_rate": (
            round(len(candidate_source_ids) / len(eligible), 6) if eligible else 0.0
        ),
        "candidate_count": candidate_count,
        "entity_candidate_count": candidate_count,
        "complete_address_count": candidate_count,
        "unique_address_count": len(groups),
        "cross_source_duplicate_group_count": len(duplicate_groups),
        "possible_near_duplicate_group_count": len(near_duplicates),
        "state_coverage_count": len(coverage["state_codes"]),
        "state_codes": coverage["state_codes"],
        "phone_coverage_count": coverage["phone"],
        "phone_coverage_rate": coverage_rate(coverage["phone"]),
        "email_coverage_count": coverage["email"],
        "email_coverage_rate": coverage_rate(coverage["email"]),
        "website_coverage_count": coverage["website"],
        "website_coverage_rate": coverage_rate(coverage["website"]),
        "phone_or_email_coverage_count": coverage["contact"],
        "phone_or_email_coverage_rate": coverage_rate(coverage["contact"]),
        "publisher_coordinate_count": coverage["coordinate"],
        "publisher_coordinate_coverage_rate": coverage_rate(coverage["coordinate"]),
        "manufacturer_authorization_scope_field_count": coverage["manufacturer_scope"],
        "manufacturer_authorization_scope_field_rate": coverage_rate(
            coverage["manufacturer_scope"]
        ),
        "viltrox_authorization_evidence_count": 0,
        "viltrox_product_presence_evidence_count": 0,
        "preflight_snapshot_hash_match_count": coverage["hash_match"],
        "blocked_source_call_count": len(blocked_source_calls),
        "legal_approval_count": 0,
        "source_activation_count": 0,
        "business_rows_written": 0,
    }


def build_quarantine(
    *,
    preflight: Mapping[str, Any],
    registry: Mapping[str, Any],
    captured_at: str,
    fetch: Callable[[str], Mapping[str, Any]],
    preflight_sha256: str,
    registry_sha256: str,
    runtime: QuarantineRuntime,
) -> dict[str, Any]:
    eligible, excluded = runtime.eligible_sources(preflight)
    registry_by_id = {
        str(row.get("id") or ""): row
        for row in registry.get("dealer_discovery_sources") or []
        if isinstance(row, Mapping)
    }
    source_results: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    called_ids: list[str] = []
    for preflight_row in eligible:
        source_id = str(preflight_row.get("source_registry_id") or "")
        source = _source_projection(
            preflight_row,
            registry_by_id.get(source_id) or {},
        )
        called_ids.append(source_id)
        result, candidates = _capture_source(
            preflight_row=preflight_row,
            source=source,
            captured_at=captured_at,
            fetch=fetch,
            runtime=runtime,
        )
        source_results.append(result)
        all_candidates.extend(candidates)
    source_results.sort(key=lambda item: str(item.get("source_registry_id") or ""))
    all_candidates.sort(
        key=lambda item: (
            str(item.get("cross_source_dedupe_key")),
            str(item.get("source_entity_key")),
        )
    )
    groups, duplicate_groups = _duplicate_groups(all_candidates)
    near_duplicates = _near_duplicate_groups(
        all_candidates,
        runtime.canonical_hash,
    )
    candidate_source_ids = sorted(
        {
            str(row["source_registry_id"])
            for row in source_results
            if int(row.get("candidate_count") or 0) > 0
        }
    )
    blocked_ids = {
        str(row.get("source_registry_id") or "")
        for row in excluded
        if "robots_path_not_allowed" in set(row.get("reasons") or [])
    }
    blocked_source_calls = sorted(blocked_ids & set(called_ids))
    coverage = _coverage_counts(all_candidates, source_results)
    payload = {
        "contract": {
            "id": runtime.contract_id,
            "version": runtime.contract_version,
            "read_only": True,
            "technical_quarantine_only": True,
            "database_accessed": False,
            "candidate_rows_written": 0,
            "business_rows_written": 0,
            "direct_import_available": False,
            "geocoding_performed": False,
            "legal_approval": False,
            "source_activation": False,
        },
        "generated_at": captured_at,
        "registry_version": registry.get("registry_version"),
        "input_provenance": {
            "technical_preflight_sha256": preflight_sha256,
            "source_registry_sha256": registry_sha256,
        },
        "summary": _summary(
            registry_by_id=registry_by_id,
            preflight=preflight,
            eligible=eligible,
            excluded=excluded,
            called_ids=called_ids,
            candidate_source_ids=candidate_source_ids,
            candidates=all_candidates,
            groups=groups,
            duplicate_groups=duplicate_groups,
            near_duplicates=near_duplicates,
            blocked_source_calls=blocked_source_calls,
            coverage=coverage,
        ),
        "called_source_ids": called_ids,
        "blocked_source_calls": blocked_source_calls,
        "excluded_sources": excluded,
        "candidate_source_ids": candidate_source_ids,
        "cross_source_duplicate_groups": duplicate_groups,
        "possible_near_duplicate_groups": near_duplicates,
        "sources": source_results,
        "claim_status": runtime.claim_status,
        "truth_note": (
            "Rows are read-only, evidence-bound quarantine candidates. They do not prove "
            "Viltrox authorization, Viltrox product presence, inventory, local impact, legal "
            "approval, source activation, or a Dealer business-table write."
        ),
    }
    payload["artifact_content_sha256"] = runtime.canonical_hash(payload)
    return payload
