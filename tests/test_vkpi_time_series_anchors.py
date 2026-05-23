from __future__ import annotations

from app.services.vkpi import time_series_anchors


def test_time_series_anchor_report_is_read_only_and_classified(monkeypatch) -> None:
    columns = {
        "vkpi_channel_metrics": {
            "channel_id", "snapshot_date", "captured_at", "followers", "posts_count",
            "total_views", "total_likes", "total_comments", "total_shares",
            "followers_delta", "posts_delta", "views_delta_24h", "likes_delta_24h",
        },
        "vkpi_channel_post_metrics": {
            "channel_id", "post_uid", "snapshot_date", "captured_at", "first_seen_at",
            "views", "likes", "comments", "shares", "views_delta", "likes_delta",
            "comments_delta", "shares_delta",
        },
        "vkpi_metric_runs": {"run_uid", "scope_type", "scope_id", "period_start", "period_end", "generated_at"},
        "vkpi_metric_values": {"run_id", "metric_key", "created_at", "value_numeric"},
    }
    monkeypatch.setattr(time_series_anchors, "_columns", lambda table: columns.get(table, {"id", "created_at", "score", "signal_uid"}))
    monkeypatch.setattr(time_series_anchors, "_count", lambda table: 10)
    monkeypatch.setattr(time_series_anchors, "_entity_count", lambda table, entity_columns: 3)
    monkeypatch.setattr(time_series_anchors, "_min_max", lambda table, column: {"min": "2026-05-01", "max": "2026-05-23"})

    report = time_series_anchors.build_time_series_anchor_report()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["summary"]["anchor_count"] >= 6
    assert report["summary"]["delta_anchors"] >= 2
    assert report["contract"]["cumulative_only_is_not_growth"] is True


def test_time_series_anchor_report_flags_missing_core_anchor(monkeypatch) -> None:
    monkeypatch.setattr(time_series_anchors, "_columns", lambda table: set())
    monkeypatch.setattr(time_series_anchors, "_count", lambda table: 0)
    monkeypatch.setattr(time_series_anchors, "_entity_count", lambda table, entity_columns: 0)
    monkeypatch.setattr(time_series_anchors, "_min_max", lambda table, column: {"min": "", "max": ""})

    report = time_series_anchors.build_time_series_anchor_report()

    assert report["passed"] is False
    assert report["checks"]["official_channel_anchor_ready"] is False
    assert report["checks"]["metric_lineage_anchor_ready"] is False
