"""视频分析上下文塑形(从 apify_jobs_worker.py 抽出,行为不变)。

纯函数:evidence/scores → 性能/最终上下文 + 低分提取 + 关键帧请求。依赖全来自 worker helpers barrel。
被 apify_jobs_worker re-export。红线:纯上下文塑形,零触 viltrox_fit_score。
"""
from __future__ import annotations

from typing import Any

from app.workers.apify_jobs_worker_helpers import (
    _float_or_none,
    _int_or_none,
    _iso_or_none,
    _rate,
    _truthy,
)

# Identity of the project-free final_v1 prompt contract.  Stored under
# ``provenance.prompt_contract`` of every final_v1 cache row so a later audit
# (scripts/ops/mark_stale_final_v1_sku_context_cache.py) can separate rows
# produced by this contract from rows produced while project SKU context was
# being injected into the prompt.
FINAL_V1_PROMPT_CONTRACT = "final_v1_pure_video_evidence_v2"

# Keys that must never reach the final_v1 prompt (project / employee scope).
FINAL_V1_PROJECT_SCOPED_KEYS = frozenset(
    {
        "project_id",
        "project_name",
        "product_sku",
        "product_name",
        "linked_products",
        "candidate_products",
        "assignment_id",
    }
)

# Project-agnostic brand / product-line recognition vocabulary.  It helps the
# model *name* what it sees; it is never presence evidence and carries no SKU
# that belongs to a specific project.
FINAL_V1_BRAND_LEXICON = (
    "Viltrox",
    "唯卓仕",
    "Viltrox AF (autofocus prime)",
    "Viltrox Pro series",
    "Viltrox LAB series",
    "Viltrox Air series",
    "Viltrox EPIC / cine lens",
    "Viltrox lens adapter / speed booster",
)


def _low_scores(scores: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, value in scores.items():
        if isinstance(value, (int, float)) and value <= 6:
            output.append({"dimension": key, "score": value})
    return output[:8]


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _video_performance_context(evidence: dict[str, Any]) -> dict[str, Any]:
    views = _int_or_none(evidence.get("view_count"))
    return {
        "view_count": views,
        "like_count": _int_or_none(evidence.get("like_count")),
        "comment_count": _int_or_none(evidence.get("comment_count")),
        "share_count": _int_or_none(evidence.get("share_count")),
        "like_rate": _rate(evidence.get("like_count"), views),
        "comment_rate": _rate(evidence.get("comment_count"), views),
        "duration_seconds": _int_or_none(evidence.get("duration_seconds")),
        "publish_date": _iso_or_none(evidence.get("publish_date")),
        "metrics_source": evidence.get("metrics_source"),
        "metrics_scraped_at": _iso_or_none(evidence.get("metrics_scraped_at")),
        "account_baseline": {
            "followers": _int_or_none(evidence.get("followers")),
            "avg_views": _int_or_none(evidence.get("avg_views")),
            "engagement_rate": _float_or_none(evidence.get("engagement_rate")),
        },
        "relative_to_account_baseline_allowed": False,
        "relative_baseline_note": "followers/avg_views are often missing; use absolute performance only.",
    }


def _video_final_context(evidence: dict[str, Any]) -> dict[str, Any]:
    """final_v1 prompt context: pure video evidence, never project scope.

    Project SKU / project name / other employees' manual product links are
    deliberately absent: they vary per project, so putting them in the prompt
    would leak one project's commercial context into the *global* final_v1
    cache row shared by every project.  SKU association lives only in the
    independent tracking layer and in the project-isolated content-fit layer.
    """
    context = _video_performance_context(evidence)
    context["product_context"] = {
        "creator_handle": evidence.get("creator_handle"),
        "creator_name": evidence.get("creator_name"),
        "kol_pool_id": evidence.get("kol_pool_id"),
        "brand_lexicon": list(FINAL_V1_BRAND_LEXICON),
        "brand_lexicon_is_evidence": False,
        "project_scope": "none",
        "campaign_goal": "sell Viltrox lenses and validate lens proof; not to grow the KOL account",
    }
    context["prompt_contract"] = FINAL_V1_PROMPT_CONTRACT
    leaked = sorted(_project_scoped_keys(context))
    if leaked:
        raise ValueError(f"final_v1 context must stay project-free, leaked={leaked}")
    return context


def _project_scoped_keys(value: Any, _path: str = "") -> set[str]:
    """Return dotted paths of any project-scoped key found anywhere in ``value``."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            path = f"{_path}.{key}" if _path else str(key)
            if str(key) in FINAL_V1_PROJECT_SCOPED_KEYS:
                found.add(path)
            found |= _project_scoped_keys(inner, path)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found |= _project_scoped_keys(inner, f"{_path}[{index}]")
    return found


def final_v1_context_is_project_free(context: dict[str, Any]) -> bool:
    return not _project_scoped_keys(context)


def _select_keyframe_requests(layer1: dict[str, Any], limit: int = 6) -> list[dict[str, str]]:
    timeline = layer1.get("scene_timeline") if isinstance(layer1.get("scene_timeline"), list) else []
    candidates = [
        {"timestamp": str(item.get("timestamp") or ""), "reason": str(item.get("what") or "")}
        for item in timeline
        if isinstance(item, dict) and item.get("timestamp")
    ]
    if not candidates:
        return [{"timestamp": ts, "reason": "fallback keyframe"} for ts in ["00:00", "00:15", "00:45", "01:30", "02:30", "04:30"]]
    if len(candidates) <= limit:
        return candidates
    indexes = [round(index * (len(candidates) - 1) / (limit - 1)) for index in range(limit)]
    output: list[dict[str, str]] = []
    seen: set[int] = set()
    for index in indexes:
        if index in seen:
            continue
        seen.add(index)
        output.append(candidates[index])
    return output[:limit]
