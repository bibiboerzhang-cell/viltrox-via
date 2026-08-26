"""Privacy projection for the Smart-local qualification boundary.

Raw crawler payloads and contact details never leave the server.  These
helpers strip them and whitelist the handful of evidence fields the operator
UI may render; this module is the only place that decision lives.
"""
from __future__ import annotations
import re
from typing import Any

from app.domains.kol.profile_recall_match_evidence import why_fit_from_match_evidence


_SMART_LOCAL_PRIVATE_ITEM_FIELDS = {
    "bio",
    "profile_text",
    "raw",
    "raw_data",
    "raw_platform_data",
    "email",
    "business_email",
    "contact_email",
    "phone",
    "phone_number",
    "other_contacts_json",
    "contact",
    "contacts",
    "contact_channels",
    "contact_details",
    "contact_methods",
    "wechat",
    "whatsapp",
    "telegram",
    "line",
}
_SMART_LOCAL_FACET_FIELDS = {
    "platform",
    "country",
    "language",
    "profile_type",
    "contact_available",
    "video_evidence",
}
_SMART_LOCAL_EVIDENCE_FIELDS = {
    "handle",
    "display_name",
    "bio",
    "primary_topic",
    "content_style",
    "secondary_topics_json",
    "profile_text",
    "type_reason",
    "representative_evidence.title",
}
_SMART_LOCAL_EVIDENCE_SOURCES = {"server_profile_evidence"}
_CONTACT_TERM_RE = re.compile(r"@|(?:^|\D)\+?\d(?:[\s().-]*\d){6,}(?:\D|$)")


def _private_smart_local_field(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    return bool(
        normalized in _SMART_LOCAL_PRIVATE_ITEM_FIELDS
        or normalized.startswith("raw_")
        or normalized.endswith("_email")
        or normalized.endswith("_phone")
        or ("contact" in normalized and normalized != "contact_available")
    )


def _strip_private_smart_local_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_private_smart_local_values(nested)
            for key, nested in value.items()
            if not _private_smart_local_field(key)
        }
    if isinstance(value, list):
        return [_strip_private_smart_local_values(item) for item in value]
    return value


def _project_match_evidence(value: Any) -> list[dict[str, str]]:
    projected: list[dict[str, str]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        field = str(raw.get("field") or "").strip()
        term = str(raw.get("term") or "").strip()
        source = str(raw.get("source") or "").strip()
        if (
            field not in _SMART_LOCAL_EVIDENCE_FIELDS
            or not term
            or _CONTACT_TERM_RE.search(term)
            or (source and source not in _SMART_LOCAL_EVIDENCE_SOURCES)
        ):
            continue
        evidence = {"field": field, "term": term}
        if source:
            evidence["source"] = source
        projected.append(evidence)
    return projected[:12]


def _project_gate_evidence(value: Any) -> dict[str, Any]:
    gate = _strip_private_smart_local_values(value) if isinstance(value, dict) else {}
    relevance = gate.get("relevance") if isinstance(gate.get("relevance"), dict) else None
    if relevance is not None:
        safe_evidence = _project_match_evidence(relevance.get("evidence"))
        gate["relevance"] = {
            **relevance,
            "passed": bool(safe_evidence),
            "evidence": safe_evidence,
        }
    return gate


def _project_smart_local_item(value: Any) -> dict[str, Any]:
    item = _strip_private_smart_local_values(value) if isinstance(value, dict) else {}
    match_evidence = _project_match_evidence(item.get("match_evidence"))
    item["match_evidence"] = match_evidence
    item["why_fit"] = why_fit_from_match_evidence(match_evidence)
    facets = item.get("candidate_facets") if isinstance(item.get("candidate_facets"), dict) else {}
    item["candidate_facets"] = {
        key: str(facets.get(key) or "unknown")
        for key in _SMART_LOCAL_FACET_FIELDS
    }
    if isinstance(item.get("qualification_evidence"), dict):
        item["qualification_evidence"] = _project_gate_evidence(item["qualification_evidence"])
    return item
