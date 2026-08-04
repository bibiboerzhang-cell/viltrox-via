"""Offline, label-backed evaluation for KOL analysis decisions.

The runtime may expose evidence coverage and model confidence, but neither is
business accuracy.  This module only evaluates explicit human-reviewed binary
labels.  Abstentions stay visible and count against strict recall instead of
being silently removed from the denominator.

It is deliberately read-only and independent from ``viltrox_fit_score``.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


CLAIM_STATUS = "descriptive_only"
ABSTAIN_TOKENS = {"", "abstain", "insufficient", "not_ready", "unknown"}


@dataclass(frozen=True)
class EvaluationPolicy:
    minimum_total: int = 180
    minimum_per_task: int = 180
    minimum_positive: int = 30
    minimum_negative: int = 30
    minimum_per_platform: int = 60
    reviewed_statuses: tuple[str, ...] = ("single_reviewed", "dual_reviewed", "adjudicated")
    required_platforms: tuple[str, ...] = ("youtube", "instagram", "tiktok")
    minimum_dual_review_ratio: float = 0.2
    minimum_kappa_pairs: int = 30
    minimum_cohen_kappa: float = 0.7
    calibration_bins: int = 10


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _truth(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    token = _text(value)
    if token in {"true", "1", "positive", "yes", "fit", "present"}:
        return True
    if token in {"false", "0", "negative", "no", "not_fit", "absent"}:
        return False
    raise ValueError(f"{field} must be a binary label")


def _prediction(value: Any) -> bool | None:
    if value is None or _text(value) in ABSTAIN_TOKENS:
        return None
    return _truth(value, field="prediction")


def _confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("confidence must be between 0 and 1") from exc
    if not math.isfinite(parsed) or parsed < 0 or parsed > 1:
        raise ValueError("confidence must be between 0 and 1")
    return parsed


def _safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _calibration(rows: list[dict[str, Any]], bins: int) -> dict[str, Any]:
    scored = [row for row in rows if row["prediction"] is not None and row["confidence"] is not None]
    if not scored:
        return {"sample_size": 0, "brier_score": None, "expected_calibration_error": None}

    probabilities: list[tuple[float, int]] = []
    for row in scored:
        confidence = float(row["confidence"])
        probability_positive = confidence if row["prediction"] is True else 1.0 - confidence
        probabilities.append((probability_positive, int(row["gold"])))
    brier = sum((probability - gold) ** 2 for probability, gold in probabilities) / len(probabilities)

    safe_bins = max(2, min(20, int(bins or 10)))
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for probability, gold in probabilities:
        bucket = min(safe_bins - 1, int(probability * safe_bins))
        buckets[bucket].append((probability, gold))
    ece = 0.0
    for bucket_rows in buckets.values():
        avg_probability = sum(item[0] for item in bucket_rows) / len(bucket_rows)
        positive_rate = sum(item[1] for item in bucket_rows) / len(bucket_rows)
        ece += (len(bucket_rows) / len(probabilities)) * abs(avg_probability - positive_rate)
    return {
        "sample_size": len(probabilities),
        "brier_score": round(brier, 4),
        "expected_calibration_error": round(ece, 4),
    }


def _agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [row["reviewer_pair"] for row in rows if row.get("reviewer_pair") is not None]
    if not pairs:
        return {"pair_count": 0, "raw_agreement": None, "cohen_kappa": None}
    observed = sum(left == right for left, right in pairs) / len(pairs)
    left_positive = sum(left for left, _ in pairs) / len(pairs)
    right_positive = sum(right for _, right in pairs) / len(pairs)
    expected = left_positive * right_positive + (1 - left_positive) * (1 - right_positive)
    if expected >= 1:
        kappa = 1.0 if observed >= 1 else None
    else:
        kappa = (observed - expected) / (1 - expected)
    return {
        "pair_count": len(pairs),
        "raw_agreement": round(observed, 4),
        "cohen_kappa": round(kappa, 4) if kappa is not None else None,
    }


def _metrics(rows: list[dict[str, Any]], *, calibration_bins: int) -> dict[str, Any]:
    tp = sum(row["gold"] is True and row["prediction"] is True for row in rows)
    fp = sum(row["gold"] is False and row["prediction"] is True for row in rows)
    tn = sum(row["gold"] is False and row["prediction"] is False for row in rows)
    fn = sum(row["gold"] is True and row["prediction"] is False for row in rows)
    abstain_positive = sum(row["gold"] is True and row["prediction"] is None for row in rows)
    abstain_negative = sum(row["gold"] is False and row["prediction"] is None for row in rows)
    total = len(rows)
    decided = total - abstain_positive - abstain_negative
    return {
        "sample_size": total,
        "positive_labels": tp + fn + abstain_positive,
        "negative_labels": tn + fp + abstain_negative,
        "decided": decided,
        "abstained": total - decided,
        "confusion": {
            "true_positive": tp,
            "false_positive": fp,
            "true_negative": tn,
            "false_negative": fn,
            "abstain_positive": abstain_positive,
            "abstain_negative": abstain_negative,
        },
        "coverage": _safe_ratio(decided, total),
        "abstention_rate": _safe_ratio(total - decided, total),
        "selective_accuracy": _safe_ratio(tp + tn, decided),
        "precision": _safe_ratio(tp, tp + fp),
        # Strict recall includes abstained positive cases: the system did not
        # surface a usable positive decision for those labels.
        "strict_recall": _safe_ratio(tp, tp + fn + abstain_positive),
        "false_positive_rate": _safe_ratio(fp, fp + tn + abstain_negative),
        "calibration": _calibration(rows, calibration_bins),
    }


def _normalize_rows(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(records, start=1):
        case_id = str(raw.get("case_id") or "").strip()
        task = _text(raw.get("task"))
        platform = _text(raw.get("platform")) or "unknown"
        review_status = _text(raw.get("review_status")) or "unreviewed"
        raw_reviewer_labels = raw.get("reviewer_labels")
        reviewer_pair: tuple[bool, bool] | None = None
        if isinstance(raw_reviewer_labels, list) and len(raw_reviewer_labels) >= 2:
            reviewer_pair = (
                _truth(raw_reviewer_labels[0], field="reviewer_labels[0]"),
                _truth(raw_reviewer_labels[1], field="reviewer_labels[1]"),
            )
        if not case_id or not task:
            raise ValueError(f"row {index} requires case_id and task")
        key = (task, case_id)
        if key in seen:
            raise ValueError(f"duplicate task/case_id: {task}/{case_id}")
        seen.add(key)
        normalized.append(
            {
                "case_id": case_id,
                "task": task,
                "platform": platform,
                "review_status": review_status,
                "reviewer_pair": reviewer_pair,
                "gold": _truth(raw.get("gold"), field="gold"),
                "prediction": _prediction(raw.get("prediction")),
                "confidence": _confidence(raw.get("confidence")),
            }
        )
    return normalized


def evaluate_analysis_precision(
    records: Iterable[Mapping[str, Any]],
    *,
    policy: EvaluationPolicy | None = None,
) -> dict[str, Any]:
    """Return label-backed metrics and a fail-closed claimability gate."""

    active_policy = policy or EvaluationPolicy()
    rows = _normalize_rows(records)
    by_task_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task_rows[row["task"]].append(row)

    overall = _metrics(rows, calibration_bins=active_policy.calibration_bins)
    agreement = _agreement(rows)
    platform_counts = Counter(row["platform"] for row in rows)
    review_counts = Counter(row["review_status"] for row in rows)
    accepted_review = set(active_policy.reviewed_statuses)
    unqualified_review_count = sum(count for status, count in review_counts.items() if status not in accepted_review)
    dual_review_count = sum(
        row["review_status"] in {"dual_reviewed", "adjudicated"} or row["reviewer_pair"] is not None
        for row in rows
    )
    dual_review_ratio = _safe_ratio(dual_review_count, len(rows))
    required_platforms = set(active_policy.required_platforms)
    platform_shortfalls = {
        platform: int(active_policy.minimum_per_platform - platform_counts.get(platform, 0))
        for platform in sorted(required_platforms)
        if platform_counts.get(platform, 0) < active_policy.minimum_per_platform
    }
    blockers: list[str] = []
    if overall["sample_size"] < active_policy.minimum_total:
        blockers.append("sample_size_below_minimum")
    if overall["positive_labels"] < active_policy.minimum_positive:
        blockers.append("positive_labels_below_minimum")
    if overall["negative_labels"] < active_policy.minimum_negative:
        blockers.append("negative_labels_below_minimum")
    if unqualified_review_count:
        blockers.append("labels_not_human_reviewed")
    if (dual_review_ratio or 0) < active_policy.minimum_dual_review_ratio:
        blockers.append("dual_review_coverage_below_minimum")
    if agreement["pair_count"] < active_policy.minimum_kappa_pairs:
        blockers.append("kappa_pair_count_below_minimum")
    elif agreement["cohen_kappa"] is None or agreement["cohen_kappa"] < active_policy.minimum_cohen_kappa:
        blockers.append("cohen_kappa_below_minimum")
    if platform_shortfalls:
        blockers.append("required_platform_coverage_below_minimum")

    task_reports: dict[str, dict[str, Any]] = {}
    for task, task_rows in sorted(by_task_rows.items()):
        metrics = _metrics(task_rows, calibration_bins=active_policy.calibration_bins)
        task_review_counts = Counter(row["review_status"] for row in task_rows)
        task_platform_counts = Counter(row["platform"] for row in task_rows)
        task_agreement = _agreement(task_rows)
        task_dual_count = sum(
            row["review_status"] in {"dual_reviewed", "adjudicated"} or row["reviewer_pair"] is not None
            for row in task_rows
        )
        task_dual_ratio = _safe_ratio(task_dual_count, len(task_rows))
        task_blockers: list[str] = []
        if metrics["sample_size"] < active_policy.minimum_per_task:
            task_blockers.append("task_sample_size_below_minimum")
        if metrics["positive_labels"] < active_policy.minimum_positive:
            task_blockers.append("task_positive_labels_below_minimum")
        if metrics["negative_labels"] < active_policy.minimum_negative:
            task_blockers.append("task_negative_labels_below_minimum")
        if any(status not in accepted_review for status in task_review_counts):
            task_blockers.append("task_labels_not_human_reviewed")
        if (task_dual_ratio or 0) < active_policy.minimum_dual_review_ratio:
            task_blockers.append("task_dual_review_coverage_below_minimum")
        if task_agreement["pair_count"] < active_policy.minimum_kappa_pairs:
            task_blockers.append("task_kappa_pair_count_below_minimum")
        elif task_agreement["cohen_kappa"] is None or task_agreement["cohen_kappa"] < active_policy.minimum_cohen_kappa:
            task_blockers.append("task_cohen_kappa_below_minimum")
        if any(
            task_platform_counts.get(platform, 0) < active_policy.minimum_per_platform
            for platform in required_platforms
        ):
            task_blockers.append("task_required_platform_coverage_below_minimum")
        task_reports[task] = {
            **metrics,
            "accuracy_claimable": not task_blockers,
            "blockers": task_blockers,
            "review_status_counts": dict(sorted(task_review_counts.items())),
            "dual_review_count": task_dual_count,
            "dual_review_ratio": task_dual_ratio,
            "agreement": task_agreement,
            "platform_counts": dict(sorted(task_platform_counts.items())),
        }
    if any(report["blockers"] for report in task_reports.values()):
        blockers.append("one_or_more_task_evaluation_gates_failed")

    if not rows:
        evaluation_status = "no_labels"
    elif blockers:
        evaluation_status = "descriptive_baseline_only"
    else:
        evaluation_status = "offline_evaluation_gate_passed"
    return {
        "schema_version": "kol_analysis_precision_eval_v1",
        "claim_status": CLAIM_STATUS,
        "evaluation_status": evaluation_status,
        "accuracy_claimable": not blockers and bool(rows),
        "blockers": blockers,
        "policy": asdict(active_policy),
        "review_status_counts": dict(sorted(review_counts.items())),
        "dual_review_count": dual_review_count,
        "dual_review_ratio": dual_review_ratio,
        "agreement": agreement,
        "platform_counts": dict(sorted(platform_counts.items())),
        "platform_shortfalls": platform_shortfalls,
        "overall": overall,
        "by_task": task_reports,
        "notes": [
            "evidence coverage and model confidence are not accuracy",
            "abstained positive labels remain in strict recall",
            "offline evaluation does not prove campaign ROI or business outcome",
        ],
        "diagnostics": {
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "viltrox_fit_score_write": False,
        },
    }
