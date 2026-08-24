"""Pure source-fence helpers for keyframe-review cache validity."""
from __future__ import annotations

import hashlib
import json
from typing import Any


FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
KEYFRAME_QA_DERIVE_METHOD = "video_analysis_final_v1_keyframe_qa"
_FINAL_LAYERS = (
    "layer1_visual_content",
    "layer2_viewer_emotion",
    "layer3_three_values",
    "layer4_attribution",
    "layer5_recommendations",
    "layer6_flags_and_scores",
)


def json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def final_v1_payload_from_cache_result(value: Any) -> dict[str, Any]:
    """Return the six-layer payload from both current and legacy cache shapes."""
    root = json_object(value)
    direct = root.get(FINAL_V1_DERIVE_METHOD)
    if isinstance(direct, dict) and any(isinstance(direct.get(key), dict) for key in _FINAL_LAYERS):
        return {key: direct.get(key) if isinstance(direct.get(key), dict) else {} for key in _FINAL_LAYERS}
    raw = root.get("raw_gemini_video")
    raw = raw if isinstance(raw, dict) else {}
    nested = raw.get("video_analysis_final_v1")
    if isinstance(nested, dict) and any(isinstance(nested.get(key), dict) for key in _FINAL_LAYERS):
        return {key: nested.get(key) if isinstance(nested.get(key), dict) else {} for key in _FINAL_LAYERS}
    if any(isinstance(root.get(key), dict) for key in _FINAL_LAYERS):
        return {key: root.get(key) if isinstance(root.get(key), dict) else {} for key in _FINAL_LAYERS}
    return {}


def final_v1_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def qa_cache_matches_source(
    result: Any,
    *,
    evidence_id: int,
    source_cache_id: int,
    source_payload_sha256: str,
) -> bool:
    """A review is ready only for the exact video and main-analysis bytes."""
    root = json_object(result)
    raw = root.get("raw_gemini_video") if isinstance(root.get("raw_gemini_video"), dict) else {}
    fence = root.get("final_v1_pass") if isinstance(root.get("final_v1_pass"), dict) else raw.get("final_v1_pass")
    if not isinstance(fence, dict):
        return False
    try:
        fenced_cache_id = int(fence.get("source_cache_id") or 0)
        fenced_evidence_id = int(fence.get("source_target_id") or 0)
    except (TypeError, ValueError):
        return False
    return (
        fenced_evidence_id == int(evidence_id)
        and fenced_cache_id == int(source_cache_id)
        and str(fence.get("source_payload_sha256") or "").lower()
        == str(source_payload_sha256 or "").lower()
    )


def valid_qa_caches(
    source_caches: dict[int, dict[str, Any]], qa_caches: dict[int, dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    valid: dict[int, dict[str, Any]] = {}
    for evidence_id, qa_cache in qa_caches.items():
        source = source_caches.get(evidence_id) or {}
        if str(source.get("status") or "") != "ready":
            continue
        payload = final_v1_payload_from_cache_result(source.get("result"))
        if payload and qa_cache_matches_source(
            qa_cache.get("result"), evidence_id=evidence_id,
            source_cache_id=int(source.get("id") or 0),
            source_payload_sha256=final_v1_payload_sha256(payload),
        ):
            valid[evidence_id] = qa_cache
    return valid


__all__ = [
    "FINAL_V1_DERIVE_METHOD", "KEYFRAME_QA_DERIVE_METHOD",
    "final_v1_payload_from_cache_result", "final_v1_payload_sha256",
    "qa_cache_matches_source", "valid_qa_caches",
]
