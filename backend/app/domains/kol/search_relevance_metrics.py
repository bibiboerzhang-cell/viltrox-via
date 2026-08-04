"""Statistical reporting for the human-reviewed KOL relevance gold set.

Candidate export and label validation live in :mod:`search_relevance_eval`.
This module owns metric computation and the final fail-closed evaluation gate.
"""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Any, Iterable, Mapping, Sequence

from app.domains.kol.search_relevance_eval import (
    EVALUATION_SCHEMA_VERSION,
    RETRIEVAL_TIERS,
    SearchEvaluationPolicy,
    _diagnostic_integrity_valid,
    _safe_mapping,
    _text,
    build_runtime_evaluation_status,
    validate_human_labels,
)


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


def _wilson_interval(
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


def _bootstrap_mean_interval(
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


def _ndcg(grades: Sequence[int], cutoff: int) -> float:
    actual = _dcg(grades, cutoff)
    ideal = _dcg(sorted(grades[:cutoff], reverse=True), cutoff)
    return round(actual / ideal, 6) if ideal > 0 else 0.0


def _tier_report(rows: Sequence[Mapping[str, Any]], *, threshold: int) -> dict[str, Any]:
    sample_size = len(rows)
    relevant = sum(int(row["relevance"]) >= threshold for row in rows)
    vertical = sum(bool(row["vertical_fit"]) for row in rows)
    evidence = sum(bool(row["evidence_sufficient"]) for row in rows)
    return {
        "sample_size": sample_size,
        "relevance_hits": relevant,
        "relevance_hit_rate": _ratio(relevant, sample_size),
        "relevance_hit_rate_ci95": _wilson_interval(relevant, sample_size),
        "vertical_fit_hits": vertical,
        "vertical_fit_rate": _ratio(vertical, sample_size),
        "vertical_fit_rate_ci95": _wilson_interval(vertical, sample_size),
        "evidence_sufficient_count": evidence,
        "evidence_sufficient_rate": _ratio(evidence, sample_size),
        "evidence_sufficient_rate_ci95": _wilson_interval(evidence, sample_size),
    }


def _judgments_disagree(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if bool(left.get("unable_to_judge")) or bool(right.get("unable_to_judge")):
        return True
    return any(
        left.get(field) != right.get(field)
        for field in ("relevance", "vertical_fit", "evidence_sufficient")
    )


def _cohen_kappa(
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


def evaluate_search_relevance(
    labels: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    policy: SearchEvaluationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate a fixed suite only after dual review and adjudication."""

    active_policy = policy or SearchEvaluationPolicy()
    raw_labels = list(labels)
    validation = validate_human_labels(raw_labels, manifest=manifest)
    valid_labels = validation["valid_labels"]
    independent_labels = [row for row in valid_labels if row["review_role"] == "independent"]
    adjudication_labels = [row for row in valid_labels if row["review_role"] == "adjudication"]

    manifest_queries = [
        row for row in manifest.get("queries") or [] if isinstance(row, Mapping)
    ]
    manifest_candidates = [
        row for row in manifest.get("candidates") or [] if isinstance(row, Mapping)
    ]
    expected_query_ids = [_text(row.get("query_id")) for row in manifest_queries]
    expected_candidates = active_policy.required_query_count * active_policy.required_candidates_per_query
    expected_independent_reviews = (
        expected_candidates * active_policy.required_independent_reviews_per_candidate
    )

    reviews_by_candidate: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    adjudications_by_candidate: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in independent_labels:
        reviews_by_candidate[(row["query_id"], row["candidate_id"])].append(row)
    for row in adjudication_labels:
        adjudications_by_candidate[(row["query_id"], row["candidate_id"])].append(row)

    ordered_manifest_keys = [
        (_text(row.get("query_id")), _text(row.get("candidate_id")))
        for row in manifest_candidates
    ]
    final_labels: list[dict[str, Any]] = []
    dual_pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    dual_reviewed_candidates = 0
    disagreement_candidates = 0
    adjudicated_candidates = 0
    unable_to_judge_pairs = 0
    duplicate_reviewer_candidates: list[str] = []
    unresolved_candidates: list[str] = []
    unadjudicated_candidates: list[str] = []
    for key in ordered_manifest_keys:
        reviews = sorted(reviews_by_candidate.get(key, []), key=lambda row: row["review_slot"])
        if len(reviews) != active_policy.required_independent_reviews_per_candidate:
            unresolved_candidates.append(f"{key[0]}:{key[1]}")
            continue
        if {row["review_slot"] for row in reviews} != {"A", "B"}:
            unresolved_candidates.append(f"{key[0]}:{key[1]}")
            continue
        if len({row["labeler"] for row in reviews}) != 2:
            duplicate_reviewer_candidates.append(f"{key[0]}:{key[1]}")
            continue
        dual_reviewed_candidates += 1
        dual_pairs.append((reviews[0], reviews[1]))
        if any(row["unable_to_judge"] for row in reviews):
            unable_to_judge_pairs += 1
        disagreement = _judgments_disagree(reviews[0], reviews[1])
        if not disagreement:
            final_labels.append(dict(reviews[0]))
            continue
        disagreement_candidates += 1
        adjudications = adjudications_by_candidate.get(key, [])
        if (
            len(adjudications) != 1
            or adjudications[0]["unable_to_judge"]
            or adjudications[0]["labeler"] in {row["labeler"] for row in reviews}
        ):
            unresolved_key = f"{key[0]}:{key[1]}"
            unresolved_candidates.append(unresolved_key)
            unadjudicated_candidates.append(unresolved_key)
            continue
        adjudicated_candidates += 1
        final_labels.append(dict(adjudications[0]))

    final_labels_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in final_labels:
        final_labels_by_query[row["query_id"]].append(row)
    for rows in final_labels_by_query.values():
        rows.sort(key=lambda row: (row["rank"], row["candidate_id"]))

    blockers: list[str] = []
    if not raw_labels or validation["unlabeled_template_count"] == len(raw_labels):
        blockers.append("no_human_labels")
    if validation["issues"]:
        blockers.append("label_validation_failed")
    if _text(manifest.get("truth_status")) != "candidate_export_not_gold_truth":
        blockers.append("manifest_truth_status_invalid")
    if not bool(manifest.get("candidate_export_complete")):
        blockers.append("candidate_export_incomplete")
    query_integrity_failures = [
        _text(row.get("query_id"))
        for row in manifest_queries
        if not _diagnostic_integrity_valid(
            row.get("diagnostics"),
            expected=active_policy.required_candidates_per_query,
        )
    ]
    if query_integrity_failures:
        blockers.append("one_or_more_query_integrity_contracts_failed")
    manifest_algorithm_version = _text(manifest.get("algorithm_version"))
    query_algorithm_versions = {
        _text(_safe_mapping(row.get("diagnostics")).get("algorithm_version"))
        for row in manifest_queries
    }
    if query_algorithm_versions != {manifest_algorithm_version} or not manifest_algorithm_version:
        blockers.append("algorithm_version_context_mismatch")
    if len(expected_query_ids) != active_policy.required_query_count:
        blockers.append("required_query_count_not_met")
    if int(manifest.get("candidates_per_query") or 0) != active_policy.required_candidates_per_query:
        blockers.append("manifest_candidates_per_query_mismatch")
    if len(manifest_candidates) != expected_candidates:
        blockers.append("manifest_candidate_count_not_exact")
    manifest_ranks_by_query: dict[str, set[int]] = defaultdict(set)
    for row in manifest_candidates:
        try:
            manifest_rank = int(row.get("rank"))
        except (TypeError, ValueError):
            manifest_rank = 0
        manifest_ranks_by_query[_text(row.get("query_id"))].add(manifest_rank)
    exact_ranks = set(range(1, active_policy.required_candidates_per_query + 1))
    if any(manifest_ranks_by_query.get(query_id, set()) != exact_ranks for query_id in expected_query_ids):
        blockers.append("manifest_query_rank_contract_not_met")
    if len(independent_labels) != expected_independent_reviews:
        blockers.append("independent_review_count_below_required")
    independent_labelers = sorted(
        {_text(row.get("labeler")) for row in independent_labels if row.get("labeler")}
    )
    if len(independent_labelers) < active_policy.minimum_distinct_labelers:
        blockers.append("minimum_distinct_human_labelers_not_met")
    if dual_reviewed_candidates != expected_candidates:
        blockers.append("one_or_more_candidates_not_dual_reviewed")
    if duplicate_reviewer_candidates:
        blockers.append("same_reviewer_used_twice_for_candidate")
    if unadjudicated_candidates:
        blockers.append("one_or_more_disagreements_unadjudicated")

    incomplete_queries: dict[str, int] = {}
    for query_id in expected_query_ids:
        rows = final_labels_by_query.get(query_id, [])
        actual_ranks = {int(row["rank"]) for row in rows}
        if len(rows) != active_policy.required_candidates_per_query or actual_ranks != exact_ranks:
            incomplete_queries[query_id] = max(
                0,
                active_policy.required_candidates_per_query - len(rows),
            )
    if incomplete_queries:
        blockers.append("one_or_more_queries_incompletely_resolved")

    inter_rater = _cohen_kappa(
        dual_pairs,
        threshold=active_policy.relevance_threshold,
    )
    inter_rater.update(
        {
            "dual_reviewed_candidates": dual_reviewed_candidates,
            "disagreement_candidates": disagreement_candidates,
            "disagreement_rate": _ratio(disagreement_candidates, dual_reviewed_candidates),
            "unable_to_judge_pairs": unable_to_judge_pairs,
            "adjudicated_candidates": adjudicated_candidates,
            "unresolved_candidates": len(set(unresolved_candidates)),
            "unadjudicated_candidates": len(set(unadjudicated_candidates)),
        }
    )

    base = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "query_suite_version": _text(manifest.get("query_suite_version")),
        "manifest_fingerprint": _text(manifest.get("manifest_fingerprint")),
        "algorithm_version": _text(manifest.get("algorithm_version")),
        "filter_policy_version": _text(manifest.get("filter_policy_version")),
        "code_version": _text(manifest.get("code_version")),
        "dataset_snapshot_id": _text(manifest.get("dataset_snapshot_id")),
        "evaluation_status": "not_evaluated" if blockers else "evaluated",
        "gate_status": "blocked" if blockers else "passed",
        "claim_status": (
            "not_evaluated"
            if blockers
            else "offline_human_label_evaluation_only"
        ),
        # A complete label set makes the listed offline relevance metrics
        # reportable. It still cannot establish general business accuracy,
        # campaign performance, ROI, or online production quality.
        "offline_relevance_metrics_claimable": not blockers,
        "accuracy_claimable": False,
        "business_outcome_claimable": False,
        "blockers": list(dict.fromkeys(blockers)),
        "policy": asdict(active_policy),
        "label_validation": {
            "input_rows": len(raw_labels),
            "valid_human_review_records": len(valid_labels),
            "valid_independent_reviews": len(independent_labels),
            "valid_adjudications": len(adjudication_labels),
            "unlabeled_template_count": validation["unlabeled_template_count"],
            "issue_count": len(validation["issues"]),
            "issue_counts": validation["issue_counts"],
            "issues": validation["issues"][:100],
            "issues_truncated": len(validation["issues"]) > 100,
        },
        "coverage": {
            "required_query_count": active_policy.required_query_count,
            "manifest_query_count": len(expected_query_ids),
            "required_candidates": expected_candidates,
            "required_independent_reviews": expected_independent_reviews,
            "valid_independent_reviews": len(independent_labels),
            "dual_reviewed_candidates": dual_reviewed_candidates,
            "resolved_candidates": len(final_labels),
            "disagreement_candidates": disagreement_candidates,
            "adjudicated_candidates": adjudicated_candidates,
            "unresolved_candidates": len(set(unresolved_candidates)),
            "unadjudicated_candidates": len(set(unadjudicated_candidates)),
            "resolved_candidates_by_query": {
                query_id: len(final_labels_by_query.get(query_id, []))
                for query_id in expected_query_ids
            },
            "incomplete_queries": incomplete_queries,
            "distinct_human_labelers": len(independent_labelers),
        },
        "diagnostics": {
            "provider_calls": False,
            "llm_calls": False,
            "database_write": False,
        },
        "notes": [
            "candidate export and retrieval scores are not gold truth",
            "metrics require two independent human reviews per candidate and adjudication of every disagreement",
            "offline relevance does not prove campaign ROI or business outcome",
            "Cohen kappa is reported for binary relevance agreement and never replaced by model labels",
        ],
    }
    if blockers:
        report = {
            **base,
            "metrics": None,
            "metrics_not_computed_reason": "complete_valid_human_labels_required",
        }
        report["runtime_evaluation_status"] = build_runtime_evaluation_status(
            algorithm_version=_text(manifest.get("algorithm_version")),
            code_version=_text(manifest.get("code_version")),
            dataset_snapshot_id=_text(manifest.get("dataset_snapshot_id")),
            filter_policy_version=_text(manifest.get("filter_policy_version")),
            report=report,
        )
        return report

    per_query: dict[str, dict[str, Any]] = {}
    precision_values: dict[int, list[float]] = {
        cutoff: [] for cutoff in active_policy.precision_cutoffs
    }
    ndcg_values: list[float] = []
    for query_id in expected_query_ids:
        rows = final_labels_by_query[query_id]
        grades = [int(row["relevance"]) for row in rows]
        query_metrics: dict[str, Any] = {
            "sample_size": len(rows),
            "relevance_grade_counts": dict(sorted(Counter(grades).items())),
        }
        for cutoff in active_policy.precision_cutoffs:
            top = rows[:cutoff]
            relevant = sum(
                int(row["relevance"]) >= active_policy.relevance_threshold
                for row in top
            )
            value = relevant / cutoff
            precision_values[cutoff].append(value)
            query_metrics[f"precision_at_{cutoff}"] = round(value, 4)
            query_metrics[f"precision_at_{cutoff}_sample_size"] = len(top)
        query_ndcg = _ndcg(grades, active_policy.ndcg_cutoff)
        ndcg_values.append(query_ndcg)
        query_metrics[f"ndcg_at_{active_policy.ndcg_cutoff}"] = query_ndcg
        per_query[query_id] = query_metrics

    aggregate: dict[str, Any] = {
        "query_sample_size": len(expected_query_ids),
        "candidate_sample_size": len(final_labels),
    }
    for cutoff, values in precision_values.items():
        relevant = sum(
            int(row["relevance"]) >= active_policy.relevance_threshold
            for rows in final_labels_by_query.values()
            for row in rows[:cutoff]
        )
        candidate_n = len(expected_query_ids) * cutoff
        aggregate[f"precision_at_{cutoff}"] = {
            "macro_mean": round(sum(values) / len(values), 4),
            "query_level_ci95": _bootstrap_mean_interval(
                values,
                iterations=active_policy.bootstrap_iterations,
                seed=active_policy.bootstrap_seed + cutoff,
            ),
            "micro_rate": _ratio(relevant, candidate_n),
            "candidate_level_ci95": _wilson_interval(relevant, candidate_n),
            "query_sample_size": len(values),
            "candidate_sample_size": candidate_n,
        }
    aggregate[f"ndcg_at_{active_policy.ndcg_cutoff}"] = {
        "macro_mean": round(sum(ndcg_values) / len(ndcg_values), 4),
        "query_level_ci95": _bootstrap_mean_interval(
            ndcg_values,
            iterations=active_policy.bootstrap_iterations,
            seed=active_policy.bootstrap_seed + active_policy.ndcg_cutoff,
        ),
        "query_sample_size": len(ndcg_values),
    }
    by_tier = {
        tier: _tier_report(
            [row for row in final_labels if row["match_tier"] == tier],
            threshold=active_policy.relevance_threshold,
        )
        for tier in RETRIEVAL_TIERS
    }
    unknown_tiers = sorted(
        {row["match_tier"] for row in final_labels} - set(RETRIEVAL_TIERS)
    )
    if unknown_tiers:
        by_tier["other"] = _tier_report(
            [row for row in final_labels if row["match_tier"] in unknown_tiers],
            threshold=active_policy.relevance_threshold,
        )
    report = {
        **base,
        "metrics": {
            "relevance_threshold": active_policy.relevance_threshold,
            "aggregate": aggregate,
            "by_query": per_query,
            "by_match_tier": by_tier,
            "inter_rater": inter_rater,
            "hard_filter_violation_rate": 0.0,
            "lane_contract_pass_rate": 1.0,
            "overall": _tier_report(
                final_labels,
                threshold=active_policy.relevance_threshold,
            ),
        },
    }
    report["runtime_evaluation_status"] = build_runtime_evaluation_status(
        algorithm_version=_text(manifest.get("algorithm_version")),
        code_version=_text(manifest.get("code_version")),
        dataset_snapshot_id=_text(manifest.get("dataset_snapshot_id")),
        filter_policy_version=_text(manifest.get("filter_policy_version")),
        report=report,
    )
    return report


__all__ = ["evaluate_search_relevance"]
