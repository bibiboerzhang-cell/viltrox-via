"""Pure provenance projections for the video data-watch write boundary."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def link_metadata(
    sku_source: str,
    detected_detail: dict[str, Any] | None,
    detected_source: str,
    *,
    structured_evidence_source: str,
    cached_content_source: str,
    title_alias_source: str,
    confirmation_source: str,
    tracking_source: str,
    structured_confidence: Callable[[dict[str, Any]], float],
) -> tuple[str, str, float]:
    if sku_source == "confirmation":
        return "confirmed", confirmation_source, 1.0
    if sku_source == "auto":
        source = detected_source or (
            structured_evidence_source if detected_detail else title_alias_source
        )
        confidence = (
            structured_confidence(detected_detail)
            if detected_detail
            else 0.55 if source == cached_content_source else 0.6
        )
        return "detected", source, confidence
    return "manual", tracking_source, 1.0


def existing_provenance(
    existing_detail: list[dict[str, Any]],
    *,
    text: Callable[[Any], str],
) -> dict[str, Any]:
    relation_types = {text(item.get("relation_type")) for item in existing_detail}
    detected_pending = any(
        text(item.get("relation_type")) == "detected" for item in existing_detail
    )
    return {
        "relation_type": next(iter(relation_types)) if len(relation_types) == 1 else "mixed",
        "source": text(existing_detail[0].get("source")) if len(existing_detail) == 1 else "existing_link",
        "confidence": existing_detail[0].get("confidence") if len(existing_detail) == 1 else None,
        "requires_human_confirmation": detected_pending,
        "links": existing_detail,
    }


def sku_provenance(
    *,
    sku_source: str,
    link_relation_type: str,
    link_source: str,
    link_confidence: float,
    detected_detail: dict[str, Any] | None,
    confirmed_detection: dict[str, Any] | None,
    existing: dict[str, Any],
    confirmation_source: str,
    int_value: Callable[[Any], int],
    text: Callable[[Any], str],
) -> dict[str, Any]:
    if sku_source == "auto":
        return {
            "relation_type": "detected",
            "source": link_source,
            "confidence": link_confidence,
            "requires_human_confirmation": True,
            **(
                {
                    "cache_id": int_value(detected_detail.get("cache_id")),
                    "modalities": list(detected_detail.get("modalities") or []),
                    "source_fields": list(detected_detail.get("source_fields") or []),
                    "evidence_excerpt": text(detected_detail.get("evidence_excerpt"))[:200],
                    "extractor_version": text(detected_detail.get("extractor_version")),
                }
                if detected_detail
                else {}
            ),
        }
    if sku_source == "confirmation":
        return {
            "relation_type": "confirmed",
            "source": confirmation_source,
            "confidence": 1.0,
            "requires_human_confirmation": False,
            "confirmed_from": {
                "relation_type": "detected",
                "source": text((confirmed_detection or {}).get("source")),
                "confidence": (confirmed_detection or {}).get("confidence"),
            },
        }
    if sku_source == "existing":
        return existing
    return {
        "relation_type": link_relation_type if sku_source == "manual" else "existing",
        "source": link_source if sku_source == "manual" else "existing_link",
        "confidence": link_confidence if sku_source == "manual" else None,
        "requires_human_confirmation": False,
    }
