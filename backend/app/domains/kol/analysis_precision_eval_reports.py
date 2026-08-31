"""Gate/report builders for offline KOL precision evaluation."""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Mapping


def global_blockers(
    *,
    overall: Mapping[str, Any],
    agreement: Mapping[str, Any],
    platform_shortfalls: Mapping[str, int],
    unqualified_review_count: int,
    dual_review_ratio: float | None,
    policy: Any,
) -> list[str]:
    blockers: list[str] = []
    if overall["sample_size"] < policy.minimum_total:
        blockers.append("sample_size_below_minimum")
    if overall["positive_labels"] < policy.minimum_positive:
        blockers.append("positive_labels_below_minimum")
    if overall["negative_labels"] < policy.minimum_negative:
        blockers.append("negative_labels_below_minimum")
    if unqualified_review_count:
        blockers.append("labels_not_human_reviewed")
    if (dual_review_ratio or 0) < policy.minimum_dual_review_ratio:
        blockers.append("dual_review_coverage_below_minimum")
    if agreement["pair_count"] < policy.minimum_kappa_pairs:
        blockers.append("kappa_pair_count_below_minimum")
    elif agreement["cohen_kappa"] is None or agreement["cohen_kappa"] < policy.minimum_cohen_kappa:
        blockers.append("cohen_kappa_below_minimum")
    if platform_shortfalls:
        blockers.append("required_platform_coverage_below_minimum")
    return blockers


def task_report(
    rows: list[dict[str, Any]],
    *,
    policy: Any,
    accepted_review: set[str],
    required_platforms: set[str],
    metrics_fn: Callable[..., dict[str, Any]],
    agreement_fn: Callable[[list[dict[str, Any]]], dict[str, Any]],
    safe_ratio_fn: Callable[[int | float, int | float], float | None],
) -> dict[str, Any]:
    metrics = metrics_fn(rows, calibration_bins=policy.calibration_bins)
    review_counts = Counter(row["review_status"] for row in rows)
    platform_counts = Counter(row["platform"] for row in rows)
    agreement = agreement_fn(rows)
    dual_count = sum(
        row["review_status"] in {"dual_reviewed", "adjudicated"} or row["reviewer_pair"] is not None
        for row in rows
    )
    dual_ratio = safe_ratio_fn(dual_count, len(rows))
    blockers: list[str] = []
    if metrics["sample_size"] < policy.minimum_per_task:
        blockers.append("task_sample_size_below_minimum")
    if metrics["positive_labels"] < policy.minimum_positive:
        blockers.append("task_positive_labels_below_minimum")
    if metrics["negative_labels"] < policy.minimum_negative:
        blockers.append("task_negative_labels_below_minimum")
    if any(status not in accepted_review for status in review_counts):
        blockers.append("task_labels_not_human_reviewed")
    if (dual_ratio or 0) < policy.minimum_dual_review_ratio:
        blockers.append("task_dual_review_coverage_below_minimum")
    if agreement["pair_count"] < policy.minimum_kappa_pairs:
        blockers.append("task_kappa_pair_count_below_minimum")
    elif agreement["cohen_kappa"] is None or agreement["cohen_kappa"] < policy.minimum_cohen_kappa:
        blockers.append("task_cohen_kappa_below_minimum")
    if any(platform_counts.get(platform, 0) < policy.minimum_per_platform for platform in required_platforms):
        blockers.append("task_required_platform_coverage_below_minimum")
    return {
        **metrics,
        "accuracy_claimable": not blockers,
        "blockers": blockers,
        "review_status_counts": dict(sorted(review_counts.items())),
        "dual_review_count": dual_count,
        "dual_review_ratio": dual_ratio,
        "agreement": agreement,
        "platform_counts": dict(sorted(platform_counts.items())),
    }


def build_task_reports(
    rows_by_task: Mapping[str, list[dict[str, Any]]],
    *,
    policy: Any,
    accepted_review: set[str],
    required_platforms: set[str],
    metrics_fn: Callable[..., dict[str, Any]],
    agreement_fn: Callable[[list[dict[str, Any]]], dict[str, Any]],
    safe_ratio_fn: Callable[[int | float, int | float], float | None],
) -> dict[str, dict[str, Any]]:
    return {
        task: task_report(
            rows,
            policy=policy,
            accepted_review=accepted_review,
            required_platforms=required_platforms,
            metrics_fn=metrics_fn,
            agreement_fn=agreement_fn,
            safe_ratio_fn=safe_ratio_fn,
        )
        for task, rows in sorted(rows_by_task.items())
    }
