from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.routers import vkpi_my_kol as router_mod  # noqa: E402
from app.domains import content_metric_snapshots as snapshots  # noqa: E402
from app.domains.kol import video_tracking_trends as trends  # noqa: E402


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, duplicate_of_id INTEGER, display_name TEXT, handle TEXT);
        CREATE TABLE vkpi_kol_pool_favorites (id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER, staff_id INTEGER);
        CREATE TABLE vkpi_kol_pool_members (id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER, staff_id INTEGER);
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER NOT NULL, content_url TEXT NOT NULL,
            platform TEXT, title TEXT, video_title TEXT, is_active INTEGER DEFAULT 1,
            published_at_norm TEXT, publish_date TEXT, posted_at TEXT,
            view_count INTEGER, like_count INTEGER, comment_count INTEGER, share_count INTEGER,
            metrics_scraped_at TEXT, metrics_source TEXT, updated_at TEXT, created_at TEXT
        );
        CREATE TABLE vkpi_kol_video_metric_tracking (
            evidence_id INTEGER PRIMARY KEY, tracked_by_staff_id INTEGER,
            status TEXT DEFAULT 'active', source TEXT DEFAULT 'my_kol_video_tracking',
            last_enqueued_at TEXT, last_job_id INTEGER, last_enqueue_status TEXT DEFAULT '',
            pause_reason TEXT DEFAULT '', created_at TEXT, updated_at TEXT
        );
        CREATE TABLE scheduler_tasks (task_key TEXT PRIMARY KEY, enabled INTEGER);
        INSERT INTO vkpi_kol_pool VALUES (9, NULL, 'Creator Nine', 'nine'), (10, NULL, 'Creator Ten', 'ten');
        INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (9, 10), (10, 20);
        INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, content_url, platform, title, published_at_norm, view_count, created_at, updated_at)
        VALUES (41, 9, 'https://www.youtube.com/watch?v=abcdefghijk', 'youtube', 'Tracked hot video', '2026-08-20T10:00:00+00:00', 1500, '2026-08-20', '2026-08-20'),
               (42, 9, 'https://www.youtube.com/watch?v=bbbbbbbbbbb', 'youtube', 'Never measured', '2026-01-01T10:00:00+00:00', NULL, '2026-08-20', '2026-08-20'),
               (43, 10, 'https://www.youtube.com/watch?v=ccccccccccc', 'youtube', 'Other staff video', '2026-08-01T10:00:00+00:00', 9, '2026-08-20', '2026-08-20');
        INSERT INTO vkpi_kol_video_metric_tracking (evidence_id, tracked_by_staff_id, status, created_at, updated_at)
        VALUES (41, 10, 'active', '2026-08-10', '2026-08-21'),
               (42, 10, 'paused', '2026-08-10', '2026-08-20'),
               (43, 20, 'active', '2026-08-10', '2026-08-19');
        INSERT INTO scheduler_tasks VALUES ('vkpi_kol_video_metric_refresh', 0);
        """
    )
    snapshots.ensure_sqlite_schema(conn)
    return conn


def _seed_history(conn: sqlite3.Connection) -> None:
    points = [
        (NOW - timedelta(days=31), 100, 10, 1),
        (NOW - timedelta(days=8), 700, 60, 5),
        (NOW - timedelta(days=2), 1200, 90, 8),
        (NOW - timedelta(hours=3), 1500, 100, 9),
    ]
    for at, views, likes, comments in points:
        snapshots.append_snapshot(
            conn,
            evidence_id=41,
            provider="fixture",
            fetched_at=at.isoformat(),
            status="success",
            views=views,
            likes=likes,
            comments=comments,
            shares=0,
        )
    snapshots.append_snapshot(
        conn,
        evidence_id=43,
        provider="fixture",
        fetched_at=(NOW - timedelta(hours=5)).isoformat(),
        status="failed",
        error_code="runtimeerror",
    )


def test_window_deltas_and_daily_average_use_real_baselines() -> None:
    conn = _conn()
    _seed_history(conn)

    body = trends.tracked_video_trends(conn, kol_pool_id=9, now=NOW)

    assert body["contract"] == "my_kol_metric_trends_v1"
    assert body["read_only"] is True
    assert body["scheduler"]["enabled"] is False
    assert body["empty_reason"] is None
    items = {item["evidence_id"]: item for item in body["items"]}
    hot = items[41]
    assert hot["latest"]["views"] == 1500
    assert hot["sample_count"] == 4
    week = hot["windows"]["7d"]["views"]
    assert week["status"] == "ready"
    assert week["delta"] == 1500 - 700
    assert week["baseline_value"] == 700
    assert week["daily_avg"] == round(800 / ((NOW - timedelta(hours=3) - (NOW - timedelta(days=8))).total_seconds() / 86400), 2)
    month = hot["windows"]["30d"]["views"]
    assert month["status"] == "ready" and month["delta"] == 1400
    assert hot["windows"]["7d"]["likes"]["delta"] == 40
    assert hot["windows"]["30d"]["comments"]["delta"] == 8
    # 调度闸 OFF → 下次刷新诚实给 scheduler_disabled,不给假时间
    assert hot["tracking"]["next_refresh"] == {"tier": "hot", "estimated_at": None, "reason": "scheduler_disabled"}
    assert hot["series"][-1]["views"] == 1500 and len(hot["series"]) == 4

    never = items[42]
    assert never["latest"] is None
    assert never["tracking"]["status"] == "paused"
    assert never["tracking"]["history"] == "never_measured"
    assert never["windows"]["7d"]["views"]["status"] == "insufficient_history"
    assert never["tracking"]["next_refresh"]["reason"] == "tracking_paused"

    summary = body["summary"]
    assert summary["tracked_total"] == 2 and summary["active"] == 1 and summary["paused"] == 1
    assert summary["measured"] == 1 and summary["views_latest_total"] == 1500
    assert summary["windows"]["7d"]["views"] == {"delta": 800, "videos": 1}


def test_partial_window_is_labelled_not_faked() -> None:
    conn = _conn()
    for at, views in ((NOW - timedelta(days=3), 100), (NOW - timedelta(hours=1), 400)):
        snapshots.append_snapshot(conn, evidence_id=41, provider="fixture", fetched_at=at.isoformat(), status="success", views=views)

    item = {i["evidence_id"]: i for i in trends.tracked_video_trends(conn, kol_pool_id=9, now=NOW)["items"]}[41]

    month = item["windows"]["30d"]["views"]
    assert month["status"] == "partial"
    assert month["delta"] == 300
    assert 2.9 < month["covered_days"] < 3.0
    assert abs(month["daily_avg"] - 300 / (71 / 24)) < 0.01


def test_next_refresh_is_estimated_only_when_scheduler_enabled() -> None:
    conn = _conn()
    conn.execute("UPDATE scheduler_tasks SET enabled=1")
    last = NOW - timedelta(hours=2)
    snapshots.append_snapshot(conn, evidence_id=41, provider="fixture", fetched_at=last.isoformat(), status="success", views=5)

    item = {i["evidence_id"]: i for i in trends.tracked_video_trends(conn, kol_pool_id=9, now=NOW)["items"]}[41]

    assert item["tracking"]["next_refresh"]["reason"] == "estimated_by_cadence"
    assert item["tracking"]["next_refresh"]["estimated_at"] == (last + timedelta(hours=6)).isoformat(timespec="seconds")


def test_failed_attempt_keeps_latest_success_and_backoff() -> None:
    conn = _conn()
    conn.execute("UPDATE scheduler_tasks SET enabled=1")
    _seed_history(conn)
    failed_at = NOW - timedelta(hours=1)
    snapshots.append_snapshot(conn, evidence_id=41, provider="fixture", fetched_at=failed_at.isoformat(), status="failed", error_code="http_429")

    item = {i["evidence_id"]: i for i in trends.tracked_video_trends(conn, kol_pool_id=9, now=NOW)["items"]}[41]

    assert item["latest"]["views"] == 1500
    assert item["last_attempt"]["status"] == "failed" and item["last_attempt"]["error_code"] == "http_429"
    assert item["failed_count"] == 1 and item["attempt_count"] == 5
    assert item["tracking"]["next_refresh"]["estimated_at"] == (failed_at + timedelta(hours=24)).isoformat(timespec="seconds")


def test_overview_respects_collection_scope() -> None:
    conn = _conn()
    _seed_history(conn)

    own = trends.tracked_video_overview(conn, staff_scope_id=10, now=NOW)
    team = trends.tracked_video_overview(conn, staff_scope_id=None, now=NOW)

    assert {i["evidence_id"] for i in own["items"]} == {41, 42}
    assert own["scope"]["mode"] == "staff_collection"
    assert {i["evidence_id"] for i in team["items"]} == {41, 42, 43}
    assert team["scope"]["mode"] == "team_collection"
    other = {i["evidence_id"]: i for i in team["items"]}[43]
    assert other["kol_name"] == "Creator Ten"
    assert other["last_attempt"]["status"] == "failed" and other["latest"] is None


def test_empty_tracking_is_honest() -> None:
    conn = _conn()
    conn.execute("DELETE FROM vkpi_kol_video_metric_tracking")

    body = trends.tracked_video_trends(conn, kol_pool_id=9, now=NOW)

    assert body["items"] == [] and body["empty_reason"] == "no_tracked_videos"
    assert body["summary"]["tracked_total"] == 0


def test_scheduler_gate_unknown_when_registry_missing() -> None:
    conn = _conn()
    conn.execute("DROP TABLE scheduler_tasks")
    assert trends.scheduler_gate(conn)["enabled"] is None


def test_router_trends_endpoint_enforces_row_scope(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)

    body = router_mod.my_kol_metric_trends_endpoint(kol_pool_id=9, limit=10, staff={"id": 10, "role": "member"})
    assert body["kol_pool_id"] == 9 and body["summary"]["tracked_total"] == 2

    with pytest.raises(HTTPException) as error:
        router_mod.my_kol_metric_trends_endpoint(kol_pool_id=10, limit=10, staff={"id": 10, "role": "member"})
    assert error.value.status_code == 403
    assert error.value.detail == "my_kol_target_read_forbidden"


def test_router_overview_reduces_employee_to_own_scope(monkeypatch) -> None:
    conn = _conn()
    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)

    body = router_mod.my_kol_metric_tracking_overview_endpoint(limit=10, staff_id=20, staff={"id": 10, "role": "member"})

    assert body["scope"]["staff_scope_id"] == 10
    assert {i["evidence_id"] for i in body["items"]} == {41, 42}
    with pytest.raises(HTTPException):
        router_mod.my_kol_metric_tracking_overview_endpoint(limit=10, staff_id=None, staff={"role": "member"})
