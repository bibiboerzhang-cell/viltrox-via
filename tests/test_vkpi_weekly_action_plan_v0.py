from __future__ import annotations

from app.domains.intelligence import weekly_plan_use_case as weekly_action_plan_v0


def test_weekly_action_plan_generates_evidence_actions(monkeypatch) -> None:
    monkeypatch.setattr(
        weekly_action_plan_v0.new_launch_acceptance_v0,
        "build_new_launch_acceptance_v0",
        lambda **kwargs: {
            "passed": True,
            "mode": "p6_74_new_launch_acceptance_v0",
            "generated_at": "2026-05-23T11:00:00Z",
            "summary": {"sku": "AF-14MM-F40-AIR-FE", "candidate_count": 1},
            "product": {"sku": "AF-14MM-F40-AIR-FE"},
            "top_candidates": [
                {
                    "kol_pool_id": 1,
                    "platform": "youtube",
                    "handle": "creator",
                    "display_name": "Creator",
                    "acceptance_score": 66,
                    "acceptance_tier": "qualified_candidate",
                    "evidence": {"kol_product_fit_score": 75, "platform_signal_count": 3},
                }
            ],
        },
    )
    monkeypatch.setattr(
        weekly_action_plan_v0.today_new_signals_v0,
        "build_today_new_signals_v0",
        lambda **kwargs: {
            "passed": True,
            "mode": "p6_76_today_new_signals_v0",
            "generated_at": "2026-05-23T11:10:00Z",
            "summary": {"abnormal_growth_24h": 2, "comment_status": "cached_window"},
            "action_items": [
                {
                    "priority": "high",
                    "action": "review_growth_post",
                    "reason": "youtube spike",
                    "entity": {"platform": "youtube", "post_uid": "p1"},
                }
            ],
            "market_events": [],
        },
    )

    report = weekly_action_plan_v0.build_weekly_action_plan_v0(top_n=5)

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["summary"]["action_count"] >= 2
    assert all(item["evidence"] for item in report["actions"])
    assert report["policy"]["no_outreach_triggered"] is True


def test_weekly_action_plan_fails_when_sources_fail(monkeypatch) -> None:
    monkeypatch.setattr(
        weekly_action_plan_v0.new_launch_acceptance_v0,
        "build_new_launch_acceptance_v0",
        lambda **kwargs: {"passed": False, "summary": {}, "top_candidates": []},
    )
    monkeypatch.setattr(
        weekly_action_plan_v0.today_new_signals_v0,
        "build_today_new_signals_v0",
        lambda **kwargs: {"passed": False, "summary": {}, "action_items": [], "market_events": []},
    )

    report = weekly_action_plan_v0.build_weekly_action_plan_v0()

    assert report["passed"] is False
    assert report["checks"]["acceptance_report_passed"] is False
    assert report["checks"]["today_signals_passed"] is False
