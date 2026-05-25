from __future__ import annotations

from app.domains.launch import acceptance_use_case as new_launch_acceptance_v0


def test_new_launch_acceptance_combines_fit_trend_and_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        new_launch_acceptance_v0.product_campaign_card,
        "build_product_campaign_card",
        lambda **kwargs: {
            "passed": True,
            "product": {"sku": "AF-35/1.8-FE", "mount": "FE", "focal_length_label": "35mm"},
            "market_risk": {"risk_tier": "medium", "risk_score": 30, "top_competitor_brands": []},
            "kol_candidates": [
                {
                    "kol_pool_id": 1,
                    "platform": "youtube",
                    "handle": "creator",
                    "display_name": "Creator",
                    "followers": 100000,
                    "avg_views": 25000,
                    "score": 82,
                    "confidence": 0.72,
                    "risk_flags": [],
                    "evidence": [{"type": "alias_match", "value": "35mm F1.8 FE"}],
                }
            ],
        },
    )
    monkeypatch.setattr(
        new_launch_acceptance_v0.trend_detection_v0,
        "build_trend_detection_v0",
        lambda **kwargs: {
            "passed": True,
            "summary": {"signals_total": 4, "abnormal_growth_signals": 2},
            "signals": [
                {
                    "signal_type": "official_post_growth",
                    "rule_key": "official_post_views_delta_spike",
                    "score": 85,
                    "confidence": 0.7,
                    "is_abnormal_growth": True,
                    "entity": {"platform": "youtube", "account_handle": "viltroxofficial", "post_uid": "p1"},
                }
            ],
        },
    )

    report = new_launch_acceptance_v0.build_new_launch_acceptance_v0(top_n=3)

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["summary"]["sku"] == "AF-35/1.8-FE"
    assert report["top_candidates"]
    assert report["top_candidates"][0]["acceptance_tier"] in {"strong_candidate", "qualified_candidate"}
    evidence = report["top_candidates"][0]["evidence"]
    assert evidence["platform_signal_count"] == 1
    assert evidence["market_risk_tier"] == "medium"
    assert report["policy"]["not_a_trained_model"] is True


def test_new_launch_acceptance_fails_without_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        new_launch_acceptance_v0.product_campaign_card,
        "build_product_campaign_card",
        lambda **kwargs: {
            "passed": False,
            "product": {},
            "market_risk": {"risk_tier": "unknown"},
            "kol_candidates": [],
        },
    )
    monkeypatch.setattr(
        new_launch_acceptance_v0.trend_detection_v0,
        "build_trend_detection_v0",
        lambda **kwargs: {"passed": True, "summary": {"signals_total": 0, "abnormal_growth_signals": 0}, "signals": []},
    )

    report = new_launch_acceptance_v0.build_new_launch_acceptance_v0()

    assert report["passed"] is False
    assert report["checks"]["top_candidates_generated"] is False
    assert report["policy"]["no_project_created"] is True
