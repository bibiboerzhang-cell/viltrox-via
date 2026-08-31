"""test_final_v1_quality_eval_characterization(前半;共享层见 _support)。"""
from tests.test_final_v1_quality_eval_characterization_support import (  # noqa: F401
    Any,
    FinalV1QualityInputError,
    GOLDEN_REPORT,
    GOLDEN_REPORT_PASS,
    MODEL,
    PROMPT,
    SHA_A,
    SHA_B,
    SHA_C,
    SHA_D,
    SHA_GHOST,
    _canonical,
    _case_a_expected,
    _case_a_payload,
    _case_b_payload,
    _del_threshold,
    _gold_manifest,
    _gold_manifest_pass,
    _mutation_cases,
    _predictions_manifest,
    _predictions_manifest_pass,
    copy,
    evaluate_final_v1_quality,
    json,
    pytest,
)




def test_mixed_dataset_report_deep_equals_recorded_golden() -> None:
    report = evaluate_final_v1_quality(_gold_manifest(), _predictions_manifest())
    golden = json.loads(GOLDEN_REPORT)
    assert report == golden
    # 类型级锁定(int/float/bool 序列化形态必须一致,守住 6 位小数与整数计数口径)。
    assert _canonical(report) == _canonical(golden)


def test_mixed_dataset_threshold_checks_locked_to_the_digit() -> None:
    report = evaluate_final_v1_quality(_gold_manifest(), _predictions_manifest())
    table = [
        (item["metric"], item["observed"], item["comparator"], item["threshold"], item["passed"])
        for item in report["quality_gate"]["checks"]
    ]
    assert table == [
        ("brand_accuracy_min", 0.5, ">=", 0.5, True),
        ("unknown_as_absent_max", 0, "<=", 0.0, True),
        ("non_title_evidence_recall_min", 1.0, ">=", 1.0, True),
        ("product_precision_min", 0.5, ">=", 0.9, False),
        ("product_recall_min", 1.0, ">=", 1.0, True),
        ("competitor_f1_min", 1.0, ">=", 0.9, True),
        ("evidence_modality_support_min", 0.75, ">=", 0.7, True),
        ("evidence_timestamp_support_min", 0.75, ">=", 0.8, False),
        ("unsupported_absent_max", 1, "<=", 0.0, False),
        ("schema_coverage_min", 0.380952, ">=", 0.5, False),
    ]
    assert report["quality_gate"]["metric_status"] == "fail"
    assert report["quality_gate"]["production_acceptance_eligible"] is False
    assert report["dataset"]["timestamp_tolerance_seconds"] == 1.5
    assert report["input_integrity"]["missing_or_drifted"] == [
        "case-drift:prediction_model_mismatch",
        "case-drift:prediction_prompt_version_mismatch",
        "case-unknown:prediction_missing",
    ]
    assert report["input_integrity"]["unexpected_case_ids"] == ["case-ghost"]


def test_pass_dataset_report_deep_equals_recorded_golden() -> None:
    report = evaluate_final_v1_quality(_gold_manifest_pass(), _predictions_manifest_pass())
    golden = json.loads(GOLDEN_REPORT_PASS)
    assert report == golden
    assert _canonical(report) == _canonical(golden)
    assert report["quality_gate"]["metric_status"] == "pass"
    # 无 tolerance 字段时默认 2.0(判据数字,不许动)。
    assert report["dataset"]["timestamp_tolerance_seconds"] == 2.0
    assert report["accuracy_claim"] == {
        "allowed": False,
        "reason": "offline_framework_does_not_verify_human_adjudication_or_provider_execution",
        "declared_model_invoked": True,
    }


def test_evaluation_never_mutates_inputs() -> None:
    gold = _gold_manifest()
    predictions = _predictions_manifest()
    gold_before = copy.deepcopy(gold)
    predictions_before = copy.deepcopy(predictions)
    evaluate_final_v1_quality(gold, predictions)
    assert gold == gold_before
    assert predictions == predictions_before
