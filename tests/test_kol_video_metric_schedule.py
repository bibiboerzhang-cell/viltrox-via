from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domains.kol import (
    video_metric_refresh,
    video_metric_schedule,
    video_tracking,
)
from app.services.scheduler import jobs_tasks_kol


NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)


def _fake_active_enqueue(conn, *, job_type, payload, idempotency_key):
    existing = conn.execute(
        """
        SELECT id, job_type, payload, status FROM apify_jobs
        WHERE idempotency_key=? AND status IN ('queued', 'running')
        ORDER BY id DESC LIMIT 1
        """,
        (idempotency_key,),
    ).fetchone()
    if existing:
        row = dict(existing)
        row["payload"] = json.loads(row["payload"])
        return row, False
    cursor = conn.execute(
        """
        INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
        VALUES (?, ?, ?, 'queued')
        """,
        (job_type, json.dumps(payload), idempotency_key),
    )
    return {
        "id": int(cursor.lastrowid),
        "job_type": job_type,
        "payload": payload,
        "status": "queued",
    }, True


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("NOW", 0, lambda: NOW.isoformat())
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, status TEXT, email TEXT);
        CREATE TABLE staff (
            id INTEGER PRIMARY KEY, user_id INTEGER, role TEXT,
            permissions_json TEXT, active INTEGER, suspended_at TEXT,
            is_owner INTEGER DEFAULT 0
        );
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY, duplicate_of_id INTEGER
        );
        CREATE TABLE vkpi_kol_pool_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER, staff_id INTEGER,
            UNIQUE(kol_pool_id, staff_id)
        );
        CREATE TABLE vkpi_products (sku TEXT PRIMARY KEY);
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER, content_url TEXT,
            platform TEXT, evidence_type TEXT DEFAULT 'video',
            is_active INTEGER DEFAULT 1, channel_id TEXT,
            published_at_norm TEXT, publish_date TEXT, posted_at TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE vkpi_kol_video_product_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id INTEGER,
            product_sku TEXT, relation_type TEXT, source TEXT,
            confidence REAL, created_by_staff_id INTEGER,
            created_at TEXT, updated_at TEXT,
            UNIQUE(evidence_id, product_sku, relation_type)
        );
        CREATE TABLE vkpi_content_metric_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, evidence_id INTEGER,
            capture_key TEXT UNIQUE, provider TEXT, source_observed_at TEXT,
            fetched_at TEXT, views INTEGER, likes INTEGER, comments INTEGER,
            shares INTEGER, status TEXT, error_code TEXT, run_id TEXT,
            quality_flags TEXT, created_at TEXT
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT,
            payload TEXT, idempotency_key TEXT, status TEXT
        );
        CREATE TABLE vkpi_kol_video_metric_tracking (
            evidence_id INTEGER PRIMARY KEY, tracked_by_staff_id INTEGER,
            status TEXT DEFAULT 'active', source TEXT,
            last_enqueued_at TEXT, last_job_id INTEGER,
            last_enqueue_status TEXT DEFAULT '', pause_reason TEXT DEFAULT '',
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE vkpi_provider_budget_caps (
            scope TEXT PRIMARY KEY, cap_usd REAL, current_spend REAL DEFAULT 0,
            warning_at REAL DEFAULT 0.8, hard_stop_at REAL DEFAULT 1.0,
            reset_at TEXT, fallback_action TEXT, metadata_json TEXT DEFAULT '{}'
        );
        CREATE TABLE vkpi_ai_cost_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, cron_task TEXT, ai_provider TEXT,
            model_name TEXT, cost_usd REAL, metadata_json TEXT, occurred_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT INTO vkpi_provider_budget_caps (scope, cap_usd, fallback_action)
        VALUES ('metric_tracking', 30, 'pause_tracking_enqueue')
        """
    )
    conn.executemany(
        "INSERT INTO users (id, status, email) VALUES (?, ?, ?)",
        [
            (110, "active", "one@example.test"),
            (120, "active", "two@example.test"),
            (130, "active", "three@example.test"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO staff
          (id, user_id, role, permissions_json, active, suspended_at)
        VALUES (?, ?, 'member', '{"vkpi":"write"}', ?, NULL)
        """,
        [(10, 110, 1), (20, 120, 1), (30, 130, 1)],
    )
    conn.execute("INSERT INTO vkpi_kol_pool (id) VALUES (1)")
    conn.executemany(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (1, ?)",
        [(10,), (20,), (30,)],
    )
    conn.commit()
    return conn


def _add_evidence(
    conn: sqlite3.Connection,
    evidence_id: int,
    *,
    published_at: str,
    tracked_by: int | None = 10,
    last_enqueued_at: str | None = None,
    snapshot_at: str | None = None,
    snapshot_status: str = "success",
    content_url: str | None = None,
) -> None:
    url = content_url or f"https://www.youtube.com/watch?v=video{evidence_id:06d}"
    conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, content_url, platform, evidence_type, is_active,
            channel_id, published_at_norm, created_at, updated_at
        ) VALUES (?, 1, ?, 'youtube', 'video', 1, 'UC-owner', ?, ?, ?)
        """,
        (evidence_id, url, published_at, published_at, published_at),
    )
    if tracked_by is not None:
        conn.execute(
            """
            INSERT INTO vkpi_kol_video_metric_tracking (
                evidence_id, tracked_by_staff_id, status, source,
                last_enqueued_at, created_at, updated_at
            ) VALUES (?, ?, 'active', 'test', ?, ?, ?)
            """,
            (
                evidence_id,
                tracked_by,
                last_enqueued_at,
                published_at,
                published_at,
            ),
        )
    if snapshot_at:
        conn.execute(
            """
            INSERT INTO vkpi_content_metric_snapshots (
                evidence_id, capture_key, provider, fetched_at, status,
                quality_flags, created_at
            ) VALUES (?, ?, 'test', ?, ?, '[]', ?)
            """,
            (
                evidence_id,
                f"snapshot:{evidence_id}:{snapshot_at}",
                snapshot_at,
                snapshot_status,
                snapshot_at,
            ),
        )
    conn.commit()


@pytest.fixture()
def schedule_conn(monkeypatch: pytest.MonkeyPatch):
    conn = _conn()
    monkeypatch.setattr(
        video_metric_refresh,
        "enqueue_active_apify_job",
        _fake_active_enqueue,
    )
    yield conn
    conn.close()


def test_hot_warm_cold_cadence_and_explicit_tracking_only(
    schedule_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_evidence(
        schedule_conn,
        101,
        published_at="2026-08-20T12:00:00+00:00",
        snapshot_at="2026-08-21T08:00:00+00:00",
    )
    _add_evidence(
        schedule_conn,
        102,
        published_at="2026-08-10T12:00:00+00:00",
        snapshot_at="2026-08-20T10:00:00+00:00",
    )
    _add_evidence(
        schedule_conn,
        103,
        published_at="2026-07-01T12:00:00+00:00",
        snapshot_at="2026-08-13T00:00:00+00:00",
    )
    _add_evidence(
        schedule_conn,
        104,
        published_at="2026-08-20T12:00:00+00:00",
        tracked_by=None,
    )
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: pytest.fail("scheduler called provider"),
    )

    result = video_metric_schedule.enqueue_due_tracked_video_refreshes(
        schedule_conn,
        now=NOW,
    )

    assert result["status"] == "ok"
    assert result["queued"] == 2
    assert result["not_due"] == 1
    assert result["tier_due"] == {"hot": 0, "warm": 1, "cold": 1}
    assert result["provider_calls_performed"] is False
    payloads = [
        json.loads(row[0])
        for row in schedule_conn.execute(
            "SELECT payload FROM apify_jobs ORDER BY id"
        ).fetchall()
    ]
    assert sorted(payload["evidence_id"] for payload in payloads) == [102, 103]
    assert all(payload["source"] == video_metric_schedule.TASK_KEY for payload in payloads)
    assert all(payload["queue_lane"] == "batch" for payload in payloads)


def test_budget_cap_blocks_enqueue_and_mirrors_ledger_spend(
    schedule_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_evidence(schedule_conn, 111, published_at="2026-08-20T12:00:00+00:00")
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: pytest.fail("scheduler called provider"),
    )
    # Two attributed ledger rows this month reach the $30 cap; an unrelated
    # Apify row and a last-month row must not count.
    marker = json.dumps({"operation": "kol_video_metric_refresh"})[1:-1]
    schedule_conn.executemany(
        """
        INSERT INTO vkpi_ai_cost_ledger (ai_provider, cost_usd, metadata_json, occurred_at)
        VALUES ('apify', ?, ?, ?)
        """,
        [
            (20.0, "{" + marker + "}", "2026-08-02T00:00:00+00:00"),
            (10.0, "{" + marker + "}", "2026-08-15T00:00:00+00:00"),
            (99.0, '{"operation": "listening_batch"}', "2026-08-15T00:00:00+00:00"),
            (99.0, "{" + marker + "}", "2026-07-15T00:00:00+00:00"),
        ],
    )
    schedule_conn.commit()

    result = video_metric_schedule.enqueue_due_tracked_video_refreshes(schedule_conn, now=NOW)

    assert result["status"] == "budget_blocked"
    assert result["queued"] == 0 and result["due_selected"] == 0
    assert result["budget"]["allowed"] is False
    assert result["budget"]["spend_usd"] == 30.0
    assert result["budget"]["cap_usd"] == 30.0
    assert schedule_conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0] == 0
    mirrored = schedule_conn.execute(
        "SELECT current_spend FROM vkpi_provider_budget_caps WHERE scope='metric_tracking'"
    ).fetchone()[0]
    assert mirrored == 30.0
    # Subscriptions are untouched: the pass resumes after the monthly reset.
    assert schedule_conn.execute(
        "SELECT status FROM vkpi_kol_video_metric_tracking WHERE evidence_id=111"
    ).fetchone()[0] == "active"

    # Missing scope row is fail-closed too (seed via enroll_metric_tracking.py).
    schedule_conn.execute("DELETE FROM vkpi_provider_budget_caps")
    schedule_conn.commit()
    blocked = video_metric_schedule.enqueue_due_tracked_video_refreshes(schedule_conn, now=NOW)
    assert blocked["status"] == "budget_blocked"
    assert blocked["budget"]["reason"] == "budget_scope_not_configured"


def test_hot_tier_is_queued_before_cold_when_batch_is_smaller_than_due(
    schedule_conn: sqlite3.Connection,
) -> None:
    # Three cold rows never attempted (lowest evidence ids) and one hot row:
    # a batch of 2 must take the hot row first, then the oldest cold row.
    for evidence_id in (301, 302, 303):
        _add_evidence(schedule_conn, evidence_id, published_at="2026-06-01T12:00:00+00:00")
    _add_evidence(schedule_conn, 309, published_at="2026-08-21T06:00:00+00:00")

    result = video_metric_schedule.enqueue_due_tracked_video_refreshes(
        schedule_conn, now=NOW, limit=2,
    )

    assert result["status"] == "ok"
    assert result["queued"] == 2
    assert result["scan_truncated"] is True
    assert result["tier_due"] == {"hot": 1, "warm": 0, "cold": 1}
    queued = [
        json.loads(row[0])["evidence_id"]
        for row in schedule_conn.execute("SELECT payload FROM apify_jobs ORDER BY id").fetchall()
    ]
    assert queued == [309, 301]


def test_failed_snapshot_uses_backoff_and_active_job_is_idempotent(
    schedule_conn: sqlite3.Connection,
) -> None:
    _add_evidence(
        schedule_conn,
        201,
        published_at="2026-08-20T12:00:00+00:00",
        snapshot_at="2026-08-21T00:00:00+00:00",
        snapshot_status="failed",
    )
    first = video_metric_schedule.enqueue_due_tracked_video_refreshes(
        schedule_conn,
        now=NOW,
    )
    assert first["not_due"] == 1
    assert first["queued"] == 0

    later = datetime(2026, 8, 22, 1, tzinfo=timezone.utc)
    second = video_metric_schedule.enqueue_due_tracked_video_refreshes(
        schedule_conn,
        now=later,
    )
    assert second["queued"] == 1
    schedule_conn.execute(
        "UPDATE vkpi_kol_video_metric_tracking SET last_enqueued_at=NULL WHERE evidence_id=201"
    )
    third = video_metric_schedule.enqueue_due_tracked_video_refreshes(
        schedule_conn,
        now=later,
    )
    assert third["already_queued"] == 1
    assert schedule_conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0] == 1


def test_revoked_actor_and_invalid_identity_pause_without_provider_or_job(
    schedule_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_evidence(
        schedule_conn,
        301,
        published_at="2026-08-01T00:00:00+00:00",
        tracked_by=10,
    )
    _add_evidence(
        schedule_conn,
        302,
        published_at="2026-08-01T00:00:00+00:00",
        tracked_by=20,
    )
    _add_evidence(
        schedule_conn,
        303,
        published_at="2026-08-01T00:00:00+00:00",
        tracked_by=30,
        content_url="https://youtube.com.evil.test/watch?v=video303000",
    )
    schedule_conn.execute(
        "DELETE FROM vkpi_kol_pool_favorites WHERE staff_id=10"
    )
    schedule_conn.execute("UPDATE staff SET active=0 WHERE id=20")
    schedule_conn.commit()
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: pytest.fail("invalid subscription called provider"),
    )

    result = video_metric_schedule.enqueue_due_tracked_video_refreshes(
        schedule_conn,
        now=NOW,
    )

    assert result["paused"] == 3
    assert result["queued"] == 0
    assert result["provider_calls_performed"] is False
    assert schedule_conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0] == 0
    rows = schedule_conn.execute(
        "SELECT status, pause_reason FROM vkpi_kol_video_metric_tracking ORDER BY evidence_id"
    ).fetchall()
    assert all(row["status"] == "paused" for row in rows)
    assert all(row["pause_reason"] for row in rows)


def test_worker_revalidates_actor_after_enqueue_before_provider(
    schedule_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_evidence(
        schedule_conn,
        401,
        published_at="2026-08-20T00:00:00+00:00",
    )
    schedule_conn.execute("UPDATE staff SET active=0 WHERE id=10")
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: pytest.fail("revoked actor called provider"),
    )
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        {
            "evidence_id": 401,
            "kol_pool_id": 1,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=video000401",
            "staff_id": 10,
            "triggered_by_user_id": 110,
        },
        conn=schedule_conn,
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "video_refresh_actor_inactive"
    assert result["provider_calls_performed"] is False


def test_scheduler_gate_and_failure_receipt_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = []
    recorded = []
    monkeypatch.setattr(jobs_tasks_kol, "_scheduler_task_enabled", lambda _key: False)
    monkeypatch.setattr(
        video_metric_schedule,
        "enqueue_due_tracked_video_refreshes",
        lambda: called.append(True),
    )
    assert asyncio.run(jobs_tasks_kol.job_vkpi_kol_video_metric_refresh()) is None
    assert called == []

    monkeypatch.setattr(jobs_tasks_kol, "_scheduler_task_enabled", lambda _key: True)
    monkeypatch.setattr(
        video_metric_schedule,
        "enqueue_due_tracked_video_refreshes",
        lambda: (_ for _ in ()).throw(RuntimeError("private@example.test")),
    )
    monkeypatch.setattr(
        jobs_tasks_kol,
        "_record_scheduler_run",
        lambda key, **kwargs: recorded.append((key, kwargs)),
    )
    result = asyncio.run(jobs_tasks_kol.job_vkpi_kol_video_metric_refresh())
    assert result == {
        "status": "failed",
        "error_code": "runtimeerror",
        "provider_calls_performed": False,
    }
    assert recorded == [
        (video_metric_schedule.TASK_KEY, {"ok": False, "error": "runtimeerror"})
    ]
    assert "private@example.test" not in json.dumps(result)


def test_migration_285_and_scheduler_registration_are_fail_closed() -> None:
    root = Path(__file__).resolve().parents[1]
    up = (root / "migrations/285_vkpi_kol_video_metric_tracking.sql").read_text()
    down = (
        root / "migrations/285_vkpi_kol_video_metric_tracking_down.sql"
    ).read_text()
    registry = (
        root / "backend/app/services/scheduler/jobs_registry.py"
    ).read_text()
    supervisor = (root / "scripts/ops/local_stack_supervisor.sh").read_text()

    assert "evidence_id BIGINT PRIMARY KEY" in up
    assert "tracked_by_staff_id BIGINT" in up
    assert "ON DELETE SET NULL" in up
    assert "'vkpi_kol_video_metric_refresh'" in up
    assert "FALSE" in up and "'high'" in up
    assert "DROP TABLE IF EXISTS vkpi_kol_video_metric_tracking" in down
    assert "285_vkpi_kol_video_metric_tracking.sql" in down
    assert 'id="vkpi_kol_video_metric_refresh"' in registry
    assert "IntervalTrigger(hours=1)" in registry
    assert "vkpi_kol_video_metric_refresh" in supervisor
