"""Sanitized persistence projection for Smart-local qualification contracts."""
from __future__ import annotations

from typing import Any, Callable, Collection


def _safe_policy(
    raw_policy: dict[str, Any],
    *,
    dict_value: Callable[[Any], dict[str, Any]],
    list_value: Callable[[Any], list[Any]],
    safe_int: Callable[..., int | None],
    safe_code: Callable[..., str],
    unknown_policy_key: str,
    unknown_modes: Collection[str],
) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    for key in ("policy_version", "target_count", "candidate_limit", "min_followers", "fresh_priority_days", "max_video_age_days"):
        number = safe_int(raw_policy.get(key), maximum=5_000_000_000)
        if number is not None:
            policy[key] = number
    for key in ("server_owned", "allow_unknown_followers", "allow_unknown_market", "allow_unknown_language", "allow_unknown_profile_type", "allow_low_quality_backfill", "canonical_dedupe"):
        if isinstance(raw_policy.get(key), bool):
            policy[key] = raw_policy[key]
    follower_filter = dict_value(raw_policy.get("followers_filter"))
    if follower_filter:
        policy["followers_filter"] = {
            "requested": follower_filter.get("requested") is True,
            "minimum": safe_int(follower_filter.get("minimum"), maximum=5_000_000_000),
            "maximum": safe_int(follower_filter.get("maximum"), maximum=5_000_000_000),
            "source": safe_code(follower_filter.get("source"), limit=80),
            "unknown_policy": safe_code(follower_filter.get("unknown_policy"), limit=40),
        }
    unknown_activity = safe_code(raw_policy.get(unknown_policy_key), limit=40)
    if unknown_activity in unknown_modes:
        policy[unknown_policy_key] = unknown_activity
    policy["market"] = safe_code(raw_policy.get("market"), limit=40)
    policy["platforms"] = [
        platform
        for entry in list_value(raw_policy.get("platforms"))[:8]
        if (platform := safe_code(entry, limit=40))
    ]
    for key in ("languages", "profile_types", "excluded_account_types"):
        policy[key] = [
            item
            for entry in list_value(raw_policy.get(key))[:16]
            if (item := safe_code(entry, limit=80))
        ]
    return policy


def _safe_deferred(
    raw: dict[str, Any],
    *,
    safe_int: Callable[..., int | None],
    safe_code: Callable[..., str],
    unknown_modes: Collection[str],
) -> dict[str, Any]:
    deferred: dict[str, Any] = {
        "counts_toward_target": raw.get("counts_toward_target") is True,
        "selectable": raw.get("selectable") is True,
    }
    mode = safe_code(raw.get("policy"), limit=40)
    if mode in unknown_modes:
        deferred["policy"] = mode
    for key in ("reason_code", "status"):
        code = safe_code(raw.get(key), limit=80)
        if code:
            deferred[key] = code
    for key in ("available", "returned", "max_video_age_days", "fresh_priority_days"):
        number = safe_int(raw.get(key), maximum=100_000)
        if number is not None:
            deferred[key] = number
    return deferred


def _safe_counts(
    value: Any,
    *,
    dict_value: Callable[[Any], dict[str, Any]],
    safe_int: Callable[..., int | None],
    safe_code: Callable[..., str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key, raw_number in list(dict_value(value).items())[:32]:
        safe_key = safe_code(key, limit=80)
        number = safe_int(raw_number, maximum=100_000)
        if safe_key and number is not None:
            counts[safe_key] = number
    return counts


def _safe_timing(
    value: Any,
    *,
    dict_value: Callable[[Any], dict[str, Any]],
    safe_float: Callable[..., float | None],
    safe_code: Callable[..., str],
) -> dict[str, float]:
    timing: dict[str, float] = {}
    for key, raw_number in list(dict_value(value).items())[:16]:
        safe_key = safe_code(key, limit=80)
        number = safe_float(raw_number, maximum=86_400_000)
        if safe_key and number is not None:
            timing[safe_key] = number
    return timing


def _safe_ratio_policy(
    value: Any,
    *,
    dict_value: Callable[[Any], dict[str, Any]],
    safe_int: Callable[..., int | None],
    safe_code: Callable[..., str],
) -> dict[str, Any]:
    raw = dict_value(value)
    ratio: dict[str, Any] = {}
    policy_name = safe_code(raw.get("policy"), limit=40)
    if policy_name:
        ratio["policy"] = policy_name
    for key in ("creator_target", "reviewer_target", "unused_quota_backfilled"):
        number = safe_int(raw.get(key), maximum=100_000)
        if number is not None:
            ratio[key] = number
    return ratio


def project_local_qualification(
    value: Any,
    *,
    text_value: Callable[[Any], str],
    dict_value: Callable[[Any], dict[str, Any]],
    list_value: Callable[[Any], list[Any]],
    safe_int: Callable[..., int | None],
    safe_float: Callable[..., float | None],
    safe_code: Callable[..., str],
    unknown_policy_key: str,
    unknown_modes: Collection[str],
) -> dict[str, Any]:
    raw = dict_value(value)
    if text_value(raw.get("schema")) != "smart_local_qualified_v2":
        return {}
    output: dict[str, Any] = {"schema": "smart_local_qualified_v2"}
    status = text_value(raw.get("status")).lower()
    if status in {"ready", "shortfall"}:
        output["status"] = status
    for key in ("qualified_count", "returned_count", "qualified_returned_count", "shortfall", "evaluated_count", "unique_evaluated", "unique_qualified"):
        number = safe_int(raw.get(key), maximum=100_000)
        if number is not None:
            output[key] = number
    reason = safe_code(raw.get("shortfall_reason"), limit=120)
    if reason:
        output["shortfall_reason"] = reason
    funnel_scope = safe_code(raw.get("funnel_scope"), limit=80)
    if funnel_scope == "cell_candidate_evaluations":
        output["funnel_scope"] = funnel_scope
    unique_scope = safe_code(raw.get("unique_evaluated_scope"), limit=120)
    if unique_scope:
        output["unique_evaluated_scope"] = unique_scope
    raw_deferred = dict_value(raw.get("deferred_activity"))
    if raw_deferred:
        output["deferred_activity"] = {
            "available": safe_int(raw_deferred.get("available"), maximum=100_000) or 0,
            "returned": safe_int(raw_deferred.get("returned"), maximum=100_000) or 0,
            "counts_toward_target": False,
            "selectable": raw_deferred.get("selectable") is True,
        }
    output["policy"] = _safe_policy(
        dict_value(raw.get("policy")),
        dict_value=dict_value,
        list_value=list_value,
        safe_int=safe_int,
        safe_code=safe_code,
        unknown_policy_key=unknown_policy_key,
        unknown_modes=unknown_modes,
    )
    if raw_deferred:
        output["deferred_activity"] = _safe_deferred(
            raw_deferred,
            safe_int=safe_int,
            safe_code=safe_code,
            unknown_modes=unknown_modes,
        )
    for source_key in ("funnel", "rejected_by_reason"):
        output[source_key] = _safe_counts(
            raw.get(source_key),
            dict_value=dict_value,
            safe_int=safe_int,
            safe_code=safe_code,
        )
    timing = _safe_timing(
        raw.get("stage_timing"),
        dict_value=dict_value,
        safe_float=safe_float,
        safe_code=safe_code,
    )
    if timing:
        output["stage_timing"] = timing
    ratio = _safe_ratio_policy(
        raw.get("ratio_policy"),
        dict_value=dict_value,
        safe_int=safe_int,
        safe_code=safe_code,
    )
    if ratio:
        output["ratio_policy"] = ratio
    scope = safe_code(raw.get("gate_evidence_scope"), limit=80)
    if scope:
        output["gate_evidence_scope"] = scope
    return output
