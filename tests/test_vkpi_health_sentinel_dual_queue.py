from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domains.ops import health_sentinel


def test_legacy_queue_orphan_is_fail(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "table_exists", lambda table: table == "job_execution_ledger")
    monkeypatch.setattr(
        health_sentinel,
        "_row",
        lambda *_args, **_kwargs: {
            "active_total": 18,
            "stale_active": 18,
            "stale_without_stream": 16,
            "oldest_stale": datetime.now(timezone.utc) - timedelta(days=50),
        },
    )

    check = health_sentinel._check_legacy_queue_orphans()
    assert check["status"] == "fail"
    assert "孤儿 18" in check["detail"]
    assert "无 stream_id 16" in check["detail"]


def test_legacy_queue_empty_is_ok(monkeypatch) -> None:
    monkeypatch.setattr(health_sentinel, "table_exists", lambda table: table == "job_execution_ledger")
    monkeypatch.setattr(
        health_sentinel,
        "_row",
        lambda *_args, **_kwargs: {"active_total": 0, "stale_active": 0, "stale_without_stream": 0},
    )

    check = health_sentinel._check_legacy_queue_orphans()
    assert check["status"] == "ok"
    assert "0 条" in check["detail"]
