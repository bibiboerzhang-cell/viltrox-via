from __future__ import annotations

import json

from app.services.vkpi import prediction_accuracy_feedback_v0


def _write_calibration(path, *, official: bool, precision: float, coverage: float, sku: str = "AF-14MM-F40-AIR-FE") -> None:
    payload = {
        "passed": True,
        "generated_at": "2026-05-23T11:00:00Z",
        "summary": {
            "calibration_status": "official_cross_day" if official else "same_day_smoke_or_pending",
            "accuracy_official": official,
            "prediction_generated_at": "2026-05-22T11:00:00Z" if official else "2026-05-23T10:00:00Z",
            "truth_generated_at": "2026-05-23T11:00:00Z",
            "day_gap": 1 if official else 0,
            "predicted_candidate_count": 10,
            "truth_platform_count": 6,
            "truth_abnormal_platform_count": 3,
            "hit_count": int(precision * 10),
            "proxy_precision": precision,
            "proxy_platform_coverage": coverage,
            "prediction_sku": sku,
            "prediction_market_risk_tier": "medium",
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_prediction_accuracy_feedback_splits_official_and_smoke(tmp_path) -> None:
    _write_calibration(tmp_path / "a-p6-75-prediction-calibration-v0.json", official=True, precision=0.6, coverage=0.4)
    _write_calibration(tmp_path / "b-p6-75-prediction-calibration-v0.json", official=False, precision=0.9, coverage=0.5)

    report = prediction_accuracy_feedback_v0.build_prediction_accuracy_feedback_v0(
        ops_dir=str(tmp_path),
        min_official_runs=3,
    )

    assert report["passed"] is True
    assert report["summary"]["official_runs"] == 1
    assert report["summary"]["smoke_runs"] == 1
    assert report["summary"]["calibration_allowed"] is False
    assert report["summary"]["auto_tuning_allowed"] is False
    assert report["summary"]["official_proxy_precision_avg"] == 0.6
    assert report["summary"]["smoke_proxy_precision_avg"] == 0.9
    assert report["policy"]["no_model_weight_update"] is True
    assert report["provider_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False


def test_prediction_accuracy_feedback_opens_window_after_min_official_runs(tmp_path) -> None:
    for index, precision in enumerate([0.5, 0.7, 0.9], start=1):
        _write_calibration(
            tmp_path / f"{index}-p6-75-prediction-calibration-v0.json",
            official=True,
            precision=precision,
            coverage=0.5,
        )

    report = prediction_accuracy_feedback_v0.build_prediction_accuracy_feedback_v0(
        ops_dir=str(tmp_path),
        min_official_runs=3,
    )

    assert report["passed"] is True
    assert report["summary"]["feedback_status"] == "calibration_window_ready"
    assert report["summary"]["official_runs"] == 3
    assert report["summary"]["calibration_allowed"] is True
    assert report["summary"]["auto_tuning_allowed"] is False
    assert report["summary"]["weight_update_allowed"] is False
    assert report["policy"]["human_review_required_before_tuning"] is True


def test_prediction_accuracy_feedback_fails_without_artifacts(tmp_path) -> None:
    report = prediction_accuracy_feedback_v0.build_prediction_accuracy_feedback_v0(ops_dir=str(tmp_path))

    assert report["passed"] is False
    assert report["summary"]["feedback_status"] == "no_calibration_artifacts"
    assert report["checks"]["calibration_artifacts_loaded"] is False
    assert report["summary"]["calibration_allowed"] is False
