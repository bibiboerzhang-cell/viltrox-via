"""Statistical reporting for the human-reviewed KOL relevance gold set.

Candidate export and label validation live in :mod:`search_relevance_eval`.
This module owns metric computation and the final fail-closed evaluation gate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
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
from app.domains.kol.search_relevance_statistics import (
    bootstrap_mean_interval as _bootstrap_mean_interval,
    cohen_kappa as _cohen_kappa,
    judgments_disagree as _judgments_disagree,
    ndcg as _ndcg,
    ratio as _ratio,
    tier_report as _tier_report,
    wilson_interval as _wilson_interval,
)


@dataclass(frozen=True)
class _EvaluationContext:
    policy: SearchEvaluationPolicy
    raw_labels: list[Any]
    validation: dict[str, Any]
    valid_labels: list[dict[str, Any]]
    independent_labels: list[dict[str, Any]]
    adjudication_labels: list[dict[str, Any]]
    manifest_queries: list[Mapping[str, Any]]
    manifest_candidates: list[Mapping[str, Any]]
    expected_query_ids: list[str]
    expected_candidates: int
    expected_independent_reviews: int


@dataclass(frozen=True)
class _CandidateResolution:
    final_label: dict[str, Any] | None = None
    dual_pair: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None
    disagreement: bool = False
    unable_to_judge: bool = False
    adjudicated: bool = False
    unresolved: bool = False
    duplicate_reviewer: bool = False
    unadjudicated: bool = False


@dataclass(frozen=True)
class _ResolutionSummary:
    final_labels: list[dict[str, Any]]
    final_labels_by_query: dict[str, list[dict[str, Any]]]
    dual_pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]]
    dual_reviewed_candidates: int
    disagreement_candidates: int
    adjudicated_candidates: int
    unable_to_judge_pairs: int
    duplicate_reviewer_candidates: list[str]
    unresolved_candidates: list[str]
    unadjudicated_candidates: list[str]


def _prepare_evaluation(
    labels: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    policy: SearchEvaluationPolicy | None,
) -> _EvaluationContext:
    active_policy = policy or SearchEvaluationPolicy()
    raw_labels = list(labels)
    validation = validate_human_labels(raw_labels, manifest=manifest)
    valid_labels = validation["valid_labels"]
    independent_labels = [
        row for row in valid_labels if row["review_role"] == "independent"
    ]
    adjudication_labels = [
        row for row in valid_labels if row["review_role"] == "adjudication"
    ]
    manifest_queries = [
        row for row in manifest.get("queries") or [] if isinstance(row, Mapping)
    ]
    manifest_candidates = [
        row for row in manifest.get("candidates") or [] if isinstance(row, Mapping)
    ]
    expected_query_ids = [_text(row.get("query_id")) for row in manifest_queries]
    expected_candidates = (
        active_policy.required_query_count
        * active_policy.required_candidates_per_query
    )
    return _EvaluationContext(
        policy=active_policy,
        raw_labels=raw_labels,
        validation=validation,
        valid_labels=valid_labels,
        independent_labels=independent_labels,
        adjudication_labels=adjudication_labels,
        manifest_queries=manifest_queries,
        manifest_candidates=manifest_candidates,
        expected_query_ids=expected_query_ids,
        expected_candidates=expected_candidates,
        expected_independent_reviews=(
            expected_candidates
            * active_policy.required_independent_reviews_per_candidate
        ),
    )


def _index_reviews(
    rows: Sequence[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        indexed[(row["query_id"], row["candidate_id"])].append(row)
    return indexed


def _resolve_candidate(
    reviews: Sequence[dict[str, Any]],
    adjudications: Sequence[dict[str, Any]],
    *,
    policy: SearchEvaluationPolicy,
) -> _CandidateResolution:
    ordered_reviews = sorted(reviews, key=lambda row: row["review_slot"])
    if len(ordered_reviews) != policy.required_independent_reviews_per_candidate:
        return _CandidateResolution(unresolved=True)
    if {row["review_slot"] for row in ordered_reviews} != {"A", "B"}:
        return _CandidateResolution(unresolved=True)
    if len({row["labeler"] for row in ordered_reviews}) != 2:
        return _CandidateResolution(duplicate_reviewer=True)
    pair = (ordered_reviews[0], ordered_reviews[1])
    unable_to_judge = any(row["unable_to_judge"] for row in ordered_reviews)
    if not _judgments_disagree(*pair):
        return _CandidateResolution(
            final_label=dict(ordered_reviews[0]),
            dual_pair=pair,
            unable_to_judge=unable_to_judge,
        )
    invalid_adjudication = (
        len(adjudications) != 1
        or adjudications[0]["unable_to_judge"]
        or adjudications[0]["labeler"]
        in {row["labeler"] for row in ordered_reviews}
    )
    if invalid_adjudication:
        return _CandidateResolution(
            dual_pair=pair,
            disagreement=True,
            unable_to_judge=unable_to_judge,
            unresolved=True,
            unadjudicated=True,
        )
    return _CandidateResolution(
        final_label=dict(adjudications[0]),
        dual_pair=pair,
        disagreement=True,
        unable_to_judge=unable_to_judge,
        adjudicated=True,
    )


def _group_final_labels(
    rows: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    for query_rows in grouped.values():
        query_rows.sort(key=lambda row: (row["rank"], row["candidate_id"]))
    return grouped


def _resolve_reviews(context: _EvaluationContext) -> _ResolutionSummary:
    reviews_by_candidate = _index_reviews(context.independent_labels)
    adjudications_by_candidate = _index_reviews(context.adjudication_labels)
    final_labels: list[dict[str, Any]] = []
    dual_pairs: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    duplicate_reviewers: list[str] = []
    unresolved: list[str] = []
    unadjudicated: list[str] = []
    dual_count = disagreement_count = adjudicated_count = unable_count = 0
    for row in context.manifest_candidates:
        key = (_text(row.get("query_id")), _text(row.get("candidate_id")))
        outcome = _resolve_candidate(
            reviews_by_candidate.get(key, []),
            adjudications_by_candidate.get(key, []),
            policy=context.policy,
        )
        key_text = f"{key[0]}:{key[1]}"
        if outcome.dual_pair is not None:
            dual_pairs.append(outcome.dual_pair)
            dual_count += 1
        if outcome.unable_to_judge:
            unable_count += 1
        if outcome.disagreement:
            disagreement_count += 1
        if outcome.adjudicated:
            adjudicated_count += 1
        if outcome.duplicate_reviewer:
            duplicate_reviewers.append(key_text)
        if outcome.unresolved:
            unresolved.append(key_text)
        if outcome.unadjudicated:
            unadjudicated.append(key_text)
        if outcome.final_label is not None:
            final_labels.append(outcome.final_label)
    return _ResolutionSummary(
        final_labels=final_labels,
        final_labels_by_query=_group_final_labels(final_labels),
        dual_pairs=dual_pairs,
        dual_reviewed_candidates=dual_count,
        disagreement_candidates=disagreement_count,
        adjudicated_candidates=adjudicated_count,
        unable_to_judge_pairs=unable_count,
        duplicate_reviewer_candidates=duplicate_reviewers,
        unresolved_candidates=unresolved,
        unadjudicated_candidates=unadjudicated,
    )


def _manifest_metadata_blockers(
    context: _EvaluationContext,
    manifest: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if _text(manifest.get("truth_status")) != "candidate_export_not_gold_truth":
        blockers.append("manifest_truth_status_invalid")
    if not bool(manifest.get("candidate_export_complete")):
        blockers.append("candidate_export_incomplete")
    integrity_failed = any(
        not _diagnostic_integrity_valid(
            row.get("diagnostics"),
            expected=context.policy.required_candidates_per_query,
        )
        for row in context.manifest_queries
    )
    if integrity_failed:
        blockers.append("one_or_more_query_integrity_contracts_failed")
    manifest_algorithm_version = _text(manifest.get("algorithm_version"))
    query_algorithm_versions = {
        _text(_safe_mapping(row.get("diagnostics")).get("algorithm_version"))
        for row in context.manifest_queries
    }
    if (
        query_algorithm_versions != {manifest_algorithm_version}
        or not manifest_algorithm_version
    ):
        blockers.append("algorithm_version_context_mismatch")
    if len(context.expected_query_ids) != context.policy.required_query_count:
        blockers.append("required_query_count_not_met")
    if (
        int(manifest.get("candidates_per_query") or 0)
        != context.policy.required_candidates_per_query
    ):
        blockers.append("manifest_candidates_per_query_mismatch")
    if len(context.manifest_candidates) != context.expected_candidates:
        blockers.append("manifest_candidate_count_not_exact")
    return blockers


def _manifest_rank_blockers(
    context: _EvaluationContext,
    exact_ranks: set[int],
) -> list[str]:
    ranks_by_query: dict[str, set[int]] = defaultdict(set)
    for row in context.manifest_candidates:
        try:
            rank = int(row.get("rank"))
        except (TypeError, ValueError):
            rank = 0
        ranks_by_query[_text(row.get("query_id"))].add(rank)
    valid = all(
        ranks_by_query.get(query_id, set()) == exact_ranks
        for query_id in context.expected_query_ids
    )
    return [] if valid else ["manifest_query_rank_contract_not_met"]


def _review_contract_blockers(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    independent_labelers: Sequence[str],
) -> list[str]:
    blockers: list[str] = []
    if len(context.independent_labels) != context.expected_independent_reviews:
        blockers.append("independent_review_count_below_required")
    if len(independent_labelers) < context.policy.minimum_distinct_labelers:
        blockers.append("minimum_distinct_human_labelers_not_met")
    if resolution.dual_reviewed_candidates != context.expected_candidates:
        blockers.append("one_or_more_candidates_not_dual_reviewed")
    if resolution.duplicate_reviewer_candidates:
        blockers.append("same_reviewer_used_twice_for_candidate")
    if resolution.unadjudicated_candidates:
        blockers.append("one_or_more_disagreements_unadjudicated")
    return blockers


def _incomplete_queries(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    exact_ranks: set[int],
) -> dict[str, int]:
    incomplete: dict[str, int] = {}
    for query_id in context.expected_query_ids:
        rows = resolution.final_labels_by_query.get(query_id, [])
        actual_ranks = {int(row["rank"]) for row in rows}
        if (
            len(rows) != context.policy.required_candidates_per_query
            or actual_ranks != exact_ranks
        ):
            incomplete[query_id] = max(
                0,
                context.policy.required_candidates_per_query - len(rows),
            )
    return incomplete


def _evaluation_blockers(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    manifest: Mapping[str, Any],
    independent_labelers: Sequence[str],
    incomplete_queries: Mapping[str, int],
    exact_ranks: set[int],
) -> list[str]:
    blockers: list[str] = []
    if (
        not context.raw_labels
        or context.validation["unlabeled_template_count"] == len(context.raw_labels)
    ):
        blockers.append("no_human_labels")
    if context.validation["issues"]:
        blockers.append("label_validation_failed")
    blockers.extend(_manifest_metadata_blockers(context, manifest))
    blockers.extend(_manifest_rank_blockers(context, exact_ranks))
    blockers.extend(
        _review_contract_blockers(context, resolution, independent_labelers)
    )
    if incomplete_queries:
        blockers.append("one_or_more_queries_incompletely_resolved")
    return list(dict.fromkeys(blockers))


def _inter_rater_report(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
) -> dict[str, Any]:
    report = _cohen_kappa(
        resolution.dual_pairs,
        threshold=context.policy.relevance_threshold,
    )
    report.update(
        {
            "dual_reviewed_candidates": resolution.dual_reviewed_candidates,
            "disagreement_candidates": resolution.disagreement_candidates,
            "disagreement_rate": _ratio(
                resolution.disagreement_candidates,
                resolution.dual_reviewed_candidates,
            ),
            "unable_to_judge_pairs": resolution.unable_to_judge_pairs,
            "adjudicated_candidates": resolution.adjudicated_candidates,
            "unresolved_candidates": len(set(resolution.unresolved_candidates)),
            "unadjudicated_candidates": len(
                set(resolution.unadjudicated_candidates)
            ),
        }
    )
    return report


def _base_report(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    *,
    manifest: Mapping[str, Any],
    blockers: Sequence[str],
    independent_labelers: Sequence[str],
    incomplete_queries: Mapping[str, int],
) -> dict[str, Any]:
    blocked = bool(blockers)
    validation = context.validation
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "query_suite_version": _text(manifest.get("query_suite_version")),
        "manifest_fingerprint": _text(manifest.get("manifest_fingerprint")),
        "algorithm_version": _text(manifest.get("algorithm_version")),
        "filter_policy_version": _text(manifest.get("filter_policy_version")),
        "code_version": _text(manifest.get("code_version")),
        "dataset_snapshot_id": _text(manifest.get("dataset_snapshot_id")),
        "evaluation_status": "not_evaluated" if blocked else "evaluated",
        "gate_status": "blocked" if blocked else "passed",
        "claim_status": (
            "not_evaluated" if blocked else "offline_human_label_evaluation_only"
        ),
        # Offline human labels make only these relevance metrics reportable;
        # they do not prove general accuracy, campaign ROI, or business outcome.
        "offline_relevance_metrics_claimable": not blocked,
        "accuracy_claimable": False,
        "business_outcome_claimable": False,
        "blockers": list(blockers),
        "policy": asdict(context.policy),
        "label_validation": {
            "input_rows": len(context.raw_labels),
            "valid_human_review_records": len(context.valid_labels),
            "valid_independent_reviews": len(context.independent_labels),
            "valid_adjudications": len(context.adjudication_labels),
            "unlabeled_template_count": validation["unlabeled_template_count"],
            "issue_count": len(validation["issues"]),
            "issue_counts": validation["issue_counts"],
            "issues": validation["issues"][:100],
            "issues_truncated": len(validation["issues"]) > 100,
        },
        "coverage": {
            "required_query_count": context.policy.required_query_count,
            "manifest_query_count": len(context.expected_query_ids),
            "required_candidates": context.expected_candidates,
            "required_independent_reviews": context.expected_independent_reviews,
            "valid_independent_reviews": len(context.independent_labels),
            "dual_reviewed_candidates": resolution.dual_reviewed_candidates,
            "resolved_candidates": len(resolution.final_labels),
            "disagreement_candidates": resolution.disagreement_candidates,
            "adjudicated_candidates": resolution.adjudicated_candidates,
            "unresolved_candidates": len(set(resolution.unresolved_candidates)),
            "unadjudicated_candidates": len(
                set(resolution.unadjudicated_candidates)
            ),
            "resolved_candidates_by_query": {
                query_id: len(
                    resolution.final_labels_by_query.get(query_id, [])
                )
                for query_id in context.expected_query_ids
            },
            "incomplete_queries": dict(incomplete_queries),
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


def _query_metric_report(
    rows: Sequence[dict[str, Any]],
    *,
    policy: SearchEvaluationPolicy,
) -> tuple[dict[str, Any], dict[int, float], float]:
    grades = [int(row["relevance"]) for row in rows]
    report: dict[str, Any] = {
        "sample_size": len(rows),
        "relevance_grade_counts": dict(sorted(Counter(grades).items())),
    }
    precision: dict[int, float] = {}
    for cutoff in policy.precision_cutoffs:
        top = rows[:cutoff]
        relevant = sum(
            int(row["relevance"]) >= policy.relevance_threshold for row in top
        )
        value = relevant / cutoff
        precision[cutoff] = value
        report[f"precision_at_{cutoff}"] = round(value, 4)
        report[f"precision_at_{cutoff}_sample_size"] = len(top)
    query_ndcg = _ndcg(grades, policy.ndcg_cutoff)
    report[f"ndcg_at_{policy.ndcg_cutoff}"] = query_ndcg
    return report, precision, query_ndcg


def _per_query_metrics(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
) -> tuple[dict[str, dict[str, Any]], dict[int, list[float]], list[float]]:
    reports: dict[str, dict[str, Any]] = {}
    precision_values = {
        cutoff: [] for cutoff in context.policy.precision_cutoffs
    }
    ndcg_values: list[float] = []
    for query_id in context.expected_query_ids:
        report, precision, ndcg = _query_metric_report(
            resolution.final_labels_by_query[query_id],
            policy=context.policy,
        )
        reports[query_id] = report
        for cutoff, value in precision.items():
            precision_values[cutoff].append(value)
        ndcg_values.append(ndcg)
    return reports, precision_values, ndcg_values


def _precision_aggregate(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    *,
    cutoff: int,
    values: Sequence[float],
) -> dict[str, Any]:
    relevant = sum(
        int(row["relevance"]) >= context.policy.relevance_threshold
        for rows in resolution.final_labels_by_query.values()
        for row in rows[:cutoff]
    )
    candidate_count = len(context.expected_query_ids) * cutoff
    return {
        "macro_mean": round(sum(values) / len(values), 4),
        "query_level_ci95": _bootstrap_mean_interval(
            values,
            iterations=context.policy.bootstrap_iterations,
            seed=context.policy.bootstrap_seed + cutoff,
        ),
        "micro_rate": _ratio(relevant, candidate_count),
        "candidate_level_ci95": _wilson_interval(relevant, candidate_count),
        "query_sample_size": len(values),
        "candidate_sample_size": candidate_count,
    }


def _aggregate_metrics(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    precision_values: Mapping[int, Sequence[float]],
    ndcg_values: Sequence[float],
) -> dict[str, Any]:
    aggregate: dict[str, Any] = {
        "query_sample_size": len(context.expected_query_ids),
        "candidate_sample_size": len(resolution.final_labels),
    }
    for cutoff, values in precision_values.items():
        aggregate[f"precision_at_{cutoff}"] = _precision_aggregate(
            context,
            resolution,
            cutoff=cutoff,
            values=values,
        )
    cutoff = context.policy.ndcg_cutoff
    aggregate[f"ndcg_at_{cutoff}"] = {
        "macro_mean": round(sum(ndcg_values) / len(ndcg_values), 4),
        "query_level_ci95": _bootstrap_mean_interval(
            ndcg_values,
            iterations=context.policy.bootstrap_iterations,
            seed=context.policy.bootstrap_seed + cutoff,
        ),
        "query_sample_size": len(ndcg_values),
    }
    return aggregate


def _tier_metrics(
    rows: Sequence[dict[str, Any]],
    *,
    threshold: int,
) -> dict[str, Any]:
    reports = {
        tier: _tier_report(
            [row for row in rows if row["match_tier"] == tier],
            threshold=threshold,
        )
        for tier in RETRIEVAL_TIERS
    }
    unknown_tiers = sorted(
        {row["match_tier"] for row in rows} - set(RETRIEVAL_TIERS)
    )
    if unknown_tiers:
        reports["other"] = _tier_report(
            [row for row in rows if row["match_tier"] in unknown_tiers],
            threshold=threshold,
        )
    return reports


def _evaluated_metrics(
    context: _EvaluationContext,
    resolution: _ResolutionSummary,
    inter_rater: Mapping[str, Any],
) -> dict[str, Any]:
    per_query, precision_values, ndcg_values = _per_query_metrics(
        context,
        resolution,
    )
    threshold = context.policy.relevance_threshold
    return {
        "relevance_threshold": threshold,
        "aggregate": _aggregate_metrics(
            context,
            resolution,
            precision_values,
            ndcg_values,
        ),
        "by_query": per_query,
        "by_match_tier": _tier_metrics(
            resolution.final_labels,
            threshold=threshold,
        ),
        "inter_rater": dict(inter_rater),
        "hard_filter_violation_rate": 0.0,
        "lane_contract_pass_rate": 1.0,
        "overall": _tier_report(
            resolution.final_labels,
            threshold=threshold,
        ),
    }


def _attach_runtime_status(
    report: dict[str, Any],
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    report["runtime_evaluation_status"] = build_runtime_evaluation_status(
        algorithm_version=_text(manifest.get("algorithm_version")),
        code_version=_text(manifest.get("code_version")),
        dataset_snapshot_id=_text(manifest.get("dataset_snapshot_id")),
        filter_policy_version=_text(manifest.get("filter_policy_version")),
        report=report,
    )
    return report


def evaluate_search_relevance(
    labels: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    policy: SearchEvaluationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate a fixed suite only after dual review and adjudication."""

    context = _prepare_evaluation(labels, manifest=manifest, policy=policy)
    resolution = _resolve_reviews(context)
    independent_labelers = sorted(
        {
            _text(row.get("labeler"))
            for row in context.independent_labels
            if row.get("labeler")
        }
    )
    exact_ranks = set(range(1, context.policy.required_candidates_per_query + 1))
    incomplete_queries = _incomplete_queries(context, resolution, exact_ranks)
    blockers = _evaluation_blockers(
        context,
        resolution,
        manifest,
        independent_labelers,
        incomplete_queries,
        exact_ranks,
    )
    inter_rater = _inter_rater_report(context, resolution)
    report = _base_report(
        context,
        resolution,
        manifest=manifest,
        blockers=blockers,
        independent_labelers=independent_labelers,
        incomplete_queries=incomplete_queries,
    )
    if blockers:
        report.update(
            {
                "metrics": None,
                "metrics_not_computed_reason": (
                    "complete_valid_human_labels_required"
                ),
            }
        )
    else:
        report["metrics"] = _evaluated_metrics(
            context,
            resolution,
            inter_rater,
        )
    return _attach_runtime_status(report, manifest=manifest)


__all__ = ["evaluate_search_relevance"]
