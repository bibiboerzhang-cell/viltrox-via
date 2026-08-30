from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

from app.domains.kol.search_relevance_eval import (
    DEFAULT_QUERY_SUITE,
    HUMAN_LABEL_SOURCE,
    LABEL_SCHEMA_VERSION,
    QUERY_SOURCE,
    QUERY_SUITE_VERSION,
    build_candidate_manifest,
    build_label_template,
    build_runtime_evaluation_status,
    evaluate_search_relevance,
)


BUILD_CONTEXT = {
    "code_version": "test-source-sha256:fixture-v1",
    "dataset_snapshot_id": "test-db-sha256:fixture-v1",
}


def _fake_search(**kwargs):
    query = kwargs["operator_query_text"]
    offset = next(
        index * 1000
        for index, spec in enumerate(DEFAULT_QUERY_SUITE, start=1)
        if spec.query_text == query
    )
    items = []
    for rank in range(1, 31):
        tier = "strict" if rank <= 15 else "relaxed"
        items.append(
            {
                "kol_pool_id": offset + rank,
                "platform": "youtube" if "YouTube" in query else "instagram",
                "handle": f"candidate-{offset + rank}",
                "display_name": f"Candidate {offset + rank}",
                "profile_url": f"https://example.test/{offset + rank}",
                "followers": 10_000 + rank,
                "match_tier": tier,
                "candidate_bucket": "core_vertical" if rank <= 18 else "expansion",
                "bucket": "reviewer" if rank % 2 else "creator",
                "retrieval_method": "lexical_idf_v1",
                "robust_rank_score": 1 - rank / 100,
                "retrieval_score": 1 - rank / 100,
                "ranking_confidence": {"score": 0.7, "level": "medium"},
                "evidence_quality": {
                    "video_evidence_count": 2,
                    "deep_analysis_count": 1,
                },
                "why_fit": "test evidence",
                "source_fields": {
                    "retrieval_meta": {
                        "matched_terms": ["camera"],
                        "factual_matched_terms": ["camera"],
                    }
                },
            }
        )
    return {
        "items": items,
        "ranking": {"robust_rank_method": "kol_robust_rank_v1"},
        "filters": {"hard_filters_relaxed": False},
        "diagnostics": {
            "final_count": 30,
            "strict_count": 15,
            "relaxed_count": 15,
            "backfill_count": 0,
            "result_contract_satisfied": True,
            "provider_free_initial": kwargs["provider_free"],
            "lane_selection": {"lane_contract_satisfied": True},
        },
    }


def _manifest():
    return build_candidate_manifest(_fake_search, **BUILD_CONTEXT)


def _completed_labels(manifest):
    labels = build_label_template(manifest)
    for row in labels:
        row.update(
            {
                "label_status": "reviewed",
                "label_source": HUMAN_LABEL_SOURCE,
                "labeler": (
                    "human:reviewer-01"
                    if row["review_slot"] == "A"
                    else "human:reviewer-02"
                ),
                "reviewed_at": "2026-08-03T20:00:00Z",
                "unable_to_judge": False,
                "relevance": 3 if row["candidate"]["rank"] <= 5 else 1,
                "vertical_fit": row["candidate"]["rank"] <= 15,
                "evidence_sufficient": row["candidate"]["rank"] % 2 == 0,
                "notes": "human review",
            }
        )
    return labels


def test_fixed_suite_is_deidentified_business_queries_not_gold_truth():
    manifest = _manifest()

    assert len(DEFAULT_QUERY_SUITE) == 10  # 2026-08-31 市场维扩充:6→10
    assert manifest["query_suite_version"] == QUERY_SUITE_VERSION
    assert manifest["query_source"] == QUERY_SOURCE
    assert manifest["truth_status"] == "candidate_export_not_gold_truth"
    assert manifest["claim_status"] == "not_evaluated"
    assert manifest["query_count"] == 10
    assert manifest["candidate_count"] == 300
    assert manifest["candidate_export_complete"] is True
    assert all(query["source"] == QUERY_SOURCE for query in manifest["queries"])
    assert all(query["truth_status"] == "not_gold_truth" for query in manifest["queries"])


def test_export_is_deterministic_and_forces_provider_free_30_candidate_contract():
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return _fake_search(**kwargs)

    first = build_candidate_manifest(search, **BUILD_CONTEXT)
    second = build_candidate_manifest(_fake_search, **BUILD_CONTEXT)

    assert first["manifest_fingerprint"] == second["manifest_fingerprint"]
    assert first["candidates"] == second["candidates"]
    assert len(calls) == 10
    assert all(call["provider_free"] is True for call in calls)
    assert all(call["limit"] == 30 and call["candidate_limit"] == 500 for call in calls)
    assert all(call["operator_query_text"] == call["query_text"] for call in calls)
    assert first["diagnostics"] == {
        "provider_calls": False,
        "llm_calls": False,
        "database_write": False,
    }
    assert all(query["diagnostics"]["integrity_contract_satisfied"] for query in first["queries"])


def test_blank_label_template_is_explicitly_blocked_and_metrics_are_not_computed():
    manifest = _manifest()
    labels = build_label_template(manifest)

    assert len(labels) == 600
    assert labels[0]["schema_version"] == LABEL_SCHEMA_VERSION
    assert labels[0]["label_source"] is None
    assert labels[0]["relevance"] is None

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "not_evaluated"
    assert report["gate_status"] == "blocked"
    assert report["accuracy_claimable"] is False
    assert report["offline_relevance_metrics_claimable"] is False
    assert "no_human_labels" in report["blockers"]
    assert report["metrics"] is None
    assert report["label_validation"]["unlabeled_template_count"] == 600


def test_runtime_status_never_exposes_metrics_before_human_evaluation():
    status = build_runtime_evaluation_status(algorithm_version="rank-v1")

    assert status["state"] == "not_evaluated"
    assert status["target_count"] == 600
    assert status["dual_review_target"] == 300
    assert status["labeled_count"] == 0
    assert status["algorithm_version"] == "rank-v1"
    assert status["metrics"] is None


def test_custom_queries_cannot_impersonate_the_official_fixed_suite():
    custom = list(DEFAULT_QUERY_SUITE)
    custom[0] = type(custom[0])(
        query_id=custom[0].query_id,
        category=custom[0].category,
        query_text="arbitrary query",
        filters=custom[0].filters,
    )

    import pytest

    with pytest.raises(ValueError, match="official_query_suite_is_fixed"):
        build_candidate_manifest(_fake_search, queries=custom, **BUILD_CONTEXT)


def test_incomplete_human_labels_do_not_produce_partial_precision():
    manifest = _manifest()
    labels = _completed_labels(manifest)[:-1]

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "not_evaluated"
    assert "independent_review_count_below_required" in report["blockers"]
    assert "one_or_more_candidates_not_dual_reviewed" in report["blockers"]
    assert "one_or_more_queries_incompletely_resolved" in report["blockers"]
    assert report["metrics"] is None


def test_duplicate_missing_and_illegal_label_values_fail_closed():
    manifest = _manifest()
    labels = _completed_labels(manifest)
    labels[0]["relevance"] = 4
    labels[1]["vertical_fit"] = None
    labels[2]["evidence_sufficient"] = "yes"
    labels[3]["labeler"] = ""
    labels.append(deepcopy(labels[4]))

    report = evaluate_search_relevance(labels, manifest=manifest)
    issue_counts = report["label_validation"]["issue_counts"]

    assert report["evaluation_status"] == "not_evaluated"
    assert report["metrics"] is None
    assert issue_counts["relevance_must_be_integer_0_to_3"] == 1
    assert issue_counts["vertical_fit_must_be_boolean"] == 1
    assert issue_counts["evidence_sufficient_must_be_boolean"] == 1
    assert issue_counts["missing_labeler"] == 1
    assert issue_counts["duplicate_candidate_review_slot"] == 1


def test_non_human_label_provenance_is_rejected():
    manifest = _manifest()
    labels = _completed_labels(manifest)
    labels[0]["labeler"] = "human:gpt-5-reviewer"
    labels[1]["label_source"] = "model_generated"

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "not_evaluated"
    assert report["metrics"] is None
    assert report["label_validation"]["issue_counts"] == {
        "label_source_not_human_review": 1,
        "non_human_labeler_forbidden": 1,
    }


def test_manifest_tampering_is_detected_before_metrics():
    manifest = _manifest()
    labels = _completed_labels(manifest)
    manifest["candidates"][0]["match_tier"] = "backfill"

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "not_evaluated"
    assert report["metrics"] is None
    assert report["label_validation"]["issue_counts"]["manifest_fingerprint_mismatch"] == 1


def test_extra_manifest_candidate_cannot_bypass_exact_six_by_thirty_contract():
    manifest = _manifest()
    labels = _completed_labels(manifest)
    extra = deepcopy(manifest["candidates"][-1])
    extra["candidate_id"] = "extra-candidate"
    extra["rank"] = 31
    manifest["candidates"].append(extra)
    payload = {
        "query_suite_version": manifest["query_suite_version"],
        "query_suite_fingerprint": manifest["query_suite_fingerprint"],
        "evaluation_context": manifest["evaluation_context"],
        "queries": manifest["queries"],
        "candidates": manifest["candidates"],
    }
    from app.domains.kol import search_relevance_eval as evaluator

    manifest["manifest_fingerprint"] = evaluator._fingerprint(payload)
    manifest["candidate_count"] = 181

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "not_evaluated"
    assert report["metrics"] is None
    assert "manifest_candidate_count_not_exact" in report["blockers"]
    assert "manifest_query_rank_contract_not_met" in report["blockers"]


def test_human_fields_without_reviewed_status_are_rejected():
    manifest = _manifest()
    labels = _completed_labels(manifest)
    labels[0]["label_status"] = "draft"

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "not_evaluated"
    assert report["metrics"] is None
    assert report["label_validation"]["issue_counts"]["label_status_not_reviewed"] == 1


def test_failed_provider_and_hard_filter_contracts_block_candidate_export_and_metrics():
    def unsafe_search(**kwargs):
        result = _fake_search(**kwargs)
        result["diagnostics"]["provider_free_initial"] = False
        result["diagnostics"]["result_contract_satisfied"] = False
        result["filters"]["hard_filters_relaxed"] = True
        return result

    manifest = build_candidate_manifest(unsafe_search, **BUILD_CONTEXT)
    # A tamperer cannot flip only the convenient summary booleans and recompute
    # the fingerprint: evaluation independently rechecks every primitive field.
    manifest["candidate_export_complete"] = True
    for query in manifest["queries"]:
        query["diagnostics"]["integrity_contract_satisfied"] = True
    from app.domains.kol import search_relevance_eval as evaluator

    manifest["manifest_fingerprint"] = evaluator._fingerprint(
        {
            "query_suite_version": manifest["query_suite_version"],
            "query_suite_fingerprint": manifest["query_suite_fingerprint"],
            "evaluation_context": manifest["evaluation_context"],
            "queries": manifest["queries"],
            "candidates": manifest["candidates"],
        }
    )
    report = evaluate_search_relevance(_completed_labels(manifest), manifest=manifest)

    assert manifest["candidate_export_complete"] is True
    assert report["evaluation_status"] == "not_evaluated"
    assert report["metrics"] is None
    assert "one_or_more_query_integrity_contracts_failed" in report["blockers"]


def test_disagreement_requires_a_third_distinct_human_adjudicator():
    manifest = _manifest()
    labels = _completed_labels(manifest)
    labels[1]["relevance"] = 0

    blocked = evaluate_search_relevance(labels, manifest=manifest)

    assert blocked["evaluation_status"] == "not_evaluated"
    assert "one_or_more_disagreements_unadjudicated" in blocked["blockers"]
    assert blocked["coverage"]["disagreement_candidates"] == 1
    assert blocked["metrics"] is None

    adjudication = deepcopy(labels[0])
    adjudication.update(
        {
            "labeler": "human:adjudicator-01",
            "review_role": "adjudication",
            "review_slot": "adjudication",
            "relevance": 2,
            "notes": "independent human adjudication",
        }
    )
    resolved = evaluate_search_relevance([*labels, adjudication], manifest=manifest)

    assert resolved["evaluation_status"] == "evaluated"
    assert resolved["coverage"]["adjudicated_candidates"] == 1
    assert resolved["metrics"]["inter_rater"]["disagreement_candidates"] == 1


def test_complete_human_labels_compute_rank_metrics_strata_ci_and_sample_sizes():
    manifest = _manifest()
    labels = _completed_labels(manifest)

    report = evaluate_search_relevance(labels, manifest=manifest)

    assert report["evaluation_status"] == "evaluated"
    assert report["gate_status"] == "passed"
    assert report["offline_relevance_metrics_claimable"] is True
    assert report["accuracy_claimable"] is False
    assert report["business_outcome_claimable"] is False
    metrics = report["metrics"]
    assert metrics["relevance_threshold"] == 2
    assert metrics["aggregate"]["query_sample_size"] == 10
    assert metrics["aggregate"]["candidate_sample_size"] == 300
    assert metrics["aggregate"]["precision_at_10"]["macro_mean"] == 0.5
    assert metrics["aggregate"]["precision_at_10"]["candidate_sample_size"] == 100
    assert metrics["aggregate"]["precision_at_30"]["macro_mean"] == 0.1667
    assert metrics["aggregate"]["precision_at_30"]["candidate_sample_size"] == 300
    assert metrics["aggregate"]["ndcg_at_30"]["query_sample_size"] == 10
    assert metrics["aggregate"]["ndcg_at_30"]["query_level_ci95"]["low"] is not None
    assert metrics["by_match_tier"]["strict"]["sample_size"] == 150
    assert metrics["by_match_tier"]["relaxed"]["sample_size"] == 150
    assert metrics["by_match_tier"]["backfill"]["sample_size"] == 0
    assert metrics["by_match_tier"]["strict"]["relevance_hit_rate_ci95"]["sample_size"] == 150
    assert set(metrics["by_query"]) == {query.query_id for query in DEFAULT_QUERY_SUITE}
    assert report["coverage"]["distinct_human_labelers"] == 2
    assert report["coverage"]["dual_reviewed_candidates"] == 300
    assert metrics["inter_rater"]["value"] == 1.0
    runtime = report["runtime_evaluation_status"]
    assert runtime["state"] == "shareable"
    assert runtime["metrics"]["precision_at_30"] == 0.1667
    assert runtime["metrics"]["evidence_support_rate"] == 0.5
    assert runtime["metrics"]["hard_filter_violation_rate"] == 0.0
    assert runtime["metrics"]["cohen_kappa"] == 1.0
    stale = build_runtime_evaluation_status(
        algorithm_version="kol_robust_rank_v2",
        code_version=manifest["code_version"],
        dataset_snapshot_id=manifest["dataset_snapshot_id"],
        filter_policy_version=manifest["filter_policy_version"],
        report=report,
    )
    assert stale["state"] == "stale"
    assert stale["metrics"] is None


def test_label_input_order_does_not_change_ranked_metrics_or_blocker_order():
    manifest = _manifest()
    labels = _completed_labels(manifest)

    ordered = evaluate_search_relevance(labels, manifest=manifest)
    reversed_input = evaluate_search_relevance(
        list(reversed(labels)),
        manifest=manifest,
    )

    assert reversed_input == ordered
    assert ordered["metrics"]["aggregate"]["precision_at_10"][
        "candidate_sample_size"
    ] == 100
    assert ordered["metrics"]["aggregate"]["precision_at_30"][
        "candidate_sample_size"
    ] == 300


def test_profile_facets_remain_metadata_and_do_not_shrink_offline_denominators():
    countries = ("US", "GB", "PH")
    follower_bands = (1_500, 75_000, 2_500_000)

    def faceted_search(**kwargs):
        result = _fake_search(**kwargs)
        for index, item in enumerate(result["items"]):
            item["country"] = countries[index % len(countries)]
            item["language"] = "en"
            item["followers"] = follower_bands[index % len(follower_bands)]
        return result

    manifest = build_candidate_manifest(faceted_search, **BUILD_CONTEXT)
    report = evaluate_search_relevance(
        _completed_labels(manifest),
        manifest=manifest,
    )

    assert report["evaluation_status"] == "evaluated"
    assert report["metrics"]["aggregate"]["candidate_sample_size"] == 300
    assert {row["country"] for row in manifest["candidates"]} == set(countries)
    assert {row["followers"] for row in manifest["candidates"]} == set(
        follower_bands
    )
    assert {row["platform"] for row in manifest["candidates"]} == {
        "instagram",
        "youtube",
    }
    assert any(row["filters"] for row in manifest["queries"])
    assert {
        "product_scene_platform",
        "product_user_fit",
        "content_format_platform",
        "product_scene_fit",
    }.issubset({row["category"] for row in manifest["queries"]})


def test_cli_evaluate_runs_without_caller_pythonpath(tmp_path):
    root = Path(__file__).resolve().parents[1]
    labels_path = tmp_path / "labels.json"
    manifest_path = tmp_path / "manifest.json"
    labels_path.write_text("[]", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "eval_kol_search_relevance.py"),
            "evaluate",
            str(labels_path),
            "--manifest",
            str(manifest_path),
        ],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 4, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["evaluation_status"] == "not_evaluated"
    assert "no_human_labels" in payload["blockers"]
    assert "error" not in payload


def test_cli_source_version_covers_split_metrics_implementation(tmp_path):
    root = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(root / "scripts" / "eval_kol_search_relevance.py"))
    source_files = namespace["SOURCE_VERSION_FILES"]
    source_version = namespace["_source_code_version"]
    source_version.__globals__["ROOT"] = tmp_path
    for relative in source_files:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    before = source_version()
    metrics_path = tmp_path / "backend/app/domains/kol/search_relevance_metrics.py"
    metrics_path.write_text("changed metrics implementation", encoding="utf-8")
    after_metrics_change = source_version()
    metrics_path.write_text(
        "backend/app/domains/kol/search_relevance_metrics.py",
        encoding="utf-8",
    )
    statistics_path = (
        tmp_path / "backend/app/domains/kol/search_relevance_statistics.py"
    )
    statistics_path.write_text("changed statistical formula", encoding="utf-8")
    after_statistics_change = source_version()

    assert {
        "backend/app/domains/kol/profile_recall_contract.py",
        "backend/app/domains/kol/profile_recall_projection.py",
        "backend/app/domains/kol/profile_recall_relevance.py",
        "backend/app/domains/kol/profile_recall_storage.py",
        "backend/app/domains/kol/search_relevance_metrics.py",
        "backend/app/domains/kol/search_relevance_statistics.py",
    }.issubset(source_files)
    assert before != after_metrics_change
    assert before != after_statistics_change
