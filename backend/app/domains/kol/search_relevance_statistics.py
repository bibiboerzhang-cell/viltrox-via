"""Pure statistical helpers for offline KOL search relevance evaluation."""
from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def wilson_interval(
    successes: int,
    sample_size: int,
    z: float = 1.959963984540054,
) -> dict[str, Any]:
    if sample_size <= 0:
        return {"method": "wilson_95", "low": None, "high": None, "sample_size": 0}
    probability = successes / sample_size
    denominator = 1 + z * z / sample_size
    centre = (probability + z * z / (2 * sample_size)) / denominator
    margin = (
        z
        * math.sqrt(
            (probability * (1 - probability) + z * z / (4 * sample_size))
            / sample_size
        )
        / denominator
    )
    return {
        "method": "wilson_95",
        "low": round(max(0.0, centre - margin), 4),
        "high": round(min(1.0, centre + margin), 4),
        "sample_size": sample_size,
    }


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    iterations: int,
    seed: int,
) -> dict[str, Any]:
    if not values:
        return {
            "method": "query_bootstrap_percentile_95",
            "low": None,
            "high": None,
            "sample_size": 0,
            "iterations": iterations,
        }
    generator = random.Random(seed)
    count = len(values)
    samples = sorted(
        sum(values[generator.randrange(count)] for _ in range(count)) / count
        for _ in range(max(200, iterations))
    )
    low_index = max(0, int(0.025 * (len(samples) - 1)))
    high_index = min(len(samples) - 1, int(0.975 * (len(samples) - 1)))
    return {
        "method": "query_bootstrap_percentile_95",
        "low": round(samples[low_index], 4),
        "high": round(samples[high_index], 4),
        "sample_size": count,
        "iterations": len(samples),
        "seed": seed,
    }


def _dcg(grades: Sequence[int], cutoff: int) -> float:
    total = 0.0
    for index, grade in enumerate(grades[:cutoff], start=1):
        total += ((2**grade) - 1) / math.log2(index + 1)
    return total


def ndcg(grades: Sequence[int], cutoff: int) -> float:
    actual = _dcg(grades, cutoff)
    ideal = _dcg(sorted(grades[:cutoff], reverse=True), cutoff)
    return round(actual / ideal, 6) if ideal > 0 else 0.0


def tier_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    threshold: int,
) -> dict[str, Any]:
    sample_size = len(rows)
    relevant = sum(int(row["relevance"]) >= threshold for row in rows)
    vertical = sum(bool(row["vertical_fit"]) for row in rows)
    evidence = sum(bool(row["evidence_sufficient"]) for row in rows)
    return {
        "sample_size": sample_size,
        "relevance_hits": relevant,
        "relevance_hit_rate": ratio(relevant, sample_size),
        "relevance_hit_rate_ci95": wilson_interval(relevant, sample_size),
        "vertical_fit_hits": vertical,
        "vertical_fit_rate": ratio(vertical, sample_size),
        "vertical_fit_rate_ci95": wilson_interval(vertical, sample_size),
        "evidence_sufficient_count": evidence,
        "evidence_sufficient_rate": ratio(evidence, sample_size),
        "evidence_sufficient_rate_ci95": wilson_interval(evidence, sample_size),
    }


def judgments_disagree(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if bool(left.get("unable_to_judge")) or bool(right.get("unable_to_judge")):
        return True
    return any(
        left.get(field) != right.get(field)
        for field in ("relevance", "vertical_fit", "evidence_sufficient")
    )


def cohen_kappa(
    pairs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    *,
    threshold: int,
) -> dict[str, Any]:
    judgeable = [
        (left, right)
        for left, right in pairs
        if not left.get("unable_to_judge") and not right.get("unable_to_judge")
    ]
    if not judgeable:
        return {
            "method": "cohen_kappa_binary_relevance_v1",
            "value": None,
            "sample_size": 0,
            "excluded_unable_to_judge_pairs": len(pairs),
            "observed_agreement": None,
        }
    binary_pairs = [
        (
            int(left["relevance"]) >= threshold,
            int(right["relevance"]) >= threshold,
        )
        for left, right in judgeable
    ]
    sample_size = len(binary_pairs)
    observed = sum(left == right for left, right in binary_pairs) / sample_size
    left_positive = sum(left for left, _right in binary_pairs) / sample_size
    right_positive = sum(right for _left, right in binary_pairs) / sample_size
    expected = (
        left_positive * right_positive
        + (1 - left_positive) * (1 - right_positive)
    )
    if math.isclose(expected, 1.0):
        value = 1.0 if math.isclose(observed, 1.0) else 0.0
    else:
        value = (observed - expected) / (1 - expected)
    return {
        "method": "cohen_kappa_binary_relevance_v1",
        "value": round(value, 4),
        "sample_size": sample_size,
        "excluded_unable_to_judge_pairs": len(pairs) - sample_size,
        "observed_agreement": round(observed, 4),
    }


__all__ = [
    "bootstrap_mean_interval",
    "cohen_kappa",
    "judgments_disagree",
    "ndcg",
    "ratio",
    "tier_report",
    "wilson_interval",
]
