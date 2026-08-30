"""Provider-free content evidence for targeted local KOL recall.

This module projects already-cached, exact-video prose into private in-memory
records.  The records are consumed by the field-level match gate; raw prose is
never copied into a recall item or persisted in a search session.

Only two evidence families are accepted:

* description/caption/transcript fields from the exact cached platform post;
* generic visual facts from a canonical ``video_analysis_final_v1`` cache.

Brand-fit verdicts, cooperation recommendations, and other downstream model
judgements are deliberately outside this projection.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable

from app.core.video_analysis_contract import FINAL_V1_DERIVE_METHOD
from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.domains.kol.contact_system import sanitize_contact_values_for_external_processing
from app.domains.kol.video_data_watch import (
    _cached_item_matches_video,
    _cached_post_items,
)
from app.domains.kol.video_url_identity import (
    VideoUrlIdentityError,
    parse_supported_video_url,
)


PRIVATE_CONTENT_EVIDENCE_KEY = "_targeted_content_evidence"
PRIVATE_CONTENT_TARGETS_KEY = "_targeted_content_targets"
CONTENT_EVIDENCE_STATUS_KEY = "targeted_content_evidence_status"
MAX_PRIVATE_FIELD_CHARS = 2_000
MAX_PRIVATE_TOTAL_CHARS = 6_000
MAX_PRIVATE_RECORDS = 18


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if value in (None, "", b""):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _text_parts(value: Any, *, depth: int = 0) -> list[str]:
    """Extract prose from bounded cached transcript/caption shapes."""

    if depth > 3 or value in (None, "", b""):
        return []
    if isinstance(value, str):
        text = " ".join(value.split()).strip()
        return [text] if text else []
    if isinstance(value, Mapping):
        output: list[str] = []
        for key in (
            "text",
            "description",
            "caption",
            "transcript",
            "content",
            "segments",
            "items",
            "results",
            "data",
        ):
            if key in value:
                output.extend(_text_parts(value.get(key), depth=depth + 1))
        return output
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        output = []
        for item in list(value)[:100]:
            output.extend(_text_parts(item, depth=depth + 1))
        return output
    return []


def _safe_join(values: Iterable[Any], *, limit: int = MAX_PRIVATE_FIELD_CHARS) -> str:
    parts: list[str] = []
    for value in values:
        for raw in _text_parts(value):
            sanitized = sanitize_contact_values_for_external_processing(raw)
            text = " ".join(str(sanitized or "").split()).strip()
            if text and text not in parts:
                parts.append(text)
    return " ".join(parts)[: max(0, int(limit))]


def cached_item_content_fields(item: Mapping[str, Any]) -> dict[str, str]:
    """Return only cached description/caption/transcript prose by field."""

    snippet = item.get("snippet") if isinstance(item.get("snippet"), Mapping) else {}
    localized = snippet.get("localized") if isinstance(snippet.get("localized"), Mapping) else {}
    fields = {
        "description": _safe_join(
            (
                item.get("description"),
                item.get("desc"),
                snippet.get("description"),
                localized.get("description"),
            )
        ),
        "caption": _safe_join((item.get("caption"), item.get("text"))),
        "transcript": _safe_join(
            (
                item.get("transcript"),
                item.get("transcripts"),
                item.get("subtitles"),
                item.get("captions"),
            )
        ),
    }
    return {field: text for field, text in fields.items() if text}


def cached_content_evidence(
    raw_platform_data: Any,
    targets: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project exact-video cached prose without network, writes, or inference."""

    raw = _mapping(raw_platform_data)
    if not raw:
        return []
    cached_items = _cached_post_items(raw)
    records: list[dict[str, Any]] = []
    used_chars = 0
    seen: set[tuple[int, str, str]] = set()
    for target_row in list(targets)[:5]:
        try:
            target = parse_supported_video_url(target_row.get("content_url"))
        except VideoUrlIdentityError:
            continue
        try:
            evidence_id = int(target_row.get("evidence_id") or 0)
        except (TypeError, ValueError):
            evidence_id = 0
        for collection, item in cached_items:
            if not _cached_item_matches_video(item, collection=collection, target=target):
                continue
            for field, text in cached_item_content_fields(item).items():
                remaining = MAX_PRIVATE_TOTAL_CHARS - used_chars
                if remaining <= 0 or len(records) >= MAX_PRIVATE_RECORDS:
                    return records
                bounded = text[:remaining]
                key = (evidence_id, field, bounded)
                if not bounded or key in seen:
                    continue
                seen.add(key)
                records.append(
                    {
                        "field": field,
                        "text": bounded,
                        "source": f"cached_pool_video.{field}",
                        "evidence_id": evidence_id or None,
                    }
                )
                used_chars += len(bounded)
    return records


def _canonical_result(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("result")
    return _mapping(value)


def canonical_final_v1_content_evidence(
    cache_rows_by_evidence_id: Mapping[int, Mapping[str, Any]],
    targets: Iterable[Mapping[str, Any]],
    *,
    classifier: Callable[..., Mapping[str, Any]] = canonical_final_v1_cache_reuse,
) -> list[dict[str, Any]]:
    """Project generic facts from canonical final-v1 rows only.

    The allowlist intentionally excludes ``brand_exposure``, ``final_verdict``,
    fit scores, and cooperation recommendations.  Those are judgements, not
    qualification facts for a prospective creator search.
    """

    records: list[dict[str, Any]] = []
    used_chars = 0
    for target in list(targets)[:5]:
        try:
            evidence_id = int(target.get("evidence_id") or 0)
        except (TypeError, ValueError):
            evidence_id = 0
        row = cache_rows_by_evidence_id.get(evidence_id)
        if evidence_id <= 0 or not isinstance(row, Mapping):
            continue
        decision = classifier(
            row,
            target_type="video",
            target_id=str(evidence_id),
            derive_method=FINAL_V1_DERIVE_METHOD,
        )
        if decision.get("reusable") is not True:
            continue
        result = _canonical_result(row)
        layer1 = result.get("layer1_visual_content")
        if not isinstance(layer1, Mapping):
            continue
        timeline = layer1.get("scene_timeline")
        timeline_what = []
        if isinstance(timeline, Sequence) and not isinstance(timeline, (str, bytes, bytearray)):
            timeline_what = [scene.get("what") for scene in list(timeline)[:8] if isinstance(scene, Mapping)]
        allowed = (
            ("visual_summary", "content_summary", (layer1.get("content_summary"),)),
            ("visual_summary", "product_presence", (layer1.get("product_presence"),)),
            ("visual_summary", "scene_timeline", tuple(timeline_what)),
        )
        for field, source_field, values in allowed:
            remaining = MAX_PRIVATE_TOTAL_CHARS - used_chars
            if remaining <= 0 or len(records) >= MAX_PRIVATE_RECORDS:
                return records
            text = _safe_join(values, limit=min(MAX_PRIVATE_FIELD_CHARS, remaining))
            if not text:
                continue
            records.append(
                {
                    "field": field,
                    "text": text,
                    "source": f"canonical_final_v1.{source_field}",
                    "evidence_id": evidence_id,
                    "claim_status": "descriptive_only",
                }
            )
            used_chars += len(text)
    return records


def attach_private_content_evidence(
    evidence: Mapping[str, Any] | None,
    *,
    raw_platform_data: Any,
    cache_rows_by_evidence_id: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return an enriched copy containing private proof text and public status."""

    output = dict(evidence or {})
    targets = [
        dict(item)
        for item in output.get(PRIVATE_CONTENT_TARGETS_KEY) or []
        if isinstance(item, Mapping)
    ][:5]
    cached = cached_content_evidence(raw_platform_data, targets)
    canonical = canonical_final_v1_content_evidence(cache_rows_by_evidence_id, targets)
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for record in [*cached, *canonical]:
        key = (
            str(record.get("field") or ""),
            str(record.get("source") or ""),
            int(record.get("evidence_id") or 0),
        )
        if key not in seen:
            seen.add(key)
            records.append(record)
    output[PRIVATE_CONTENT_EVIDENCE_KEY] = records[:MAX_PRIVATE_RECORDS]
    sources = sorted({str(record.get("source") or "") for record in records if record.get("source")})
    fields = sorted({str(record.get("field") or "") for record in records if record.get("field")})
    output[CONTENT_EVIDENCE_STATUS_KEY] = {
        "status": "available" if records else "pending_content_evidence",
        "pending": not bool(records),
        "pending_counts_toward_target": False,
        "source_types": sources,
        "evidence_fields": fields,
        "evidence_record_count": len(records),
        "content_text_returned": False,
        "provider_calls": False,
        "llm_calls": False,
        "claim_status": "descriptive_only",
    }
    return output


__all__ = [
    "CONTENT_EVIDENCE_STATUS_KEY",
    "PRIVATE_CONTENT_EVIDENCE_KEY",
    "PRIVATE_CONTENT_TARGETS_KEY",
    "attach_private_content_evidence",
    "cached_content_evidence",
    "cached_item_content_fields",
    "canonical_final_v1_content_evidence",
]
