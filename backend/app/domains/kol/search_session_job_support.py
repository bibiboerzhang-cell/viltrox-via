"""Dependency-leaf value helpers for search-session job reconciliation.

The worker compatibility modules import these helpers from the KOL domain so
terminal replay can run without a domain -> worker dependency.  The functions
intentionally preserve the historical worker coercion and JSON semantics.
"""
from __future__ import annotations

import json
from typing import Any


LINEAGE_STAGE_ROLES = ("resolver", "video", "comments", "audience")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(raw: Any, default: Any) -> Any:
    if raw in (None, "", b""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return default
    return parsed if parsed is not None else default


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def item_profile_state(item_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **as_dict(item_payload.get("profile_flow")),
        **as_dict(item_payload.get("profile_execute")),
    }


def compact_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def score_value(value: Any) -> float | None:
    raw = value.get("score") if isinstance(value, dict) else value
    if raw in (None, ""):
        return None
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 100:
        return 100.0
    return round(parsed, 3)


def score_confidence(value: Any) -> float | None:
    if not isinstance(value, dict) or value.get("confidence") in (None, ""):
        return None
    try:
        parsed = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return round(parsed, 5)


def final_v1_payload(result: Any) -> dict[str, Any]:
    root = as_dict(result)
    nested = as_dict(root.get("video_analysis_final_v1"))
    if as_dict(nested.get("layer1_visual_content")) or as_dict(
        nested.get("layer6_flags_and_scores")
    ):
        return nested
    return root


def derive_method(payload: dict[str, Any]) -> str:
    return (
        str(payload.get("derive_method") or payload.get("analysis_method") or "mock")
        .strip()
        .lower()
        or "mock"
    )


def target(payload: dict[str, Any]) -> tuple[str, str]:
    return (
        str(payload.get("target_type") or "").strip(),
        str(payload.get("target_id") or "").strip(),
    )


__all__ = [
    "as_dict",
    "compact_text",
    "derive_method",
    "final_v1_payload",
    "int_or_none",
    "item_profile_state",
    "json_dumps",
    "LINEAGE_STAGE_ROLES",
    "loads",
    "score_confidence",
    "score_value",
    "target",
]
