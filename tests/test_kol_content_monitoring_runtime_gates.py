from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_my_kol
from app.domains.kol import (
    content_monitoring,
    url_deep_crawl,
    url_deep_crawl_helpers,
    url_deep_crawl_queue,
)
from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError
from app.services.scheduler import jobs_tasks_kol


NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.create_function("NOW", 0, lambda: NOW.isoformat())
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            duplicate_of_id INTEGER,
            platform TEXT,
            handle TEXT,
            profile_url TEXT
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT,
            payload TEXT,
            idempotency_key TEXT,
            status TEXT,
            last_error TEXT,
            updated_at TEXT
        );
        CREATE TABLE vkpi_kol_content_monitoring_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            kol_pool_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            cadence_hours INTEGER NOT NULL DEFAULT 24,
            next_due_at TEXT,
            generation INTEGER NOT NULL DEFAULT 1,
            last_enqueued_at TEXT,
            last_job_id INTEGER,
            last_job_status TEXT NOT NULL DEFAULT '',
            last_success_at TEXT,
            pause_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(staff_id, kol_pool_id)
        );
        """
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_pool
          (id, duplicate_of_id, platform, handle, profile_url)
        VALUES (1, NULL, 'youtube', 'creator', 'https://www.youtube.com/@creator')
        """
    )
    conn.commit()
    return conn


@pytest.fixture()
def monitor_conn(monkeypatch: pytest.MonkeyPatch):
    conn = _conn()
    monkeypatch.setattr(
        content_monitoring,
        "assert_target_readable",
        lambda _conn, *, kol_pool_id, staff: int((staff or {}).get("id") or 0),
    )

    def writable(_conn, *, kol_pool_id, staff):
        if (staff or {}).get("write") is not True:
            raise MyKolPaidActionError("my_kol_paid_action_write_forbidden", 403)
        return int((staff or {}).get("id") or 0)

    monkeypatch.setattr(content_monitoring, "assert_target_writable", writable)
    monkeypatch.setattr(
        content_monitoring,
        "target_write_context",
        lambda _conn, *, kol_pool_id, staff: {
            "can_run_paid_actions": (staff or {}).get("write") is True
        },
    )
    yield conn
    conn.close()


def _seed_subscription(
    conn: sqlite3.Connection,
    *,
    status: str = "active",
    generation: int = 1,
    next_due_at: str = "2026-08-21T10:00:00+00:00",
    staff_id: int = 10,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO vkpi_kol_content_monitoring_subscriptions
          (staff_id, kol_pool_id, status, cadence_hours, next_due_at, generation)
        VALUES (?, 1, ?, 24, ?, ?)
        """,
        (staff_id, status, next_due_at, generation),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_monitoring_runner_materializes_recent_evidence_without_ai_or_followups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_revalidate_target_write_fence",
        lambda _payload: {"id": 10, "user_id": 110},
    )

    def execute(body):
        captured.update(body)
        return {"status": "ready", "profile_flow": {"kol_pool_id": 1}}

    monkeypatch.setattr(url_deep_crawl, "dry_run_url_deep_crawl", execute)
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("session/media followup reached")),
    )
    result = url_deep_crawl_queue.run_profile_deep_crawl_for_job(
        {
            "url": "https://www.youtube.com/@creator",
            "kol_pool_id": 1,
            "mode": "account_deep",
            "max_posts": 12,
            "representative_video_limit": 1,
            "suppress_final_v1": True,
            "suppress_profile_followups": True,
            "content_monitor_fence": {"subscription_id": 7},
        }
    )

    assert result["status"] == "ready"
    assert captured["max_posts"] == 12
    assert captured["suppress_final_v1"] is True
    assert captured["suppress_profile_followups"] is True
    assert url_deep_crawl_helpers._profile_should_enqueue_representative_videos(captured) is False
    assert url_deep_crawl_helpers._profile_should_materialize_history_videos(captured) is True


def test_terminal_receipt_records_success_but_old_cancelled_generation_cannot_overwrite(
    monitor_conn: sqlite3.Connection,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    monitor_conn.execute(
        "INSERT INTO apify_jobs (id, job_type, payload, status) VALUES (91, 'kol_profile_deep_crawl', '{}', 'done')"
    )
    monitor_conn.execute(
        """
        UPDATE vkpi_kol_content_monitoring_subscriptions
        SET last_job_id=91, last_job_status='queued'
        WHERE id=?
        """,
        (subscription_id,),
    )
    monitor_conn.commit()
    payload = {
        "content_monitor_fence": {
            "subscription_id": subscription_id,
            "generation": 1,
        }
    }

    assert content_monitoring.record_monitor_job_terminal(
        payload,
        job_id=91,
        status="done",
        conn=monitor_conn,
    ) is True
    ready = dict(
        monitor_conn.execute(
            "SELECT last_job_status, last_success_at FROM vkpi_kol_content_monitoring_subscriptions"
        ).fetchone()
    )
    monitor_conn.execute(
        """
        UPDATE vkpi_kol_content_monitoring_subscriptions
        SET status='paused', generation=2, last_job_status='cancelled'
        WHERE id=?
        """,
        (subscription_id,),
    )
    monitor_conn.commit()

    assert ready["last_job_status"] == "done"
    assert ready["last_success_at"] == NOW.isoformat()
    assert content_monitoring.record_monitor_job_terminal(
        payload,
        job_id=91,
        status="blocked",
        conn=monitor_conn,
    ) is False
    assert monitor_conn.execute(
        "SELECT last_job_status FROM vkpi_kol_content_monitoring_subscriptions"
    ).fetchone()[0] == "cancelled"


@pytest.mark.parametrize("endpoint", ["enable", "pause"])
def test_release_validation_blocks_mutations_before_domain(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    monkeypatch.setattr(vkpi_my_kol, "release_validation_active", lambda: True)
    monkeypatch.setattr(
        content_monitoring,
        "enable_content_monitoring",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("domain reached")),
    )

    function = (
        vkpi_my_kol.my_kol_content_monitoring_enable_endpoint.__wrapped__
        if endpoint == "enable"
        else vkpi_my_kol.my_kol_content_monitoring_pause_endpoint.__wrapped__
    )
    kwargs = {"body": {"cadence_hours": 24}} if endpoint == "enable" else {}
    with pytest.raises(HTTPException) as exc_info:
        function(1, staff={"id": 10}, **kwargs)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "release_validation_fenced"


def test_scheduler_callback_is_default_off_and_provider_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs_tasks_kol, "_scheduler_task_enabled", lambda _key: False)
    monkeypatch.setattr(
        content_monitoring,
        "enqueue_due_content_monitoring",
        lambda: (_ for _ in ()).throw(AssertionError("disabled scheduler ran")),
    )

    assert asyncio.run(jobs_tasks_kol.job_vkpi_kol_content_monitoring()) is None


def test_worker_contact_followup_is_explicitly_suppressed_for_monitor_jobs() -> None:
    source = (ROOT / "backend/app/workers/apify_jobs_worker_deep_crawl.py").read_text()
    assert 'if payload.get("suppress_contact_followup") is True:' in source
