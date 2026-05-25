from __future__ import annotations

import json

from app.domains.intelligence import brain_acceptance_use_case as brain_layer_acceptance_v0


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _base_report(mode: str, summary: dict) -> dict:
    return {
        "mode": mode,
        "generated_at": "2026-05-23T11:30:00Z",
        "passed": True,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "external_http_calls": False,
        "summary": summary,
    }


def _write_required_artifacts(tmp_path, *, auto_tuning_allowed: bool = False) -> None:
    _write(
        tmp_path / "p6-71-fire-metric-definitions-local.json",
        {
            **_base_report("p6_71_fire_metric_definitions", {}),
            "definition_version": "fire-v0.1",
            "metrics": {"views_velocity": {}, "engagement_velocity": {}},
            "checks": {"misleading_growth_blocked": True},
        },
    )
    _write(
        tmp_path / "p6-72-time-series-anchors-local.json",
        _base_report("p6_72_time_series_anchors", {"anchor_count": 7, "trend_replay_ready": 5, "delta_anchors": 3}),
    )
    _write(
        tmp_path / "local-p6-73-trend-detection-v0.json",
        _base_report("p6_73_trend_detection_v0", {"signals_total": 8, "abnormal_growth_signals": 2, "market_event_signals": 1}),
    )
    _write(
        tmp_path / "local-p6-74-new-launch-acceptance-v0.json",
        _base_report(
            "p6_74_new_launch_acceptance_v0",
            {"sku": "AF-14MM-F40-AIR-FE", "candidate_count": 4, "strong_candidate_count": 1, "market_risk_tier": "medium"},
        ),
    )
    _write(
        tmp_path / "local-p6-75-prediction-calibration-v0.json",
        _base_report(
            "p6_75_prediction_calibration_v0",
            {"calibration_status": "same_day_smoke_or_pending", "accuracy_official": False, "proxy_precision": 1.0},
        ),
    )
    _write(
        tmp_path / "local-p6-76-today-new-signals-v0.json",
        _base_report("p6_76_today_new_signals_v0", {"trend_signals_24h": 3, "comment_status": "cached_window", "action_items": 2}),
    )
    _write(
        tmp_path / "local-p6-77-weekly-action-plan-v0.json",
        _base_report(
            "p6_77_weekly_action_plan_v0",
            {"action_count": 2, "action_types": {"contact_kol_for_sku": 1, "review_growth_post": 1}, "sku": "AF-14MM-F40-AIR-FE"},
        ),
    )
    _write(
        tmp_path / "local-p6-78-prediction-accuracy-feedback-v0.json",
        _base_report(
            "p6_78_prediction_accuracy_feedback_v0",
            {
                "feedback_status": "pending_cross_day_truth",
                "official_runs": 0,
                "smoke_runs": 1,
                "calibration_allowed": False,
                "auto_tuning_allowed": auto_tuning_allowed,
            },
        ),
    )


def test_brain_layer_acceptance_passes_with_required_reports(tmp_path) -> None:
    _write_required_artifacts(tmp_path)

    report = brain_layer_acceptance_v0.build_brain_layer_acceptance_v0(ops_dir=str(tmp_path))

    assert report["passed"] is True
    assert report["summary"]["technical_acceptance_passed"] is True
    assert report["summary"]["decision_support_level"] == "actionable_v0_calibration_pending"
    assert report["summary"]["can_assist_new_launch_kol_decision"] is True
    assert report["summary"]["business_confirmed"] is False
    assert report["summary"]["official_accuracy_pending"] is True
    assert report["policy"]["no_model_training"] is True
    assert report["sync_triggered"] is False


def test_brain_layer_acceptance_fails_when_required_report_missing(tmp_path) -> None:
    _write_required_artifacts(tmp_path)
    (tmp_path / "local-p6-77-weekly-action-plan-v0.json").unlink()

    report = brain_layer_acceptance_v0.build_brain_layer_acceptance_v0(ops_dir=str(tmp_path))

    assert report["passed"] is False
    assert report["checks"]["all_required_artifacts_loaded"] is False
    assert "P6.77" in report["summary"]["missing_phases"]


def test_brain_layer_acceptance_blocks_auto_tuning(tmp_path) -> None:
    _write_required_artifacts(tmp_path, auto_tuning_allowed=True)

    report = brain_layer_acceptance_v0.build_brain_layer_acceptance_v0(ops_dir=str(tmp_path))

    assert report["passed"] is False
    assert report["checks"]["calibration_guard_present"] is False
