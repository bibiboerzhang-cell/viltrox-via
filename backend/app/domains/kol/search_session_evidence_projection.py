"""Privacy-safe evidence coordinates for durable KOL search sessions."""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.profile_recall_match_evidence import (
    CAPABILITY_USE_EVIDENCE_SOURCE,
    CONTROLLED_ALIAS_EVIDENCE_SOURCE,
    REPRESENTATIVE_CONTENT_EVIDENCE_FIELDS,
)
from app.domains.kol.search_sessions_serde import _list, _text
from app.domains.kol.targeted_search_contract import project_locked_term_groups


_MATCH_EVIDENCE_FIELDS = frozenset({
    "handle", "display_name", "bio", "primary_topic", "content_style",
    "secondary_topics_json", "profile_text", "type_reason",
    *(f"representative_evidence.{field}" for field in REPRESENTATIVE_CONTENT_EVIDENCE_FIELDS),
})
_MATCH_EVIDENCE_SOURCES = frozenset({
    "server_profile_evidence",
    "cached_pool_video.description",
    "cached_pool_video.caption",
    "cached_pool_video.transcript",
    "canonical_final_v1.content_summary",
    "canonical_final_v1.product_presence",
    "canonical_final_v1.scene_timeline",
    CONTROLLED_ALIAS_EVIDENCE_SOURCE,
    CAPABILITY_USE_EVIDENCE_SOURCE,
})
_ROLE_IDENTITY_EVIDENCE_FIELDS = frozenset({
    "handle", "display_name", "bio",
})


def _looks_like_contact_value(value: str) -> bool:
    text = str(value or "").strip()
    phone_like = re.search(r"(?<!\w)(?:\+?\d[\d().\s-]{5,}\d)(?!\w)", text)
    return bool(
        re.search(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}", text, flags=re.IGNORECASE)
        or re.search(r"(?:https?://|www\.)", text, flags=re.IGNORECASE)
        or (phone_like and len(re.sub(r"\D", "", phone_like.group(0))) >= 7)
    )


def _controlled_evidence_allowed(raw: dict[str, Any], controlled_specs: list[dict[str, Any]]) -> bool:
    source = _text(raw.get("source"))
    canonical = _text(raw.get("canonical_term")).lower()[:120]
    observed = _text(raw.get("observed_term")).lower()[:120]
    evidence_group = _text(raw.get("evidence_group"))
    expected_kind = {
        "product_use_fit": "product",
        "segment_use_case": "scene",
        "people_role": "role",
    }.get(evidence_group, "")
    if not canonical or not observed or not expected_kind:
        return False
    if expected_kind == "role" and _text(raw.get("field")) not in _ROLE_IDENTITY_EVIDENCE_FIELDS:
        return False
    for value in controlled_specs:
        spec = project_locked_term_groups(value)
        if not spec:
            continue
        for group in spec.get("groups") or []:
            if not isinstance(group, dict):
                continue
            if _text(group.get("kind")) != expected_kind:
                continue
            if _text(group.get("canonical_term")).lower() != canonical:
                continue
            allowed = (
                group.get("use_suitability_terms")
                if source == CAPABILITY_USE_EVIDENCE_SOURCE
                else group.get("aliases")
            )
            if observed in {_text(term).lower() for term in _list(allowed)}:
                return True
    return False


def _safe_match_evidence(
    value: Any,
    *,
    allowed_terms: set[str],
    controlled_specs: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for raw in _list(value)[:12]:
        if not isinstance(raw, dict):
            continue
        field = _text(raw.get("field"))[:48]
        term = _text(raw.get("term")).lower()[:80]
        source = _text(raw.get("source"))
        controlled = source in {CONTROLLED_ALIAS_EVIDENCE_SOURCE, CAPABILITY_USE_EVIDENCE_SOURCE}
        if not (
            field in _MATCH_EVIDENCE_FIELDS
            and (term in allowed_terms or (
                controlled and _controlled_evidence_allowed(raw, list(controlled_specs or []))
            ))
            and not _looks_like_contact_value(term)
            and source in _MATCH_EVIDENCE_SOURCES
        ):
            continue
        item = {"field": field, "term": term, "source": source}
        if controlled:
            canonical = _text(raw.get("canonical_term")).lower()[:120]
            observed = _text(raw.get("observed_term")).lower()[:120]
            if _looks_like_contact_value(canonical) or _looks_like_contact_value(observed):
                continue
            item.update({
                "canonical_term": canonical,
                "observed_term": observed,
                "evidence_group": _text(raw.get("evidence_group"))[:80],
                "evidence_relation": _text(raw.get("evidence_relation"))[:80],
            })
        output.append(item)
    return output


__all__ = ["_looks_like_contact_value", "_safe_match_evidence"]
