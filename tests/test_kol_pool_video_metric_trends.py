from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.domains import content_metric_snapshots as snapshots  # noqa: E402
from app.domains.kol import pool_detail  # noqa: E402


def _evidence_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            project_id INTEGER,
            content_url TEXT NOT NULL,
            platform TEXT,
            title TEXT,
            video_title TEXT,
            thumbnail_url TEXT,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            duration_seconds INTEGER,
            publish_date TEXT,
            posted_at TEXT,
            evidence_type TEXT,
            image_urls TEXT,
            source TEXT,
            is_active INTEGER,
            updated_at TEXT,
            created_at TEXT
        );
        INSERT INTO vkpi_kol_video_evidence VALUES (
            41, 9, NULL, 'https://www.youtube.com/watch?v=abcdefghijk', 'youtube',
            'Fixture video', 'Fixture video', '', 100, 10, 2, 1, 60,
            '2026-08-20T10:00:00Z', '2026-08-20', 'video', '[]', 'fixture', 1,
            '2026-08-20T10:00:00Z', '2026-08-20T10:00:00Z'
        );
        """
    )
    return conn


def test_pool_video_read_keeps_legacy_item_when_snapshot_table_missing(monkeypatch) -> None:
    conn = _evidence_conn()
    monkeypatch.setattr(pool_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: False)

    items = pool_detail._video_evidence_for_kol(9, limit=200)

    assert len(items) == 1
    assert items[0]["evidence_id"] == 41
    assert items[0]["view_count"] == 100
    assert items[0]["tracking_status"] == "unavailable"
    assert items[0]["views_delta_24h"] is None


def test_pool_video_read_merges_one_batch_trend_query(monkeypatch) -> None:
    conn = _evidence_conn()
    snapshots.ensure_sqlite_schema(conn)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    baseline = now - timedelta(hours=25)
    snapshots.append_snapshot(
        conn,
        evidence_id=41,
        provider="fixture",
        fetched_at=baseline.isoformat(),
        source_observed_at=baseline.isoformat(),
        views=80,
        status="success",
        run_id="baseline",
    )
    snapshots.append_snapshot(
        conn,
        evidence_id=41,
        provider="fixture",
        fetched_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        views=100,
        status="success",
        run_id="latest",
    )
    conn.commit()
    monkeypatch.setattr(pool_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: False)
    statements: list[str] = []
    conn.set_trace_callback(statements.append)

    items = pool_detail._video_evidence_for_kol(9, limit=200)

    trend_queries = [sql for sql in statements if "WITH ranked AS" in sql]
    assert len(trend_queries) == 1
    assert items[0]["tracking_status"] == "tracked"
    assert items[0]["views_delta_24h"] == 20
    assert items[0]["views_delta_7d"] is None
    assert items[0]["delta_7d_status"] == "insufficient_history"
    assert items[0]["last_success"]["views"] == 100
    assert {"provider", "run_id", "source_observed_at", "quality_flags", "error_code"}.isdisjoint(
        items[0]["last_success"]
    )
