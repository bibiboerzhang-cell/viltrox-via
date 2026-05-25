from __future__ import annotations

from datetime import datetime, timezone

from app.domains.intelligence import today_signals_use_case as today_new_signals_v0


def _dt(hour: int) -> datetime:
    return datetime(2026, 5, 23, hour, 0, 0, tzinfo=timezone.utc)


def test_today_new_signals_builds_action_digest(monkeypatch) -> None:
    monkeypatch.setattr(today_new_signals_v0, "_now", lambda: _dt(12))
    monkeypatch.setattr(
        today_new_signals_v0.trend_detection_v0,
        "build_trend_detection_v0",
        lambda **kwargs: {
            "passed": True,
            "signals": [
                {
                    "signal_type": "official_post_growth",
                    "rule_key": "official_post_views_delta_spike",
                    "severity": "critical",
                    "score": 85,
                    "confidence": 0.7,
                    "is_abnormal_growth": True,
                    "entity": {"platform": "youtube", "account_handle": "viltroxofficial", "post_uid": "p1", "title": "Launch"},
                    "metric": {"value": 1200, "threshold": 500, "captured_at": "2026-05-23T11:00:00Z"},
                    "evidence": {"views_delta": 1200},
                }
            ],
        },
    )
    monkeypatch.setattr(
        today_new_signals_v0,
        "_market_rows",
        lambda limit: [
            {
                "signal_uid": "s1",
                "normalized_brand": "sigma",
                "signal_type": "pricing_sensitive",
                "severity": "high",
                "score": 70,
                "platform": "instagram",
                "review_status": "ready",
                "detail": "Price issue",
                "created_at": "2026-05-23T10:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        today_new_signals_v0,
        "_comment_rows",
        lambda limit: [
            {
                "id": 1,
                "platform": "youtube",
                "external_post_id": "p1",
                "comment_text": "Where can I buy this mount?",
                "author_handle": "user",
                "likes_count": 3,
                "reply_count": 0,
                "fetched_at": "2026-05-23T09:00:00Z",
                "sentiment": "neutral",
                "brand_attitude": "curious",
            }
        ],
    )

    report = today_new_signals_v0.build_today_new_signals_v0()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["summary"]["abnormal_growth_24h"] == 1
    assert report["summary"]["market_events_24h"] == 1
    assert report["summary"]["comment_opportunities_24h"] == 1
    assert report["comment_anomalies"]["contract"]["status"] == "cached_window"
    assert any(item["action"] == "review_growth_post" for item in report["action_items"])


def test_today_new_signals_is_honest_when_comments_missing(monkeypatch) -> None:
    monkeypatch.setattr(today_new_signals_v0, "_now", lambda: _dt(12))
    monkeypatch.setattr(
        today_new_signals_v0.trend_detection_v0,
        "build_trend_detection_v0",
        lambda **kwargs: {"passed": True, "signals": []},
    )
    monkeypatch.setattr(today_new_signals_v0, "_market_rows", lambda limit: [])
    monkeypatch.setattr(today_new_signals_v0, "_comment_rows", lambda limit: [])

    report = today_new_signals_v0.build_today_new_signals_v0()

    assert report["passed"] is True
    assert report["comment_anomalies"]["status"] == "no_cached_comments"
    assert report["comment_anomalies"]["contract"]["status"] == "not_cached"
    assert report["action_items"][0]["action"] == "no_action_required"
