from __future__ import annotations

import pytest

from app.domains.kol.analysis_precision_eval import EvaluationPolicy, evaluate_analysis_precision


def _row(index: int, *, gold: bool, prediction, platform: str = "youtube", task: str = "product_fit"):
    return {
        "case_id": f"case-{index}",
        "task": task,
        "platform": platform,
        "gold": gold,
        "prediction": prediction,
        "confidence": 0.8 if prediction is not None else None,
        "review_status": "adjudicated",
        "reviewer_labels": [gold, gold],
    }


def test_empty_labels_never_claim_accuracy():
    report = evaluate_analysis_precision([])

    assert report["claim_status"] == "descriptive_only"
    assert report["evaluation_status"] == "no_labels"
    assert report["accuracy_claimable"] is False
    assert report["overall"]["precision"] is None
    assert report["diagnostics"]["viltrox_fit_score_write"] is False


def test_abstentions_remain_visible_and_reduce_strict_recall():
    rows = [
        _row(1, gold=True, prediction=True),
        _row(2, gold=True, prediction=None),
        _row(3, gold=False, prediction=False),
        _row(4, gold=False, prediction=True),
    ]
    report = evaluate_analysis_precision(
        rows,
        policy=EvaluationPolicy(
            minimum_total=4, minimum_per_task=4, minimum_positive=2, minimum_negative=2,
            required_platforms=(), minimum_kappa_pairs=1,
        ),
    )

    metrics = report["overall"]
    assert metrics["coverage"] == 0.75
    assert metrics["abstention_rate"] == 0.25
    assert metrics["precision"] == 0.5
    assert metrics["strict_recall"] == 0.5
    assert metrics["selective_accuracy"] == pytest.approx(0.6667)
    assert report["accuracy_claimable"] is True


def test_unreviewed_labels_fail_closed_even_when_metrics_are_perfect():
    rows = [_row(i, gold=i % 2 == 0, prediction=i % 2 == 0) for i in range(1, 7)]
    rows[0]["review_status"] = "unreviewed"
    rows[0].pop("reviewer_labels")
    report = evaluate_analysis_precision(
        rows,
        policy=EvaluationPolicy(
            minimum_total=6, minimum_per_task=6, minimum_positive=3, minimum_negative=3,
            required_platforms=(), minimum_kappa_pairs=1,
        ),
    )

    assert report["overall"]["selective_accuracy"] == 1.0
    assert report["accuracy_claimable"] is False
    assert "labels_not_human_reviewed" in report["blockers"]


def test_required_platform_shortfall_is_explicit():
    rows = [_row(i, gold=i % 2 == 0, prediction=i % 2 == 0) for i in range(1, 7)]
    report = evaluate_analysis_precision(
        rows,
        policy=EvaluationPolicy(
            minimum_total=6,
            minimum_per_task=6,
            minimum_positive=3,
            minimum_negative=3,
            minimum_per_platform=2,
            required_platforms=("youtube", "instagram"),
            minimum_kappa_pairs=1,
        ),
    )

    assert report["platform_shortfalls"] == {"instagram": 2}
    assert report["accuracy_claimable"] is False


def test_sparse_task_cannot_hide_inside_a_large_aggregate():
    rows = [_row(i, gold=i % 2 == 0, prediction=i % 2 == 0) for i in range(1, 7)]
    rows.append(_row(100, gold=True, prediction=True, task="brand_mention"))
    report = evaluate_analysis_precision(
        rows,
        policy=EvaluationPolicy(
            minimum_total=7,
            minimum_per_task=4,
            minimum_positive=1,
            minimum_negative=1,
            required_platforms=(),
            minimum_kappa_pairs=1,
        ),
    )

    assert report["by_task"]["product_fit"]["accuracy_claimable"] is True
    assert report["by_task"]["brand_mention"]["accuracy_claimable"] is False
    assert "one_or_more_task_evaluation_gates_failed" in report["blockers"]
    assert report["accuracy_claimable"] is False


def test_low_inter_rater_agreement_blocks_accuracy_claim():
    rows = [_row(i, gold=i % 2 == 0, prediction=i % 2 == 0) for i in range(1, 9)]
    for index, row in enumerate(rows):
        row["reviewer_labels"] = [index % 2 == 0, index % 3 == 0]
    report = evaluate_analysis_precision(
        rows,
        policy=EvaluationPolicy(
            minimum_total=8,
            minimum_per_task=8,
            minimum_positive=4,
            minimum_negative=4,
            required_platforms=(),
            minimum_kappa_pairs=8,
            minimum_cohen_kappa=0.7,
        ),
    )

    assert report["agreement"]["pair_count"] == 8
    assert report["agreement"]["cohen_kappa"] < 0.7
    assert "cohen_kappa_below_minimum" in report["blockers"]
    assert report["accuracy_claimable"] is False


def test_duplicate_task_case_is_rejected():
    row = _row(1, gold=True, prediction=True)
    with pytest.raises(ValueError, match="duplicate task/case_id"):
        evaluate_analysis_precision([row, row])
