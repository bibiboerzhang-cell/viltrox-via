"""Human-label-only evaluation contract for KOL search relevance.

This module deliberately separates retrieval scores from measured relevance.
Candidate exports are *labeling manifests*, not gold truth. Metrics remain
blocked until every top-30 candidate in the fixed six-query suite has two
independent human reviews and all disagreements have third-human adjudication.

The implementation is provider-free and contains no database writes.  The
caller supplies a local search function for export; evaluation itself is pure.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence


MANIFEST_SCHEMA_VERSION = "kol_search_candidate_manifest_v1"
LABEL_SCHEMA_VERSION = "kol_search_gold_label_v2"
EVALUATION_SCHEMA_VERSION = "kol_search_relevance_eval_v1"
QUERY_SUITE_VERSION = "kol_search_business_queries_v1"
QUERY_SOURCE = "deidentified_recent_business_queries"
HUMAN_LABEL_SOURCE = "human_review"
FILTER_POLICY_VERSION = "kol_search_hard_filters_and_lanes_v1"
RETRIEVAL_TIERS = ("strict", "relaxed", "backfill")
PROHIBITED_LABELERS = frozenset(
    {
        "ai",
        "auto",
        "automation",
        "chatgpt",
        "claude",
        "fixture",
        "gemini",
        "gpt",
        "llm",
        "machine",
        "model",
        "synthetic",
        "test",
    }
)


@dataclass(frozen=True)
class SearchEvalQuery:
    query_id: str
    category: str
    query_text: str
    filters: Mapping[str, Any]


# These are de-identified recent business intents supplied by the operator.
# They are candidates for human judgment, never relevance labels themselves.
DEFAULT_QUERY_SUITE: tuple[SearchEvalQuery, ...] = (
    SearchEvalQuery(
        query_id="q01_26mm_evo_fit",
        category="product_fit",
        query_text="26mm EVO 适配 KOL",
        filters={},
    ),
    SearchEvalQuery(
        query_id="q02_35mm_low_light_portrait_youtube",
        category="product_scene_platform",
        query_text="35mm 低光人像 YouTube 摄影师",
        filters={"platforms": ["youtube"]},
    ),
    SearchEvalQuery(
        query_id="q03_z1_flash",
        category="product_category_fit",
        query_text="Z1 闪光灯 KOL",
        filters={},
    ),
    SearchEvalQuery(
        query_id="q04_viltrox_monitor_user",
        category="product_user_fit",
        query_text="Viltrox 监视器用户",
        filters={},
    ),
    SearchEvalQuery(
        query_id="q05_evo_tutorial_youtube",
        category="content_format_platform",
        query_text="EVO 教程类 YouTube 博主",
        filters={"platforms": ["youtube"]},
    ),
    SearchEvalQuery(
        query_id="q06_85mm_portrait_wedding",
        category="product_scene_fit",
        query_text="85mm 人像镜头婚礼摄影师",
        filters={},
    ),
    # 2026-08-31 市场维扩充(L3 正门):套件从 6 条扩到 10 条 → 10×30=300 双审候选,
    # 同时补齐用户 L3 标尺的「3+ 市场」维(此前 6 条零市场维)。四条同为去标识化的
    # 真实业务意图;开标注前本套件随 fingerprint 冻结,此后改一字 = 全部标注作废。
    SearchEvalQuery(
        query_id="q07_us_135mm_portrait",
        category="market_product_fit",
        query_text="美国市场 135mm 人像摄影 KOL",
        filters={"market": "US"},
    ),
    SearchEvalQuery(
        query_id="q08_eu_evo_review",
        category="market_product_fit",
        query_text="欧洲市场 EVO 系列评测博主",
        filters={"market": "EU"},
    ),
    SearchEvalQuery(
        query_id="q09_jp_zmount_creator",
        category="market_mount_fit",
        query_text="日本市场 Z 卡口镜头创作者",
        filters={"market": "JP"},
    ),
    SearchEvalQuery(
        query_id="q10_sea_budget_lens_youtube",
        category="market_scene_platform",
        query_text="东南亚市场入门级镜头 YouTube 博主",
        filters={"market": "SEA", "platforms": ["youtube"]},
    ),
)


@dataclass(frozen=True)
class SearchEvaluationPolicy:
    required_query_count: int = 10
    required_candidates_per_query: int = 30
    required_independent_reviews_per_candidate: int = 2
    precision_cutoffs: tuple[int, ...] = (10, 30)
    ndcg_cutoff: int = 30
    relevance_threshold: int = 2
    minimum_distinct_labelers: int = 2
    bootstrap_iterations: int = 4000
    bootstrap_seed: int = 1729


def build_runtime_evaluation_status(
    *,
    algorithm_version: str,
    code_version: str = "",
    dataset_snapshot_id: str = "",
    filter_policy_version: str = FILTER_POLICY_VERSION,
    report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the current runtime honestly before human labels exist.

    The live search response needs an explicit status so older clients do not
    mistake retrieval scores for measured quality.  A candidate manifest is
    still only a labeling input; until completed human labels are evaluated,
    no metric is exposed here.
    """

    policy = SearchEvaluationPolicy()
    evaluation = dict(report) if isinstance(report, Mapping) else {}
    coverage = dict(evaluation.get("coverage")) if isinstance(evaluation.get("coverage"), Mapping) else {}
    valid_labels = int(coverage.get("valid_independent_reviews") or 0)
    dual_reviewed = int(coverage.get("dual_reviewed_candidates") or 0)
    state = "not_evaluated"
    flat_metrics: dict[str, Any] | None = None
    evaluated_report = (
        evaluation.get("evaluation_status") == "evaluated"
        and evaluation.get("gate_status") == "passed"
        and evaluation.get("offline_relevance_metrics_claimable") is True
        and isinstance(evaluation.get("metrics"), Mapping)
    )
    current_context = {
        "algorithm_version": _text(algorithm_version),
        "code_version": _text(code_version),
        "dataset_snapshot_id": _text(dataset_snapshot_id),
        "filter_policy_version": _text(filter_policy_version),
    }
    report_context = {key: _text(evaluation.get(key)) for key in current_context}
    context_matches = all(
        current_context[key] and current_context[key] == report_context[key]
        for key in current_context
    )
    if evaluated_report and not context_matches:
        state = "stale"
    elif evaluated_report:
        state = "shareable"
        metrics = dict(evaluation["metrics"])
        aggregate = dict(metrics.get("aggregate")) if isinstance(metrics.get("aggregate"), Mapping) else {}
        overall = dict(metrics.get("overall")) if isinstance(metrics.get("overall"), Mapping) else {}
        flat_metrics = {}
        for cutoff in policy.precision_cutoffs:
            value = aggregate.get(f"precision_at_{cutoff}")
            if isinstance(value, Mapping) and value.get("macro_mean") is not None:
                flat_metrics[f"precision_at_{cutoff}"] = value.get("macro_mean")
        ndcg = aggregate.get(f"ndcg_at_{policy.ndcg_cutoff}")
        if isinstance(ndcg, Mapping) and ndcg.get("macro_mean") is not None:
            flat_metrics[f"ndcg_at_{policy.ndcg_cutoff}"] = ndcg.get("macro_mean")
        if overall.get("evidence_sufficient_rate") is not None:
            flat_metrics["evidence_support_rate"] = overall.get("evidence_sufficient_rate")
        if metrics.get("hard_filter_violation_rate") is not None:
            flat_metrics["hard_filter_violation_rate"] = metrics.get("hard_filter_violation_rate")
        if metrics.get("lane_contract_pass_rate") is not None:
            flat_metrics["lane_contract_pass_rate"] = metrics.get("lane_contract_pass_rate")
        inter_rater = metrics.get("inter_rater")
        if isinstance(inter_rater, Mapping) and inter_rater.get("value") is not None:
            flat_metrics["cohen_kappa"] = inter_rater.get("value")
    elif valid_labels > 0:
        state = "labeling"

    return {
        "state": state,
        "evaluation_contract": EVALUATION_SCHEMA_VERSION,
        "gold_set_id": (
            _text(evaluation.get("manifest_fingerprint")) or None
            if state == "shareable"
            else None
        ),
        "dataset_version": _text(evaluation.get("query_suite_version")) or QUERY_SUITE_VERSION,
        "algorithm_version": _text(algorithm_version) or None,
        "code_version": _text(code_version) or None,
        "dataset_snapshot_id": _text(dataset_snapshot_id) or None,
        "filter_policy_version": _text(filter_policy_version) or None,
        "target_count": (
            policy.required_query_count
            * policy.required_candidates_per_query
            * policy.required_independent_reviews_per_candidate
        ),
        "labeled_count": valid_labels,
        "dual_review_target": policy.required_query_count * policy.required_candidates_per_query,
        "dual_reviewed_count": dual_reviewed,
        "disagreement_count": int(coverage.get("disagreement_candidates") or 0),
        "claim_status": (
            "offline_human_label_evaluation_only" if state == "shareable" else "not_evaluated"
        ),
        "metrics": flat_metrics,
        "note": (
            "固定 10 类 Top-30 真人标注已通过离线相关性闸；不代表业务结果或线上预测准确率。"
            if state == "shareable"
            else "算法、过滤规则、代码或数据快照已变化；历史评测不可继续发布。"
            if state == "stale"
            else "检索排序分不是准确率；完成固定 10 类 Top-30 真人标注后才发布离线相关性指标。"
        ),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _query_suite_payload(queries: Sequence[SearchEvalQuery]) -> list[dict[str, Any]]:
    return [
        {
            "query_id": query.query_id,
            "category": query.category,
            "query_text": query.query_text,
            "filters": dict(query.filters),
        }
        for query in queries
    ]


def _manifest_query_suite_payload(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in manifest.get("queries") or []:
        if not isinstance(raw, Mapping):
            continue
        output.append(
            {
                "query_id": _text(raw.get("query_id")),
                "category": _text(raw.get("category")),
                "query_text": _text(raw.get("query_text")),
                "filters": dict(raw.get("filters")) if isinstance(raw.get("filters"), Mapping) else {},
            }
        )
    return output


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 6) if math.isfinite(parsed) else None


def _candidate_id(item: Mapping[str, Any]) -> str:
    raw = item.get("kol_pool_id") or item.get("candidate_id") or item.get("id")
    return _text(raw)


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _candidate_export_row(
    query: SearchEvalQuery,
    item: Mapping[str, Any],
    *,
    rank: int,
) -> dict[str, Any]:
    source_fields = _safe_mapping(item.get("source_fields"))
    retrieval_meta = _safe_mapping(source_fields.get("retrieval_meta"))
    evidence_quality = _safe_mapping(item.get("evidence_quality"))
    ranking_confidence = _safe_mapping(item.get("ranking_confidence"))
    representative = item.get("representative_evidence")
    if not isinstance(representative, list):
        representative = []
    return {
        "query_id": query.query_id,
        "query_text": query.query_text,
        "query_category": query.category,
        "rank": int(rank),
        "candidate_id": _candidate_id(item),
        "platform": _text(item.get("platform")).lower(),
        "handle": _text(item.get("handle")),
        "display_name": _text(item.get("display_name")),
        "profile_url": _text(item.get("profile_url")),
        "country": _text(item.get("country")),
        "language": _text(item.get("language")),
        "followers": item.get("followers"),
        "primary_topic": _text(item.get("primary_topic")),
        "bio": _text(item.get("bio")),
        "match_tier": _text(item.get("match_tier")).lower() or "backfill",
        "candidate_bucket": _text(item.get("candidate_bucket")),
        "profile_bucket": _text(item.get("bucket")),
        "retrieval_method": _text(item.get("retrieval_method")),
        "robust_rank_score": _optional_float(item.get("robust_rank_score")),
        "retrieval_score": _optional_float(item.get("retrieval_score")),
        "ranking_confidence": {
            "level": _text(ranking_confidence.get("level")),
            "score": _optional_float(ranking_confidence.get("score")),
            "missing_signals": list(ranking_confidence.get("missing_signals") or []),
        },
        "evidence_quality": {
            "level": _text(evidence_quality.get("level")),
            "coverage": _optional_float(evidence_quality.get("coverage")),
            "video_evidence_count": int(evidence_quality.get("video_evidence_count") or 0),
            "deep_analysis_count": int(evidence_quality.get("deep_analysis_count") or 0),
            "missing_signals": list(evidence_quality.get("missing_signals") or []),
        },
        "why_fit": _text(item.get("why_fit")),
        "recall_reason": _text(item.get("recall_reason")),
        "matched_terms": list(retrieval_meta.get("matched_terms") or []),
        "factual_matched_terms": list(retrieval_meta.get("factual_matched_terms") or []),
        "representative_evidence": representative[:3],
    }


def _query_diagnostics(
    result: Mapping[str, Any],
    *,
    expected: int,
    exported_count: int,
) -> dict[str, Any]:
    diagnostics = _safe_mapping(result.get("diagnostics"))
    ranking = _safe_mapping(result.get("ranking"))
    lane_selection = _safe_mapping(diagnostics.get("lane_selection"))
    reported_final_count = int(diagnostics.get("final_count") or 0)
    hard_filters_relaxed = _safe_mapping(result.get("filters")).get("hard_filters_relaxed")
    provider_free = diagnostics.get("provider_free_initial") is True
    algorithm_version = _text(
        ranking.get("robust_rank_method") or ranking.get("version")
    )
    count_exact = exported_count == int(expected)
    reported_count_consistent = reported_final_count == exported_count
    reported_contract_passed = diagnostics.get("result_contract_satisfied") is True
    integrity_contract_satisfied = (
        count_exact
        and reported_count_consistent
        and reported_contract_passed
        and provider_free
        and hard_filters_relaxed is False
        and lane_selection.get("lane_contract_satisfied") is True
        and bool(algorithm_version)
    )
    return {
        "expected_candidates": int(expected),
        "exported_candidates": exported_count,
        "reported_final_count": reported_final_count,
        "shortfall": max(0, int(expected) - exported_count),
        "strict_count": int(diagnostics.get("strict_count") or 0),
        "relaxed_count": int(diagnostics.get("relaxed_count") or 0),
        "backfill_count": int(diagnostics.get("backfill_count") or 0),
        "result_count_contract_satisfied": count_exact,
        "reported_count_consistent": reported_count_consistent,
        "reported_result_contract_satisfied": reported_contract_passed,
        "lane_contract_satisfied": bool(lane_selection.get("lane_contract_satisfied", False)),
        "provider_free": provider_free,
        "hard_filters_relaxed": hard_filters_relaxed,
        "algorithm_version": algorithm_version,
        "integrity_contract_satisfied": integrity_contract_satisfied,
    }


def _diagnostic_integrity_valid(value: Any, *, expected: int) -> bool:
    diagnostics = _safe_mapping(value)
    return (
        diagnostics.get("expected_candidates") == expected
        and diagnostics.get("exported_candidates") == expected
        and diagnostics.get("reported_final_count") == expected
        and diagnostics.get("shortfall") == 0
        and diagnostics.get("result_count_contract_satisfied") is True
        and diagnostics.get("reported_count_consistent") is True
        and diagnostics.get("reported_result_contract_satisfied") is True
        and diagnostics.get("lane_contract_satisfied") is True
        and diagnostics.get("provider_free") is True
        and diagnostics.get("hard_filters_relaxed") is False
        and bool(_text(diagnostics.get("algorithm_version")))
    )


def build_candidate_manifest(
    search: Callable[..., Mapping[str, Any]],
    *,
    queries: Sequence[SearchEvalQuery] = DEFAULT_QUERY_SUITE,
    candidates_per_query: int = 30,
    code_version: str,
    dataset_snapshot_id: str,
) -> dict[str, Any]:
    """Run a fixed provider-free suite and return a deterministic label manifest.

    ``search`` must be a local read-only callable compatible with
    ``recall_kol_profiles``.  The manifest intentionally has no generated-at
    timestamp, so the same database snapshot and code produce the same
    fingerprint.
    """

    safe_count = max(1, min(30, int(candidates_per_query or 30)))
    if _query_suite_payload(queries) != _query_suite_payload(DEFAULT_QUERY_SUITE):
        raise ValueError("official_query_suite_is_fixed")
    safe_code_version = _text(code_version)
    safe_snapshot_id = _text(dataset_snapshot_id)
    if not safe_code_version or not safe_snapshot_id:
        raise ValueError("code_version_and_dataset_snapshot_id_required")
    query_suite_fingerprint = _fingerprint(_query_suite_payload(DEFAULT_QUERY_SUITE))
    query_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen_query_ids: set[str] = set()
    for query in queries:
        if query.query_id in seen_query_ids:
            raise ValueError(f"duplicate query_id: {query.query_id}")
        seen_query_ids.add(query.query_id)
        result = dict(
            search(
                query_text=query.query_text,
                operator_query_text=query.query_text,
                candidate_limit=500,
                limit=safe_count,
                creator_quota=15,
                reviewer_quota=15,
                provider_free=True,
                filters=dict(query.filters),
                search_strategy="balanced",
                allow_backfill=True,
            )
        )
        raw_items = list(result.get("items") or [])[:safe_count]
        exported = [
            _candidate_export_row(query, item, rank=rank)
            for rank, item in enumerate(raw_items, start=1)
        ]
        candidate_ids = [row["candidate_id"] for row in exported]
        if any(not candidate_id for candidate_id in candidate_ids):
            raise ValueError(f"query {query.query_id} returned a candidate without id")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"query {query.query_id} returned duplicate candidate ids")
        candidates.extend(exported)
        query_rows.append(
            {
                "query_id": query.query_id,
                "category": query.category,
                "query_text": query.query_text,
                "filters": dict(query.filters),
                "source": QUERY_SOURCE,
                "truth_status": "not_gold_truth",
                "diagnostics": _query_diagnostics(
                    result,
                    expected=safe_count,
                    exported_count=len(exported),
                ),
            }
        )

    algorithm_versions = {
        _text(row["diagnostics"].get("algorithm_version"))
        for row in query_rows
        if _text(row["diagnostics"].get("algorithm_version"))
    }
    algorithm_version = next(iter(algorithm_versions)) if len(algorithm_versions) == 1 else ""
    evaluation_context = {
        "algorithm_version": algorithm_version,
        "filter_policy_version": FILTER_POLICY_VERSION,
        "code_version": safe_code_version,
        "dataset_snapshot_id": safe_snapshot_id,
    }
    candidate_payload = {
        "query_suite_version": QUERY_SUITE_VERSION,
        "query_suite_fingerprint": query_suite_fingerprint,
        "evaluation_context": evaluation_context,
        "queries": query_rows,
        "candidates": candidates,
    }
    complete = (
        len(query_rows) == len(queries)
        and len(candidates) == len(queries) * safe_count
        and all(
            _diagnostic_integrity_valid(row["diagnostics"], expected=safe_count)
            for row in query_rows
        )
        and bool(algorithm_version)
    )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "query_suite_version": QUERY_SUITE_VERSION,
        "query_suite_fingerprint": query_suite_fingerprint,
        "algorithm_version": algorithm_version,
        "filter_policy_version": FILTER_POLICY_VERSION,
        "code_version": safe_code_version,
        "dataset_snapshot_id": safe_snapshot_id,
        "evaluation_context": evaluation_context,
        "query_source": QUERY_SOURCE,
        "truth_status": "candidate_export_not_gold_truth",
        "label_status": "unlabeled",
        "claim_status": "not_evaluated",
        "query_count": len(query_rows),
        "candidates_per_query": safe_count,
        "candidate_count": len(candidates),
        "candidate_export_complete": complete,
        "determinism_contract": {
            "fixed_query_order": True,
            "fixed_candidate_order": True,
            "provider_free": True,
            "same_database_snapshot_required": True,
            "generated_timestamp_excluded_from_fingerprint": True,
        },
        "diagnostics": {
            "provider_calls": False,
            "llm_calls": False,
            "database_write": False,
        },
        "manifest_fingerprint": _fingerprint(candidate_payload),
        "queries": query_rows,
        "candidates": candidates,
    }


def build_label_template(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return two independent blank review slots for every candidate."""

    suite_version = _text(manifest.get("query_suite_version"))
    query_by_id = {
        _text(row.get("query_id")): row
        for row in manifest.get("queries") or []
        if isinstance(row, Mapping)
    }
    template: list[dict[str, Any]] = []
    for row in manifest.get("candidates") or []:
        if not isinstance(row, Mapping):
            continue
        query_id = _text(row.get("query_id"))
        query = query_by_id.get(query_id) or {}
        for review_slot in ("A", "B"):
            template.append(
                {
                    "schema_version": LABEL_SCHEMA_VERSION,
                    "label_status": "unlabeled_template",
                    "label_source": None,
                    "labeler": None,
                    "reviewed_at": None,
                    "review_role": "independent",
                    "review_slot": review_slot,
                    "query": {
                        "suite_version": suite_version,
                        "id": query_id,
                        "text": _text(query.get("query_text")),
                    },
                    "candidate": {
                        "id": _text(row.get("candidate_id")),
                        "rank": int(row.get("rank") or 0),
                        "match_tier": _text(row.get("match_tier")),
                        "manifest_fingerprint": _text(manifest.get("manifest_fingerprint")),
                    },
                    "unable_to_judge": None,
                    "relevance": None,
                    "vertical_fit": None,
                    "evidence_sufficient": None,
                    "notes": "",
                }
            )
    return template


def _issue(code: str, *, row: int | None = None, detail: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code}
    if row is not None:
        payload["row"] = row
    if detail:
        payload["detail"] = detail
    return payload


def _manifest_index(
    manifest: Mapping[str, Any],
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if _text(manifest.get("schema_version")) != MANIFEST_SCHEMA_VERSION:
        issues.append(_issue("invalid_manifest_schema_version"))
    if _text(manifest.get("query_suite_version")) != QUERY_SUITE_VERSION:
        issues.append(_issue("invalid_query_suite_version"))
    fingerprint_payload = {
        "query_suite_version": manifest.get("query_suite_version"),
        "query_suite_fingerprint": manifest.get("query_suite_fingerprint"),
        "evaluation_context": manifest.get("evaluation_context") or {},
        "queries": manifest.get("queries") or [],
        "candidates": manifest.get("candidates") or [],
    }
    if _text(manifest.get("manifest_fingerprint")) != _fingerprint(fingerprint_payload):
        issues.append(_issue("manifest_fingerprint_mismatch"))
    expected_suite = _query_suite_payload(DEFAULT_QUERY_SUITE)
    expected_suite_fingerprint = _fingerprint(expected_suite)
    if _text(manifest.get("query_suite_fingerprint")) != expected_suite_fingerprint:
        issues.append(_issue("official_query_suite_fingerprint_mismatch"))
    if _manifest_query_suite_payload(manifest) != expected_suite:
        issues.append(_issue("official_query_suite_mismatch"))
    context = _safe_mapping(manifest.get("evaluation_context"))
    for field in ("algorithm_version", "code_version", "dataset_snapshot_id"):
        if not _text(context.get(field)) or _text(manifest.get(field)) != _text(context.get(field)):
            issues.append(_issue(f"evaluation_context_{field}_invalid"))
    if context.get("filter_policy_version") != FILTER_POLICY_VERSION:
        issues.append(_issue("evaluation_context_filter_policy_version_invalid"))
    if manifest.get("filter_policy_version") != FILTER_POLICY_VERSION:
        issues.append(_issue("manifest_filter_policy_version_invalid"))
    raw_queries = [row for row in manifest.get("queries") or [] if isinstance(row, Mapping)]
    query_ids = [_text(row.get("query_id")) for row in raw_queries]
    if len(query_ids) != len(set(query_ids)):
        issues.append(_issue("duplicate_manifest_query_id"))
    if int(manifest.get("query_count") or 0) != len(raw_queries):
        issues.append(_issue("manifest_query_count_mismatch"))

    raw_candidates = list(manifest.get("candidates") or [])
    if int(manifest.get("candidate_count") or 0) != len(raw_candidates):
        issues.append(_issue("manifest_candidate_count_mismatch"))

    index: dict[tuple[str, str], dict[str, Any]] = {}
    rank_keys: set[tuple[str, int]] = set()
    for row_number, raw in enumerate(raw_candidates, start=1):
        if not isinstance(raw, Mapping):
            issues.append(_issue("invalid_manifest_candidate", row=row_number))
            continue
        query_id = _text(raw.get("query_id"))
        candidate_id = _text(raw.get("candidate_id"))
        try:
            rank = int(raw.get("rank"))
        except (TypeError, ValueError):
            rank = 0
        key = (query_id, candidate_id)
        rank_key = (query_id, rank)
        if not query_id or not candidate_id or rank <= 0:
            issues.append(_issue("manifest_candidate_missing_key", row=row_number))
        elif key in index:
            issues.append(_issue("duplicate_manifest_candidate", row=row_number))
        elif rank_key in rank_keys:
            issues.append(_issue("duplicate_manifest_rank", row=row_number))
        else:
            index[key] = dict(raw)
            rank_keys.add(rank_key)
    return index, issues


def validate_human_labels(
    labels: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate label provenance, domain values, uniqueness, and manifest ties.

    Row-level rules live in :mod:`search_relevance_label_checks` as a check
    registry; each check owns its error codes and the registry order matches
    the historical issue order (imported lazily to avoid a module cycle).
    """

    from app.domains.kol.search_relevance_label_checks import (
        LABEL_ROW_CHECKS,
        build_label_row_context,
        normalized_label_row,
    )

    manifest_index, issues = _manifest_index(manifest)
    normalized: list[dict[str, Any]] = []
    seen_slots: set[tuple[str, str, str, str]] = set()
    unlabeled_templates = 0
    for row_number, raw in enumerate(labels, start=1):
        if not isinstance(raw, Mapping):
            issues.append(_issue("label_row_not_object", row=row_number))
            continue
        if _text(raw.get("label_status")) == "unlabeled_template":
            unlabeled_templates += 1
            continue
        context = build_label_row_context(
            raw,
            manifest=manifest,
            manifest_index=manifest_index,
            seen_slots=seen_slots,
        )
        row_issues = [code for check in LABEL_ROW_CHECKS for code in check(context)]
        if row_issues:
            issues.extend(_issue(code, row=row_number) for code in row_issues)
            continue
        normalized.append(normalized_label_row(context))
    return {
        "valid_labels": normalized,
        "issues": issues,
        "issue_counts": dict(sorted(Counter(issue["code"] for issue in issues).items())),
        "unlabeled_template_count": unlabeled_templates,
        "manifest_candidate_count": len(manifest_index),
    }


def evaluate_search_relevance(
    labels: Iterable[Mapping[str, Any]],
    *,
    manifest: Mapping[str, Any],
    policy: SearchEvaluationPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate the fixed suite through the isolated metrics implementation."""

    from app.domains.kol.search_relevance_metrics import (
        evaluate_search_relevance as _evaluate_search_relevance,
    )

    return _evaluate_search_relevance(
        labels,
        manifest=manifest,
        policy=policy,
    )


__all__ = [
    "DEFAULT_QUERY_SUITE",
    "EVALUATION_SCHEMA_VERSION",
    "FILTER_POLICY_VERSION",
    "HUMAN_LABEL_SOURCE",
    "LABEL_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "QUERY_SOURCE",
    "QUERY_SUITE_VERSION",
    "SearchEvalQuery",
    "SearchEvaluationPolicy",
    "build_candidate_manifest",
    "build_label_template",
    "build_runtime_evaluation_status",
    "evaluate_search_relevance",
    "validate_human_labels",
]
