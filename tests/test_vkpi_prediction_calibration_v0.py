from __future__ import annotations

from app.services.vkpi import prediction_calibration_v0


def test_prediction_calibration_marks_cross_day_accuracy() -> None:
    prediction = {
        "generated_at": "2026-05-22T11:00:00Z",
        "summary": {"sku": "AF-14MM-F40-AIR-FE", "market_risk_tier": "medium"},
        "top_candidates": [
            {"kol_pool_id": 1, "platform": "youtube", "handle": "creator", "acceptance_score": 64, "acceptance_tier": "qualified_candidate"},
            {"kol_pool_id": 2, "platform": "instagram", "handle": "igcreator", "acceptance_score": 58, "acceptance_tier": "qualified_candidate"},
        ],
    }
    truth = {
        "generated_at": "2026-05-23T11:00:00Z",
        "signals": [
            {
                "rule_key": "official_post_views_delta_spike",
                "score": 85,
                "is_abnormal_growth": True,
                "entity": {"platform": "youtube", "account_handle": "viltroxofficial", "post_uid": "p1"},
            }
        ],
    }

    report = prediction_calibration_v0.build_prediction_calibration_from_reports(
        prediction_report=prediction,
        truth_report=truth,
        top_n=12,
    )

    assert report["passed"] is True
    assert report["summary"]["accuracy_official"] is True
    assert report["summary"]["calibration_status"] == "official_cross_day"
    assert report["summary"]["hit_count"] == 1
    assert report["summary"]["proxy_precision"] == 0.5
    assert report["provider_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False


def test_prediction_calibration_labels_same_day_as_smoke() -> None:
    prediction = {
        "generated_at": "2026-05-23T10:00:00Z",
        "summary": {"sku": "AF-14MM-F40-AIR-FE"},
        "top_candidates": [{"platform": "youtube", "handle": "creator", "acceptance_score": 60}],
    }
    truth = {
        "generated_at": "2026-05-23T11:00:00Z",
        "signals": [{"score": 70, "is_abnormal_growth": True, "entity": {"platform": "youtube"}}],
    }

    report = prediction_calibration_v0.build_prediction_calibration_from_reports(
        prediction_report=prediction,
        truth_report=truth,
    )

    assert report["passed"] is True
    assert report["summary"]["accuracy_official"] is False
    assert report["summary"]["calibration_status"] == "same_day_smoke_or_pending"
    assert report["policy"]["official_accuracy_requires_cross_day"] is True
