from __future__ import annotations

from datetime import datetime, timezone

from app.domains.trends import trend_detection_use_case as trend_detection_v0


def _dt(day: int) -> datetime:
    return datetime(2026, 5, day, 4, 0, 0, tzinfo=timezone.utc)


def test_trend_detection_flags_delta_based_growth(monkeypatch) -> None:
    post_rows = [
        {
            "channel_id": 1,
            "platform": "youtube",
            "post_uid": "p1",
            "post_url": "https://youtube.com/watch?v=p1",
            "title": "Launch video",
            "snapshot_date": "2026-05-21",
            "captured_at": _dt(21),
            "views": 1000,
            "likes": 80,
            "comments": 10,
            "shares": 2,
            "views_delta": 100,
            "likes_delta": 10,
            "comments_delta": 1,
            "shares_delta": 0,
            "delta_method": "post_metric_delta_v1",
            "account_handle": "@viltrox",
        },
        {
            "channel_id": 1,
            "platform": "youtube",
            "post_uid": "p1",
            "post_url": "https://youtube.com/watch?v=p1",
            "title": "Launch video",
            "snapshot_date": "2026-05-22",
            "captured_at": _dt(22),
            "views": 1150,
            "likes": 90,
            "comments": 11,
            "shares": 2,
            "views_delta": 150,
            "likes_delta": 10,
            "comments_delta": 1,
            "shares_delta": 0,
            "delta_method": "post_metric_delta_v1",
            "account_handle": "@viltrox",
        },
        {
            "channel_id": 1,
            "platform": "youtube",
            "post_uid": "p1",
            "post_url": "https://youtube.com/watch?v=p1",
            "title": "Launch video",
            "snapshot_date": "2026-05-23",
            "captured_at": _dt(23),
            "views": 2750,
            "likes": 220,
            "comments": 35,
            "shares": 5,
            "views_delta": 1600,
            "likes_delta": 130,
            "comments_delta": 24,
            "shares_delta": 3,
            "delta_method": "post_metric_delta_v1",
            "account_handle": "@viltrox",
        },
    ]
    market_rows = [
        {
            "signal_uid": "s1",
            "brand": "Sigma",
            "normalized_brand": "sigma",
            "signal_type": "launch",
            "severity": "high",
            "score": 86,
            "platform": "youtube",
            "review_status": "pending_review",
            "created_at": _dt(23),
            "detail": "New launch",
        }
    ]
    monkeypatch.setattr(trend_detection_v0, "_now", lambda: _dt(23))
    monkeypatch.setattr(trend_detection_v0, "_load_post_metric_rows", lambda limit: post_rows)
    monkeypatch.setattr(trend_detection_v0, "_load_channel_metric_rows", lambda limit: [])
    monkeypatch.setattr(trend_detection_v0, "_load_market_signal_rows", lambda limit: market_rows)

    report = trend_detection_v0.build_trend_detection_v0()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["summary"]["abnormal_growth_signals"] >= 1
    assert any(signal["rule_key"] == "official_post_views_delta_spike" for signal in report["signals"])
    assert any(signal["signal_type"] == "market_signal_event" for signal in report["signals"])


def test_trend_detection_does_not_call_single_cumulative_snapshot_growth(monkeypatch) -> None:
    post_rows = [
        {
            "channel_id": 1,
            "platform": "youtube",
            "post_uid": "p2",
            "snapshot_date": "2026-05-23",
            "captured_at": _dt(23),
            "views": 1000000,
            "likes": 10000,
            "comments": 100,
            "shares": 20,
            "views_delta": 0,
            "likes_delta": 0,
            "comments_delta": 0,
            "shares_delta": 0,
            "delta_method": "post_metric_delta_v1",
            "account_handle": "@viltrox",
        }
    ]
    monkeypatch.setattr(trend_detection_v0, "_now", lambda: _dt(23))
    monkeypatch.setattr(trend_detection_v0, "_load_post_metric_rows", lambda limit: post_rows)
    monkeypatch.setattr(trend_detection_v0, "_load_channel_metric_rows", lambda limit: [])
    monkeypatch.setattr(trend_detection_v0, "_load_market_signal_rows", lambda limit: [])

    report = trend_detection_v0.build_trend_detection_v0()

    assert report["passed"] is True
    assert report["summary"]["abnormal_growth_signals"] == 0
    assert report["signals"] == []
    assert report["contract"]["single_cumulative_snapshot_is_not_growth"] is True
