"""Metric and audience calculations for prospective-growth scoring.

Pure helpers only: no providers, persistence, eligibility decisions, or
brand-history weighting. Missing observations are omitted and weights are
renormalized by the caller-facing helpers below.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any


ACTIVATION_SIGNAL_WEIGHTS: dict[str, float] = {
    "avg_views": 0.34,
    "engagement": 0.28,
    "views_per_follower": 0.22,
    "comments_per_follower": 0.11,
    "followers_reach": 0.05,
}

# Market activation is decision support, never a conversion prediction.  The
# strict gate deliberately uses simple, auditable floors instead of a cohort
# percentile: a weak candidate must not pass merely because it is the only row
# (or the least weak row) in a provider batch.
MARKET_ACTIVATION_MIN_SAMPLE_COUNT = 3
MARKET_ACTIVATION_FLOORS: dict[str, float] = {
    "avg_views": 1_000.0,
    "engagement": 0.01,
    "views_per_follower": 0.05,
    "comments_per_follower": 0.0002,
}

_OUTCOME_FIELDS = (
    "was_shortlisted",
    "shortlisted",
    "claimed",
    "outreach",
    "outreached",
    "reply_received",
    "replied",
    "agreement",
    "signed",
    "content_published",
    "attributed_orders",
    "order_attributed",
    "conversions",
    "verified_conversions",
    "attributed_revenue",
    "finalized",
)

_SCORE_LABELS = {
    "poor": 25.0,
    "fair": 50.0,
    "good": 75.0,
    "excellent": 100.0,
    "low": 25.0,
    "medium": 60.0,
    "high": 90.0,
}
_DIMENSION_ALIASES = {
    "usa": "us",
    "unitedstates": "us",
    "unitedstatesofamerica": "us",
    "美国": "us",
    "uk": "gb",
    "unitedkingdom": "gb",
    "英国": "gb",
    "english": "en",
    "英语": "en",
    "chinese": "zh",
    "mandarin": "zh",
    "中文": "zh",
    "japanese": "ja",
    "日语": "ja",
    "korean": "ko",
    "韩语": "ko",
    "spanish": "es",
    "西班牙语": "es",
    "german": "de",
    "德语": "de",
    "french": "fr",
    "法语": "fr",
}


def _number(value: Any, *, minimum: float | None = None) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or (minimum is not None and parsed < minimum):
        return None
    return parsed


def _score_100(value: Any) -> float | None:
    """Read an explicit 0-100 score without guessing that ``1`` means 100."""

    if isinstance(value, str):
        label = value.strip().lower()
        if label in _SCORE_LABELS:
            return _SCORE_LABELS[label]
    parsed = _number(value, minimum=0.0)
    if parsed is None:
        return None
    return round(min(100.0, parsed), 6)


def _percentile_01(value: Any) -> float | None:
    parsed = _number(value, minimum=0.0)
    if parsed is None:
        return None
    if parsed > 1.0:
        parsed /= 100.0
    return round(min(1.0, parsed), 6)


def _normal_text(value: Any) -> str:
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", str(value or "").lower())
    return " ".join(text.split())


def _dimension_token(value: Any) -> str:
    text = _normal_text(value)
    if re.fullmatch(r"[a-z]{2} [a-z]{2}", text):
        return text[:2]
    normalized = text.replace(" ", "")
    return _DIMENSION_ALIASES.get(normalized, normalized)


def _iter_values(value: Any) -> Iterable[Any]:
    if value in (None, ""):
        return ()
    if isinstance(value, Mapping):
        values: list[Any] = []
        for key in ("term", "value", "label", "name", "terms", "values", "items"):
            if key in value:
                values.extend(_iter_values(value.get(key)))
        return values
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = []
        for item in value:
            values.extend(_iter_values(item))
        return values
    if isinstance(value, str):
        return [part for part in re.split(r"[,，、;/|]+", value) if part.strip()]
    return (value,)


def _dedupe_terms(values: Iterable[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = _normal_text(value)
        if term and term not in seen:
            seen.add(term)
            output.append(term)
    return output
def _nested_value(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) not in (None, ""):
            return item.get(key)
    for container_key in ("metrics", "performance", "analysis", "quality", "evidence_quality"):
        nested = item.get(container_key)
        if isinstance(nested, Mapping):
            for key in keys:
                if key in nested and nested.get(key) not in (None, ""):
                    return nested.get(key)
    return None


def _raw_platform_metrics(item: Mapping[str, Any]) -> dict[str, float | None]:
    followers = _number(_nested_value(item, "followers", "follower_count", "subscribers"), minimum=0.0)
    avg_views = _number(_nested_value(item, "avg_views", "average_views", "median_views"), minimum=0.0)
    if _lifetime_proxy(item):
        avg_views = None
    representative_views = _number(
        _nested_value(item, "representative_video_views"), minimum=0.0
    )
    views_signal = avg_views if avg_views is not None else representative_views
    engagement = _number(_nested_value(item, "engagement_rate", "engagement"), minimum=0.0)
    avg_comments = _number(_nested_value(item, "avg_comments", "average_comments"), minimum=0.0)
    representative_likes = _number(
        _nested_value(item, "representative_video_likes"), minimum=0.0
    )
    representative_comments = _number(
        _nested_value(item, "representative_video_comments"), minimum=0.0
    )
    comments_signal = avg_comments if avg_comments is not None else representative_comments
    if engagement is None and views_signal and (
        representative_likes is not None or representative_comments is not None
    ):
        engagement = (representative_likes or 0.0) + (representative_comments or 0.0)
        engagement /= views_signal
    views_per_follower = _number(
        _nested_value(item, "views_per_follower", "view_rate", "avg_views_per_follower"),
        minimum=0.0,
    )
    comments_per_follower = _number(
        _nested_value(item, "comments_per_follower", "comment_rate", "avg_comments_per_follower"),
        minimum=0.0,
    )
    if views_per_follower is None and followers and views_signal is not None:
        views_per_follower = views_signal / followers
    if comments_per_follower is None and followers and comments_signal is not None:
        comments_per_follower = comments_signal / followers
    return {
        "avg_views": views_signal,
        "engagement": engagement,
        "views_per_follower": views_per_follower,
        "comments_per_follower": comments_per_follower,
        "followers_reach": followers,
    }


def _upstream_percentiles(item: Mapping[str, Any]) -> dict[str, float]:
    containers: list[Mapping[str, Any]] = []
    for key in ("platform_percentiles", "growth_percentiles", "activation_percentiles"):
        value = item.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    calibration = item.get("platform_calibration")
    if isinstance(calibration, Mapping) and isinstance(calibration.get("values"), Mapping):
        containers.append(calibration["values"])
    aliases = {
        "avg_views": ("avg_views", "avg_views_percentile", "views"),
        "engagement": ("engagement", "engagement_rate", "engagement_percentile"),
        "views_per_follower": ("views_per_follower", "view_rate", "view_rate_percentile"),
        "comments_per_follower": ("comments_per_follower", "comment_rate", "comment_rate_percentile"),
        "followers_reach": ("followers_reach", "followers", "followers_percentile", "reach"),
    }
    output: dict[str, float] = {}
    for metric, keys in aliases.items():
        for container in containers:
            value = next((container.get(key) for key in keys if container.get(key) not in (None, "")), None)
            parsed = _percentile_01(value)
            if parsed is not None:
                output[metric] = parsed
                break
        if metric in output:
            continue
        direct = next(
            (item.get(key) for key in keys if "percentile" in key and item.get(key) not in (None, "")),
            None,
        )
        parsed = _percentile_01(direct)
        if parsed is not None:
            output[metric] = parsed
    return output


def _percentile_map(values: Sequence[tuple[int, float]]) -> dict[int, float]:
    if not values:
        return {}
    ordered = sorted(values, key=lambda pair: (pair[1], pair[0]))
    if len(ordered) == 1:
        return {ordered[0][0]: 0.5}
    output: dict[int, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        percentile = ((index + end - 1) / 2.0) / (len(ordered) - 1)
        for row_index in range(index, end):
            output[ordered[row_index][0]] = round(percentile, 6)
        index = end
    return output


def platform_percentiles(
    items: Sequence[Mapping[str, Any]],
    *,
    eligible_indices: set[int] | None = None,
) -> dict[int, dict[str, float]]:
    """Calibrate raw metrics only against eligible rows on the same platform.

    Upstream percentiles remain descriptive input for their own row, but rows
    excluded by the operator's hard facets never enter another candidate's
    empirical distribution.
    """

    output = {index: _upstream_percentiles(item) for index, item in enumerate(items)}
    for index, item in enumerate(items):
        if _lifetime_proxy(item):
            output[index].pop("avg_views", None)
            output[index].pop("views_per_follower", None)
    platforms: dict[str, list[int]] = {}
    raw = [_raw_platform_metrics(item) for item in items]
    for index, item in enumerate(items):
        if eligible_indices is not None and index not in eligible_indices:
            continue
        platform = _normal_text(item.get("platform")) or "unknown"
        platforms.setdefault(platform, []).append(index)
    for indices in platforms.values():
        for metric in ACTIVATION_SIGNAL_WEIGHTS:
            values = [
                (index, float(raw[index][metric]))
                for index in indices
                if raw[index][metric] is not None
            ]
            for index, percentile in _percentile_map(values).items():
                output[index].setdefault(metric, percentile)
    return output


def _activation_sample_count(item: Mapping[str, Any]) -> int | None:
    value = _number(
        _nested_value(
            item,
            "activation_sample_count",
            "recent_video_sample_count",
            "metrics_sample_count",
            "video_evidence_count",
        ),
        minimum=0.0,
    )
    if value is not None:
        return int(value)
    if _normal_text(item.get("activation_metrics_scope")) == "exact query hit 45d":
        return 1
    return None


def _activation_metric_sample_counts(
    item: Mapping[str, Any],
    *,
    sample_count: int | None,
    metrics: Mapping[str, float | None],
) -> tuple[dict[str, int | None], str]:
    """Return auditable per-metric sample sizes for the strict floor check.

    New provider aggregates publish exact counts. Older aggregates did not, so
    they retain their historical aggregate-level sample assumption instead of
    being silently rewritten during rollout.
    """

    raw = item.get("activation_metric_sample_counts")
    if isinstance(raw, Mapping):
        counts: dict[str, int | None] = {}
        for metric in MARKET_ACTIVATION_FLOORS:
            parsed = _number(raw.get(metric), minimum=0.0)
            counts[metric] = int(parsed) if parsed is not None else None
        return counts, "explicit_per_metric_counts"
    return {
        metric: sample_count if value is not None else None
        for metric, value in metrics.items()
        if metric in MARKET_ACTIVATION_FLOORS
    }, "legacy_aggregate_sample_assumption"


def _lifetime_proxy(item: Mapping[str, Any]) -> bool:
    source = _normal_text(item.get("avg_views_source"))
    scope = _normal_text(item.get("avg_views_scope"))
    if "lifetime" in source or "lifetime" in scope:
        return True
    # Old rows sometimes stored channel totals beside an unproven quotient.
    # Without provenance, that quotient is display-only, not a recent average.
    has_channel_lifetime = any(
        _nested_value(item, key) not in (None, "")
        for key in (
            "channel_total_views",
            "channel_video_count",
            "channel_lifetime_views",
            "channel_public_video_count",
            "channel_lifetime_views_per_public_video",
        )
    )
    return bool(has_channel_lifetime and not source and not scope)


def market_activation_gate(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return a server-owned, descriptive activation eligibility verdict.

    A single representative video is useful evidence for follow-up, but never
    enough for the strict-30 list.  At least three observations plus one
    conservative absolute or follower-normalised signal are required.
    """

    sample_count = _activation_sample_count(item)
    metrics = _raw_platform_metrics(item)
    metric_sample_counts, metric_sample_policy = _activation_metric_sample_counts(
        item,
        sample_count=sample_count,
        metrics=metrics,
    )
    metric_sample_sufficient = {
        metric: (
            metric_sample_counts.get(metric) is not None
            and int(metric_sample_counts[metric] or 0) >= MARKET_ACTIVATION_MIN_SAMPLE_COUNT
        )
        for metric in MARKET_ACTIVATION_FLOORS
    }
    representative_only = (
        _normal_text(item.get("activation_metrics_scope")) == "exact query hit 45d"
        or (
            _nested_value(item, "representative_video_views") not in (None, "")
            and _nested_value(item, "avg_views", "average_views", "median_views") in (None, "")
        )
    )
    lifetime_proxy = _lifetime_proxy(item)

    if lifetime_proxy:
        status = "market_activation_missing"
    elif representative_only or (sample_count is not None and sample_count < MARKET_ACTIVATION_MIN_SAMPLE_COUNT):
        status = "insufficient_sample"
    elif sample_count is None:
        status = "market_activation_missing"
    else:
        raw_floor_results = {
            metric: (
                metrics.get(metric) is not None
                and float(metrics[metric]) >= floor
            )
            for metric, floor in MARKET_ACTIVATION_FLOORS.items()
        }
        floor_results = {
            metric: raw_floor_results[metric] and metric_sample_sufficient[metric]
            for metric in MARKET_ACTIVATION_FLOORS
        }
        observed = any(metrics.get(metric) is not None for metric in MARKET_ACTIVATION_FLOORS)
        above_floor_but_under_sampled = any(
            raw_floor_results[metric] and not metric_sample_sufficient[metric]
            for metric in MARKET_ACTIVATION_FLOORS
        )
        status = (
            "passed"
            if any(floor_results.values())
            else "insufficient_metric_sample"
            if above_floor_but_under_sampled
            else "below_floor"
            if observed
            else "market_activation_missing"
        )

    floor_results = {
        metric: (
            metrics.get(metric) is not None
            and float(metrics[metric]) >= floor
            and metric_sample_sufficient[metric]
        )
        for metric, floor in MARKET_ACTIVATION_FLOORS.items()
    }
    return {
        "passed": status == "passed",
        "status": status,
        "reason": None if status == "passed" else status,
        "sample_count": sample_count,
        "minimum_sample_count": MARKET_ACTIVATION_MIN_SAMPLE_COUNT,
        "sample_policy": "recent_or_aggregate_minimum_3_observations_per_passing_metric",
        "metric_sample_policy": metric_sample_policy,
        "metric_sample_counts": metric_sample_counts,
        "metric_sample_sufficient": metric_sample_sufficient,
        "floor_policy": "one_absolute_or_explainable_follower_ratio_signal",
        "floors": dict(MARKET_ACTIVATION_FLOORS),
        "floor_results": floor_results,
        "observed_metrics": {
            key: round(float(value), 8)
            for key, value in metrics.items()
            if key in MARKET_ACTIVATION_FLOORS and value is not None
        },
        "representative_video_policy": "provisional_only_never_strict_qualification",
        "lifetime_proxy": lifetime_proxy,
        "claim_status": "descriptive_only",
        "conversion_claim": False,
    }


def weighted_observed_score(
    components: Sequence[tuple[str, float | None, float]],
) -> tuple[float | None, list[str], float]:
    total_weight = sum(max(0.0, weight) for _name, _value, weight in components)
    observed = [(name, value, weight) for name, value, weight in components if value is not None and weight > 0]
    observed_weight = sum(weight for _name, _value, weight in observed)
    if observed_weight <= 0:
        return None, [name for name, _value, _weight in components], 0.0
    score = sum(float(value) * weight for _name, value, weight in observed) / observed_weight
    missing = [name for name, value, _weight in components if value is None]
    coverage = observed_weight / total_weight if total_weight else 0.0
    return round(score, 6), missing, round(coverage, 6)


def market_activation(percentiles: Mapping[str, float]) -> tuple[float | None, dict[str, Any]]:
    # Reach alone must not masquerade as market activation.
    substantive = any(
        percentiles.get(metric) is not None
        for metric in ("avg_views", "engagement", "views_per_follower", "comments_per_follower")
    )
    components = [
        (metric, percentiles.get(metric), weight)
        for metric, weight in ACTIVATION_SIGNAL_WEIGHTS.items()
    ]
    score, missing, coverage = weighted_observed_score(components)
    if not substantive:
        score = None
    return (
        round(score * 100.0, 6) if score is not None else None,
        {
            "method": "within_platform_empirical_percentile",
            "values": {key: round(value, 6) for key, value in percentiles.items()},
            "missing_signals": missing,
            "signal_coverage": coverage,
            "followers_weight": ACTIVATION_SIGNAL_WEIGHTS["followers_reach"],
            "followers_policy": "low_weight_reach_signal_never_eligibility_gate",
        },
    )


def _targets(payloads: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[str]:
    values: list[Any] = []
    for payload in payloads:
        for source in (payload, payload.get("filters") if isinstance(payload.get("filters"), Mapping) else {}):
            if not isinstance(source, Mapping):
                continue
            for key in keys:
                if key in source:
                    values.extend(_iter_values(source.get(key)))
    return _dedupe_terms(values)


def _distribution(item: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for source in (
        item,
        item.get("audience") if isinstance(item.get("audience"), Mapping) else {},
        item.get("raw_platform_data") if isinstance(item.get("raw_platform_data"), Mapping) else {},
    ):
        for key in keys:
            if key in source and source.get(key) not in (None, ""):
                return source.get(key)
    return None


def _distribution_match(value: Any, targets: Sequence[str]) -> float | None:
    normalized_targets = {_dimension_token(target) for target in targets if _dimension_token(target)}
    if not normalized_targets or value in (None, ""):
        return None
    if isinstance(value, Mapping):
        pairs: list[tuple[str, float]] = []
        for key, raw_share in value.items():
            share = _number(raw_share, minimum=0.0)
            if share is not None:
                pairs.append((_dimension_token(key), share))
        if not pairs:
            return None
        total = sum(share for _key, share in pairs)
        if total <= 0:
            return None
        matched = sum(share for key, share in pairs if key in normalized_targets)
        return round(min(100.0, 100.0 * matched / total), 6)
    values: list[str] = []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for record in value:
            if isinstance(record, Mapping):
                label = record.get("country") or record.get("market") or record.get("language") or record.get("code")
                if label:
                    values.append(_dimension_token(label))
            else:
                values.append(_dimension_token(record))
    else:
        values = [_dimension_token(value)]
    values = [entry for entry in values if entry]
    if not values:
        return None
    return 100.0 if normalized_targets.intersection(values) else 0.0


def audience_fit(
    item: Mapping[str, Any],
    search_brief: Mapping[str, Any],
    query_cell: Mapping[str, Any],
) -> tuple[float | None, dict[str, Any]]:
    direct = _score_100(
        _nested_value(item, "audience_fit_score", "audience_match_score", "target_audience_fit", "audience_fit")
    )
    markets = _targets((search_brief, query_cell), ("target_markets", "target_market", "markets", "market"))
    languages = _targets((search_brief, query_cell), ("target_languages", "languages", "language"))
    market_match = _distribution_match(
        _distribution(item, ("audience_market_distribution", "audience_country_distribution", "audience_markets", "audience_geo")),
        markets,
    )
    language_match = _distribution_match(
        _distribution(item, ("audience_language_distribution", "audience_languages")),
        languages,
    )
    score, missing, coverage = weighted_observed_score(
        (
            ("direct_audience_fit", direct, 0.60),
            ("target_market_audience_share", market_match, 0.28),
            ("target_language_audience_share", language_match, 0.12),
        )
    )
    return score, {
        "target_markets": markets,
        "target_languages": languages,
        "missing_signals": missing,
        "signal_coverage": coverage,
        "profile_country_used_as_audience": False,
    }


def content_execution(item: Mapping[str, Any]) -> tuple[float | None, dict[str, Any]]:
    direct = _score_100(_nested_value(item, "content_execution_score", "content_execution"))
    production = _score_100(_nested_value(item, "production_quality_score", "production_quality"))
    consistency = _score_100(
        _nested_value(item, "posting_consistency_score", "content_consistency_score", "posting_consistency")
    )
    consistency_percentile = _percentile_01(
        _nested_value(item, "posting_consistency_percentile", "recent_content_consistency_percentile")
    )
    if consistency is None and consistency_percentile is not None:
        consistency = consistency_percentile * 100.0
    originality = _score_100(_nested_value(item, "originality_score", "originality"))
    score, missing, coverage = weighted_observed_score(
        (
            ("direct_content_execution", direct, 0.45),
            ("production_quality", production, 0.25),
            ("posting_consistency", consistency, 0.20),
            ("originality", originality, 0.10),
        )
    )
    return score, {"missing_signals": missing, "signal_coverage": coverage}


def sample_depth(item: Mapping[str, Any], evidence_contract: Mapping[str, Any]) -> float:
    video_count = _number(_nested_value(item, "video_evidence_count"), minimum=0.0)
    if video_count is None:
        video_count = _number(_nested_value(item, "activation_sample_count"), minimum=0.0)
    deep_count = _number(_nested_value(item, "deep_analysis_count"), minimum=0.0)
    observed_depth = 0.0
    if video_count is not None:
        observed_depth += 0.70 * min(1.0, video_count / 5.0)
    if deep_count is not None:
        observed_depth += 0.30 * min(1.0, deep_count / 3.0)
    proof_count = len(evidence_contract.get("matched_product_terms") or []) + len(
        evidence_contract.get("matched_scene_terms") or []
    )
    proof_depth = min(1.0, proof_count / 4.0)
    return round(max(observed_depth, proof_depth), 6)


def outcome_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    fields: set[str] = set()
    for key in _OUTCOME_FIELDS:
        if key in item and item.get(key) not in (None, ""):
            fields.add(key)
    for container_key in ("outcome", "outcomes", "partnership_outcome"):
        nested = item.get(container_key)
        if isinstance(nested, Mapping):
            fields.update(str(key) for key, value in nested.items() if value not in (None, ""))
    return {
        "available": bool(fields),
        "fields": sorted(fields),
        "included_in_score": False,
        "weight": 0.0,
        "note": "真实合作与转化结果单独观测；本描述性候选分不使用这些结果。",
    }


def present_fields(item: Mapping[str, Any], names: Sequence[str]) -> list[str]:
    return sorted(name for name in names if name in item and item.get(name) not in (None, ""))


__all__ = [
    "ACTIVATION_SIGNAL_WEIGHTS",
    "MARKET_ACTIVATION_MIN_SAMPLE_COUNT",
    "MARKET_ACTIVATION_FLOORS",
    "platform_percentiles",
    "weighted_observed_score",
    "market_activation",
    "market_activation_gate",
    "audience_fit",
    "content_execution",
    "sample_depth",
    "outcome_observation",
    "present_fields",
]
