"""Pure catalog-preview construction for Event Radar.

This module never performs network or database I/O.  It preserves the strict
boundary between bundled descriptive catalog evidence and organization-scoped
verified Event Radar rows.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable


def build_preview(
    data: dict[str, Any],
    *,
    audit_event_catalog: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    """Validate and summarize a bundled reviewed catalog without writing DB."""
    sources = data["sources"]
    opportunities = data["opportunities"]
    source_ids = [str(item.get("id") or "") for item in sources]
    opportunity_ids = [str(item.get("id") or "") for item in opportunities]
    canonical_keys = [str(item.get("canonical_key") or "") for item in opportunities]
    errors: list[str] = []
    if any(not value for value in source_ids):
        errors.append("source id missing")
    if len(source_ids) != len(set(source_ids)):
        errors.append("duplicate source id")
    if any(not value for value in opportunity_ids):
        errors.append("opportunity id missing")
    if len(opportunity_ids) != len(set(opportunity_ids)):
        errors.append("duplicate opportunity id")
    if len(canonical_keys) != len(set(canonical_keys)):
        errors.append("duplicate canonical key")
    source_set = set(source_ids)
    source_by_id = {str(item.get("id") or ""): item for item in sources}
    for item in opportunities:
        source_id = str(item.get("source_id") or "")
        if source_id not in source_set:
            errors.append(f"unknown source for opportunity {item.get('id')}")
        elif str(source_by_id[source_id].get("status") or "active") != "active":
            errors.append(f"non-active source for opportunity {item.get('id')}")
        start = str(item.get("start_date") or "")
        end = str(item.get("end_date") or "")
        if start and end and end < start:
            errors.append(f"end before start for opportunity {item.get('id')}")
        if not str(item.get("official_url") or "").startswith("https://"):
            errors.append(f"official https url missing for opportunity {item.get('id')}")
    quality_contract = audit_event_catalog(data)
    preview_fields = (
        "title", "lane", "organizer", "start_date", "end_date", "timezone",
        "local_time_text", "venue", "city", "region", "country_code",
        "official_url", "registration_url", "event_status", "evidence_grade",
        "confidence", "relevance_score", "relevance_basis",
    )
    preview_items: list[dict[str, Any]] = []
    for item in opportunities:
        source = source_by_id.get(str(item.get("source_id") or ""), {})
        preview_items.append(
            {
                "id": f"catalog-preview:{item.get('id')}",
                "catalog_item_id": item.get("id"),
                **{key: item.get(key) for key in preview_fields},
                "decision_status": "needs_review",
                # A catalog value can be descriptive evidence, but it is never
                # upgraded to a current organization-scoped verification claim.
                "catalog_verification_status": item.get("verification_status"),
                "verification_status": "needs_review",
                "freshness_status": "unverified",
                "source_name": source.get("name"),
                "source_kind": source.get("source_kind"),
                "source_status": source.get("status") or "hold",
                "source_enabled": False,
                "source_requires_human_review": True,
                "source_checked_at": None,
                "viltrox_presence_status": item.get("viltrox_presence_status") or "unknown",
                "preview_only": True,
                "claim_status": "descriptive_only",
                "database_accessed": False,
                "business_rows_written": 0,
            }
        )
    return {
        "ok": not errors,
        "record_only": True,
        "import_allowed": bool(quality_contract["import_gate"]["allowed"]),
        "quality_status": quality_contract["quality_status"],
        "claim_status": quality_contract["claim_status"],
        "catalog_version": data.get("catalog_version"),
        "checked_at": data.get("checked_at"),
        "coverage_claim": data.get("coverage_claim"),
        "global_complete": False,
        "truth_note": data.get("truth_note"),
        "source_count": len(sources),
        "opportunity_count": len(opportunities),
        "source_kinds": dict(Counter(str(item.get("source_kind") or "unknown") for item in sources)),
        "source_statuses": dict(Counter(str(item.get("status") or "active") for item in sources)),
        "countries": sorted({str(item.get("country_code") or "") for item in sources if item.get("country_code")}),
        "lanes": dict(Counter(str(item.get("lane") or "unknown") for item in opportunities)),
        "preview_items": preview_items,
        "preview_item_count": len(preview_items),
        "preview_contract": {
            "read_only": True,
            "network_accessed": False,
            "database_accessed": False,
            "business_rows_written": 0,
            "automatic_promotion": False,
            "claim_status": "descriptive_only",
        },
        "quality_contract": quality_contract,
        "errors": errors,
    }


__all__ = ["build_preview"]
