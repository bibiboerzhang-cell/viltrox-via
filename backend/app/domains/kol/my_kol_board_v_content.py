"""Pure V-content projection over bounded evidence plus full final-v1 cache rows."""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.core.video_analysis_contract import FINAL_V1_DERIVE_METHOD
from app.domains.analysis import cache_reuse
from app.domains.kol.video_evidence_projection import merge_modalities

VILTROX_TITLE_TOKENS: tuple[str, ...] = ("viltrox", "唯卓仕", "唯卓")
V_CONTENT_TIERS: tuple[str, ...] = (
    "cooperation", "analysis_confirmed", "title_mention", "not_related", "undetermined",
)
REUSE_REASONS_MAX = 8


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, "", b""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _explicit_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return True if text == "true" else False if text == "false" else None


def _positive_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item not in (None, "")]


def classify_v_content(
    project_id: Any,
    title_text: Any,
    *,
    final_v1_brand_status: Any = None,
    final_v1_detected: Any = None,
    final_v1_products: Any = None,
) -> str:
    """Five mutually exclusive tiers; callers must only pass canonical LLM signals."""
    pid = str(project_id).strip() if project_id is not None else ""
    if pid and pid != "0":
        return "cooperation"
    brand_status = str(final_v1_brand_status or "").strip().lower()
    if brand_status not in {"present", "absent", "unknown"}:
        brand_status = ""
    detected = _explicit_bool(final_v1_detected)
    if brand_status == "present":
        return "analysis_confirmed"
    if not brand_status and (detected is True or bool(_positive_list(final_v1_products))):
        return "analysis_confirmed"
    title = str(title_text or "").lower()
    if any(token in title for token in VILTROX_TITLE_TOKENS):
        return "title_mention"
    if brand_status == "absent":
        return "not_related"
    if brand_status == "unknown":
        return "undetermined"
    return "not_related" if detected is False else "undetermined"


def _cache_row(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("cache_id"),
        "target_type": record.get("cache_target_type"),
        "target_id": record.get("cache_target_id"),
        "derive_method": record.get("cache_derive_method"),
        "model": record.get("cache_model"),
        "prompt_version": record.get("cache_prompt_version"),
        "status": record.get("cache_status"),
        "result": record.get("cache_result"),
        "updated_at": record.get("cache_updated_at"),
    }


def _canonical_signals(result_value: Any) -> dict[str, Any]:
    result = _json_object(result_value)
    raw = _mapping(result.get("raw_gemini_video"))
    layer1 = _mapping(_mapping(raw.get("video_analysis_final_v1")).get("layer1_visual_content"))
    blocks = (_mapping(raw.get("brand_product_evidence")), _mapping(layer1.get("brand_product_evidence")))
    statuses = [str(block.get("viltrox_status") or "").strip().lower() for block in blocks]
    brand_status = next((status for status in statuses if status in {"present", "absent", "unknown"}), "")
    modality_values = [block.get("viltrox_evidence") for block, status in zip(blocks, statuses) if status == "present"]
    return {
        "brand_status": brand_status,
        "detected": _explicit_bool(raw.get("viltrox_detected")),
        "products": _positive_list(raw.get("viltrox_products_all")),
        "competitors": _positive_list(raw.get("competitor_mentions")),
        "modalities": merge_modalities(*modality_values),
    }


def project_v_content_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one evidence row; legacy cache content never contributes an LLM claim."""
    evidence_id = int(record.get("evidence_id") or 0)
    raw_cache = record.get("cache_id") not in (None, "")
    decision: dict[str, Any] = {}
    if raw_cache:
        decision = cache_reuse.canonical_final_v1_cache_reuse(
            _cache_row(record),
            target_type="video",
            target_id=str(evidence_id),
            derive_method=FINAL_V1_DERIVE_METHOD,
        )
    canonical = bool(decision.get("reusable"))
    legacy = raw_cache and not canonical
    signals = _canonical_signals(record.get("cache_result")) if canonical else {
        "brand_status": "", "detected": None, "products": [], "competitors": [], "modalities": [],
    }
    tier = classify_v_content(
        record.get("project_id"),
        f"{record.get('video_title') or ''} {record.get('title') or ''}",
        final_v1_brand_status=signals["brand_status"],
        final_v1_detected=signals["detected"],
        final_v1_products=signals["products"],
    )
    reasons = [str(reason)[:120] for reason in decision.get("reasons") or []][:REUSE_REASONS_MAX]
    return {
        "v_tier": tier,
        "has_final_v1_cache": canonical,
        "has_final_v1_raw_cache": raw_cache,
        "canonical_ready": canonical,
        "legacy_unverified": legacy,
        "analysis_cache_reuse_status": "canonical" if canonical else "legacy_unverified" if legacy else None,
        "revalidation_required": legacy,
        "claim_status": "descriptive_only",
        "cache_reuse_reasons": reasons,
        "llm_viltrox_status": signals["brand_status"] or None,
        "llm_viltrox_detected": signals["detected"],
        "llm_viltrox_products": signals["products"],
        "llm_competitor_mentions": signals["competitors"],
        "viltrox_modalities": signals["modalities"],
    }


def aggregate_v_content(records: Iterable[Mapping[str, Any]], *, ids_max: int) -> dict[str, Any]:
    """Aggregate already-bounded source rows using the same per-row projector as the wall."""
    projected = [(record, project_v_content_record(record)) for record in records]
    tier_counts = {tier: 0 for tier in V_CONTENT_TIERS}
    tier_kols = {tier: set() for tier in V_CONTENT_TIERS}
    related_kols: set[int] = set()
    for record, item in projected:
        tier = item["v_tier"]
        tier_counts[tier] += 1
        kol_id = int(record.get("kol_pool_id") or 0)
        if kol_id > 0:
            tier_kols[tier].add(kol_id)
            if tier in {"cooperation", "analysis_confirmed", "title_mention"}:
                related_kols.add(kol_id)
    ordered_ids = sorted(related_kols)
    return {
        "total_evidence": len(projected),
        "canonical_ready": sum(1 for _, item in projected if item["canonical_ready"]),
        "legacy_unverified": sum(1 for _, item in projected if item["legacy_unverified"]),
        "tiers": tier_counts,
        "tiers_by_kol": {f"{tier}_kols": len(tier_kols[tier]) for tier in V_CONTENT_TIERS},
        "v_kol_count": len(related_kols),
        "v_kol_ids": ordered_ids[:ids_max],
        "v_kol_ids_truncated": len(ordered_ids) > ids_max,
    }


__all__ = ["VILTROX_TITLE_TOKENS", "aggregate_v_content", "classify_v_content", "project_v_content_record"]
