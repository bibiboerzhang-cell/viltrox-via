from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.domains.kol import (
    content_monitoring,
    url_deep_crawl,
    url_deep_crawl_queue,
)
from app.domains.kol.my_kol_paid_action_access import MyKolPaidActionError
from app.domains.kol.video_tracking import VideoTrackingError


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


def _install_monitor_queue_fakes(
    monkeypatch: pytest.MonkeyPatch,
    conn: sqlite3.Connection,
) -> None:
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_build_target_write_fence",
        lambda *_a, **_k: {
            "version": 1,
            "kol_pool_id": 1,
            "staff_id": 10,
            "user_id": 110,
            "canonical_profile_url": "https://www.youtube.com/@creator",
            "platform": "youtube",
            "stable_identity_key": "youtube:handle:creator",
        },
    )
    monkeypatch.setattr(
        content_monitoring.video_metric_refresh,
        "authorize_video_metric_refresh_actor",
        lambda *_a, **_k: ({"id": 10, "user_id": 110}, ""),
    )


def test_migration_286_is_free_and_registers_default_off_explicit_task() -> None:
    up = (ROOT / "migrations/286_vkpi_kol_content_monitoring.sql").read_text()
    down = (ROOT / "migrations/286_vkpi_kol_content_monitoring_down.sql").read_text()

    assert not list((ROOT / "migrations").glob("286_*.sql")) == []
    assert "vkpi_kol_content_monitoring_subscriptions" in up
    assert "UNIQUE (staff_id, kol_pool_id)" in up
    assert "'vkpi_kol_content_monitoring'" in up
    assert "FALSE" in up
    assert "WHERE task_key='kol_auto_poll'" in up
    assert "enabled=FALSE" in up
    assert "DROP TABLE IF EXISTS vkpi_kol_content_monitoring_subscriptions" in down


def test_scheduler_state_rolls_back_failed_read_before_unknown_degradation() -> None:
    class FailedSchedulerConnection:
        rolled_back = False

        def execute(self, *_args, **_kwargs):
            raise RuntimeError("current transaction is aborted")

        def rollback(self):
            self.rolled_back = True

    conn = FailedSchedulerConnection()

    result = content_monitoring._scheduler_state(conn)

    assert conn.rolled_back is True
    assert result == {
        "task_key": content_monitoring.TASK_KEY,
        "configured": False,
        "enabled": None,
        "last_run_at": None,
        "last_success_at": None,
    }


def test_enable_is_explicit_provider_free_idempotent_and_pause_invalidates_generation(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("enable must not enqueue")),
    )
    staff = {"id": 10, "write": True}

    first = content_monitoring.enable_content_monitoring(
        1,
        cadence_hours=24,
        staff=staff,
        conn=monitor_conn,
    )
    generation = monitor_conn.execute(
        "SELECT generation FROM vkpi_kol_content_monitoring_subscriptions"
    ).fetchone()[0]
    repeated = content_monitoring.enable_content_monitoring(
        1,
        cadence_hours=24,
        staff=staff,
        conn=monitor_conn,
    )
    paused = content_monitoring.pause_content_monitoring(1, staff=staff, conn=monitor_conn)
    row = dict(
        monitor_conn.execute(
            "SELECT status, generation, next_due_at FROM vkpi_kol_content_monitoring_subscriptions"
        ).fetchone()
    )

    assert first["status"] == "enabled"
    assert repeated["status"] == "already_active"
    assert generation == 1
    assert paused["status"] == "paused"
    assert row == {"status": "paused", "generation": 2, "next_due_at": None}


def test_concurrent_first_enable_is_unique_idempotent_and_keeps_generation_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "content-monitor-enable-race.sqlite3"
    setup = sqlite3.connect(db_path)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY);
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
        INSERT INTO vkpi_kol_pool(id) VALUES (1);
        """
    )
    setup.commit()
    setup.close()
    monkeypatch.setattr(
        content_monitoring,
        "assert_target_writable",
        lambda _conn, *, kol_pool_id, staff: int((staff or {}).get("id") or 0),
    )

    insert_barrier = threading.Barrier(2)
    opened: list[sqlite3.Connection] = []
    opened_lock = threading.Lock()

    class BarrierConnection:
        def __init__(self, inner: sqlite3.Connection):
            self.inner = inner

        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith(
                "INSERT INTO vkpi_kol_content_monitoring_subscriptions"
            ):
                insert_barrier.wait(timeout=5)
            return self.inner.execute(sql, params)

        def commit(self):
            self.inner.commit()

        def rollback(self):
            self.inner.rollback()

    def enable_once(_attempt: int) -> dict:
        raw = sqlite3.connect(
            db_path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        raw.row_factory = sqlite3.Row
        raw.create_function("NOW", 0, lambda: NOW.isoformat())
        with opened_lock:
            opened.append(raw)
        return content_monitoring.enable_content_monitoring(
            1,
            cadence_hours=24,
            staff={"id": 10},
            conn=BarrierConnection(raw),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(enable_once, range(2)))

        assert sorted(result["status"] for result in results) == [
            "already_active",
            "enabled",
        ]
        assert len({result["subscription"]["id"] for result in results}) == 1
        check = sqlite3.connect(db_path)
        row = check.execute(
            """
            SELECT COUNT(*), MIN(generation), MAX(generation)
            FROM vkpi_kol_content_monitoring_subscriptions
            WHERE staff_id=10 AND kol_pool_id=1
            """
        ).fetchone()
        check.close()
        assert row == (1, 1, 1)
    finally:
        for connection in opened:
            connection.close()


def test_enable_does_not_mask_non_target_integrity_errors(
    monitor_conn: sqlite3.Connection,
) -> None:
    monitor_conn.execute(
        """
        CREATE TRIGGER reject_content_monitor_insert
        BEFORE INSERT ON vkpi_kol_content_monitoring_subscriptions
        BEGIN
            SELECT RAISE(ABORT, 'forced non-unique integrity failure');
        END
        """
    )
    monitor_conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced non-unique integrity failure"):
        content_monitoring.enable_content_monitoring(
            1,
            cadence_hours=24,
            staff={"id": 10, "write": True},
            conn=monitor_conn,
        )

    assert monitor_conn.execute(
        "SELECT COUNT(*) FROM vkpi_kol_content_monitoring_subscriptions"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("cadence", [0, 5, 169, "invalid"])
def test_enable_rejects_unbounded_or_invalid_cadence_without_writes(
    monitor_conn: sqlite3.Connection,
    cadence,
) -> None:
    before = monitor_conn.total_changes

    with pytest.raises(content_monitoring.ContentMonitoringError) as exc_info:
        content_monitoring.enable_content_monitoring(
            1,
            cadence_hours=cadence,
            staff={"id": 10, "write": True},
            conn=monitor_conn,
        )

    assert exc_info.value.status_code == 422
    assert monitor_conn.total_changes == before


def test_shared_reader_get_is_pure_but_cannot_enable(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_subscription(monitor_conn, staff_id=10)
    commits = monitor_conn.total_changes
    monkeypatch.setattr(
        url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("GET must not enqueue")),
    )

    result = content_monitoring.get_content_monitoring(
        1,
        staff={"id": 20, "write": False},
        conn=monitor_conn,
    )
    with pytest.raises(MyKolPaidActionError) as exc_info:
        content_monitoring.enable_content_monitoring(
            1,
            staff={"id": 20, "write": False},
            conn=monitor_conn,
        )

    assert result["read_only"] is True
    assert result["scope"] == "target_aggregate"
    assert "id" not in result["subscription"]
    assert "last_job_id" not in result["subscription"]
    assert "pause_reason" not in result["subscription"]
    assert result["subscription"]["window"] == {
        "kind": "recent_posts",
        "max_posts": 12,
        "full_history": False,
    }
    assert result["scheduler"] == {
        "task_key": "vkpi_kol_content_monitoring",
        "configured": False,
        "enabled": None,
        "last_run_at": None,
        "last_success_at": None,
    }
    assert monitor_conn.total_changes == commits
    assert exc_info.value.code == "my_kol_paid_action_write_forbidden"


def test_read_exposes_scheduler_gate_separately_from_active_subscription(
    monitor_conn: sqlite3.Connection,
) -> None:
    _seed_subscription(monitor_conn, staff_id=10)
    monitor_conn.execute(
        """
        CREATE TABLE scheduler_tasks (
            task_key TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            last_run_at TEXT,
            last_success_at TEXT
        )
        """
    )
    monitor_conn.execute(
        """
        INSERT INTO scheduler_tasks(task_key, enabled, last_run_at, last_success_at)
        VALUES ('vkpi_kol_content_monitoring', 0, '2026-08-21T11:00:00Z', NULL)
        """
    )
    monitor_conn.commit()

    result = content_monitoring.get_content_monitoring(
        1,
        staff={"id": 10, "write": True},
        conn=monitor_conn,
    )

    assert result["subscription"]["status"] == "active"
    assert result["scheduler"] == {
        "task_key": "vkpi_kol_content_monitoring",
        "configured": True,
        "enabled": False,
        "last_run_at": "2026-08-21T11:00:00Z",
        "last_success_at": None,
    }
    assert result["provider_calls_performed"] is False


def test_scheduler_queues_bounded_recent_evidence_only_and_is_idempotent(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    captured: list[dict] = []
    monitor_conn.execute(
        "INSERT INTO apify_jobs (id, job_type, payload, status) VALUES (?, 'kol_profile_deep_crawl', ?, 'queued')",
        (
            91,
            json.dumps(
                {
                    "content_monitor_fence": {
                        "version": 1,
                        "subscription_id": subscription_id,
                        "staff_id": 10,
                        "kol_pool_id": 1,
                        "generation": 1,
                    }
                }
            ),
        ),
    )
    monitor_conn.commit()
    monkeypatch.setattr(
        content_monitoring.video_metric_refresh,
        "authorize_video_metric_refresh_actor",
        lambda *_a, **_k: ({"id": 10, "user_id": 110}, ""),
    )

    def enqueue(_url, **kwargs):
        captured.append(kwargs)
        return {"status": "queued", "job_id": 91}

    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", enqueue)

    first = content_monitoring.enqueue_due_content_monitoring(
        monitor_conn,
        now=NOW,
        limit=1,
    )
    second = content_monitoring.enqueue_due_content_monitoring(
        monitor_conn,
        now=NOW,
        limit=1,
    )

    assert first["queued"] == 1
    assert second["status"] == "empty"
    assert len(captured) == 1
    kwargs = captured[0]
    assert kwargs["max_posts"] == 12
    assert kwargs["mode"] == "account_deep"
    assert kwargs["queue_lane"] == "batch"
    assert kwargs["enforce_target_write"] is True
    assert kwargs["suppress_final_v1"] is True
    assert kwargs["suppress_contact_followup"] is True
    assert kwargs["suppress_profile_followups"] is True
    assert kwargs["content_monitor_fence"] == {
        "version": 1,
        "subscription_id": subscription_id,
        "staff_id": 10,
        "kol_pool_id": 1,
        "generation": 1,
    }
    assert first["llm_jobs_enqueued"] == 0
    assert first["contact_jobs_enqueued"] == 0
    assert first["provider_calls_performed"] is False


def test_monitor_enqueue_never_reuses_plain_active_same_url_job(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    _install_monitor_queue_fakes(monkeypatch, monitor_conn)
    monitor_conn.execute(
        """
        INSERT INTO apify_jobs (id, job_type, payload, idempotency_key, status)
        VALUES (90, 'kol_profile_deep_crawl', ?, 'plain-same-url', 'running')
        """,
        (json.dumps({"url": "https://www.youtube.com/@creator"}),),
    )
    monitor_conn.commit()

    def enqueue(conn, *, job_type, payload, idempotency_key):
        cursor = conn.execute(
            """
            INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
            VALUES (?, ?, ?, 'queued')
            """,
            (job_type, json.dumps(payload), idempotency_key),
        )
        return {"id": int(cursor.lastrowid), "payload": payload, "status": "queued"}, True

    monkeypatch.setattr(url_deep_crawl_queue, "enqueue_active_apify_job", enqueue)

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.youtube.com/@creator",
        kol_pool_id=1,
        max_posts=12,
        staff={"id": 10, "user_id": 110},
        queue_lane="batch",
        enforce_target_write=True,
        content_monitor_fence={
            "version": 1,
            "subscription_id": subscription_id,
            "staff_id": 10,
            "kol_pool_id": 1,
            "generation": 1,
        },
    )
    queued = dict(
        monitor_conn.execute(
            "SELECT id, payload, idempotency_key FROM apify_jobs WHERE id=?",
            (result["job_id"],),
        ).fetchone()
    )

    assert result == {"status": "queued", "job_id": 91}
    assert result["job_id"] != 90
    assert json.loads(queued["payload"])["content_monitor_fence"] == {
        "version": 1,
        "subscription_id": subscription_id,
        "staff_id": 10,
        "kol_pool_id": 1,
        "generation": 1,
        "window_max_posts": 12,
    }
    assert queued["idempotency_key"].startswith(
        "apify:v1:kol_profile_deep_crawl.content-monitor:"
    )


def test_monitor_same_generation_conflict_is_race_safe_and_plain_key_is_isolated(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    _install_monitor_queue_fakes(monkeypatch, monitor_conn)
    # Simulate two transactions that both missed the compatibility pre-read;
    # the active unique-key helper remains the authoritative concurrency gate.
    monkeypatch.setattr(url_deep_crawl_queue, "_active_profile_job", lambda *_a, **_k: {})
    jobs_by_key: dict[str, dict] = {}
    received_keys: list[str] = []

    def enqueue(_conn, *, job_type, payload, idempotency_key):
        received_keys.append(idempotency_key)
        existing = jobs_by_key.get(idempotency_key)
        if existing:
            return existing, False
        job = {"id": 90 + len(jobs_by_key), "job_type": job_type, "payload": payload}
        jobs_by_key[idempotency_key] = job
        return job, True

    monkeypatch.setattr(url_deep_crawl_queue, "enqueue_active_apify_job", enqueue)
    kwargs = {
        "kol_pool_id": 1,
        "staff": {"id": 10, "user_id": 110},
        "enforce_target_write": True,
        "content_monitor_fence": {
            "version": 1,
            "subscription_id": subscription_id,
            "staff_id": 10,
            "kol_pool_id": 1,
            "generation": 1,
        },
    }

    first = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.youtube.com/@creator", **kwargs
    )
    concurrent = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.youtube.com/@creator", **kwargs
    )
    plain = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://www.youtube.com/@creator",
        kol_pool_id=1,
        staff={"id": 10, "user_id": 110},
        enforce_target_write=True,
    )

    assert first == {"status": "queued", "job_id": 90}
    assert concurrent == {"status": "already_queued", "job_id": 90}
    assert plain == {"status": "queued", "job_id": 91}
    assert received_keys[0] == received_keys[1]
    assert received_keys[2] != received_keys[0]
    assert len(jobs_by_key) == 2


def test_plain_job_receipt_cannot_advance_subscription_even_if_enqueue_returns_it(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    original_due = monitor_conn.execute(
        "SELECT next_due_at FROM vkpi_kol_content_monitoring_subscriptions WHERE id=?",
        (subscription_id,),
    ).fetchone()[0]
    monitor_conn.execute(
        """
        INSERT INTO apify_jobs (id, job_type, payload, status)
        VALUES (90, 'kol_profile_deep_crawl', ?, 'running')
        """,
        (json.dumps({"url": "https://www.youtube.com/@creator"}),),
    )
    monitor_conn.commit()
    monkeypatch.setattr(
        content_monitoring.video_metric_refresh,
        "authorize_video_metric_refresh_actor",
        lambda *_a, **_k: ({"id": 10, "user_id": 110}, ""),
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_a, **_k: {"status": "already_queued", "job_id": 90},
    )

    result = content_monitoring.enqueue_due_content_monitoring(
        monitor_conn,
        now=NOW,
        limit=1,
    )
    row = dict(
        monitor_conn.execute(
            """
            SELECT next_due_at, last_job_id, last_job_status
            FROM vkpi_kol_content_monitoring_subscriptions WHERE id=?
            """,
            (subscription_id,),
        ).fetchone()
    )

    assert result["failed"] == 1
    assert result["already_queued"] == 0
    assert row == {
        "next_due_at": original_due,
        "last_job_id": None,
        "last_job_status": "",
    }


def test_terminal_job_that_wins_enqueue_binding_race_is_reconciled(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    _install_monitor_queue_fakes(monkeypatch, monitor_conn)

    def finish_before_binding(conn, *, job_type, payload, idempotency_key):
        cursor = conn.execute(
            """
            INSERT INTO apify_jobs (job_type, payload, idempotency_key, status)
            VALUES (?, ?, ?, 'done')
            """,
            (job_type, json.dumps(payload), idempotency_key),
        )
        return {"id": int(cursor.lastrowid), "payload": payload, "status": "done"}, True

    monkeypatch.setattr(
        url_deep_crawl_queue,
        "enqueue_active_apify_job",
        finish_before_binding,
    )

    result = content_monitoring.enqueue_due_content_monitoring(
        monitor_conn,
        now=NOW,
        limit=1,
    )
    row = dict(
        monitor_conn.execute(
            """
            SELECT next_due_at, last_job_id, last_job_status, last_success_at
            FROM vkpi_kol_content_monitoring_subscriptions WHERE id=?
            """,
            (subscription_id,),
        ).fetchone()
    )

    assert result["queued"] == 1
    assert row == {
        "next_due_at": "2026-08-22T12:00:00+00:00",
        "last_job_id": 1,
        "last_job_status": "done",
        "last_success_at": NOW.isoformat(),
    }


def test_scheduler_revocation_pauses_before_enqueue_provider_bomb(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_subscription(monitor_conn)
    monkeypatch.setattr(
        content_monitoring.video_metric_refresh,
        "authorize_video_metric_refresh_actor",
        lambda *_a, **_k: (None, "video_refresh_target_permission_revoked"),
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "enqueue_profile_deep_crawl_job",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider/queue reached")),
    )

    result = content_monitoring.enqueue_due_content_monitoring(
        monitor_conn,
        now=NOW,
        limit=1,
    )
    row = dict(
        monitor_conn.execute(
            "SELECT status, generation, pause_reason, last_job_status FROM vkpi_kol_content_monitoring_subscriptions"
        ).fetchone()
    )

    assert result["paused"] == 1
    assert result["provider_calls_performed"] is False
    assert row == {
        "status": "paused",
        "generation": 2,
        "pause_reason": "video_refresh_target_permission_revoked",
        "last_job_status": "blocked",
    }


def test_profile_enqueue_persists_actor_target_subscription_and_no_followup_flags(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    captured: dict = {}
    target_fence = {
        "version": 1,
        "kol_pool_id": 1,
        "staff_id": 10,
        "user_id": 110,
        "canonical_profile_url": "https://www.youtube.com/@creator",
        "platform": "youtube",
        "stable_identity_key": "youtube:handle:creator",
    }
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: monitor_conn)
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_build_target_write_fence",
        lambda *_a, **_k: dict(target_fence),
    )

    def enqueue(_conn, *, job_type, payload, idempotency_key):
        captured.update(payload)
        return {"id": 91}, True

    monkeypatch.setattr(url_deep_crawl_queue, "enqueue_active_apify_job", enqueue)

    result = url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        "https://youtube.com/@creator?utm_source=test",
        kol_pool_id=1,
        max_posts=12,
        staff={"id": 10, "user_id": 110},
        queue_lane="batch",
        enforce_target_write=True,
        content_monitor_fence={
            "version": 1,
            "subscription_id": subscription_id,
            "staff_id": 10,
            "kol_pool_id": 1,
            "generation": 1,
        },
        suppress_final_v1=True,
        suppress_contact_followup=True,
        suppress_profile_followups=True,
    )

    assert result == {"status": "queued", "job_id": 91}
    assert captured["url"] == "https://www.youtube.com/@creator"
    assert captured["max_posts"] == 12
    assert captured["target_write_fence"] == target_fence
    assert captured["content_monitor_fence"] == {
        "version": 1,
        "subscription_id": subscription_id,
        "staff_id": 10,
        "kol_pool_id": 1,
        "generation": 1,
        "window_max_posts": 12,
    }
    assert captured["monitoring_window"]["full_history"] is False
    assert captured["suppress_final_v1"] is True
    assert captured["suppress_contact_followup"] is True
    assert captured["suppress_profile_followups"] is True


def test_cancelled_subscription_blocks_worker_before_provider(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn, status="paused", generation=2)
    payload = {
        "url": "https://www.youtube.com/@creator",
        "kol_pool_id": 1,
        "mode": "account_deep",
        "max_posts": 12,
        "representative_video_limit": 1,
        "target_write_fence": {
            "version": 1,
            "kol_pool_id": 1,
            "staff_id": 10,
            "user_id": 110,
            "canonical_profile_url": "https://www.youtube.com/@creator",
            "platform": "youtube",
            "stable_identity_key": "youtube:handle:creator",
        },
        "content_monitor_fence": {
            "version": 1,
            "subscription_id": subscription_id,
            "staff_id": 10,
            "kol_pool_id": 1,
            "generation": 1,
        },
    }
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: monitor_conn)
    monkeypatch.setattr(
        "app.domains.kol.video_metric_refresh.authorize_video_metric_refresh_actor",
        lambda *_a, **_k: ({"id": 10, "user_id": 110}, ""),
    )
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_validated_profile_identity",
        lambda *_a, **_k: {
            "canonical_profile_url": "https://www.youtube.com/@creator",
            "platform": "youtube",
            "stable_identity_key": "youtube:handle:creator",
        },
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider reached")),
    )

    with pytest.raises(VideoTrackingError) as exc_info:
        url_deep_crawl_queue.run_profile_deep_crawl_for_job(payload)

    assert exc_info.value.code == "kol_content_monitor_cancelled"


def test_profile_url_drift_blocks_before_provider(
    monitor_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription_id = _seed_subscription(monitor_conn)
    payload = {
        "url": "https://www.youtube.com/@creator",
        "kol_pool_id": 1,
        "mode": "account_deep",
        "target_write_fence": {
            "version": 1,
            "kol_pool_id": 1,
            "staff_id": 10,
            "user_id": 110,
            "canonical_profile_url": "https://www.youtube.com/@creator",
            "platform": "youtube",
            "stable_identity_key": "youtube:handle:creator",
        },
        "content_monitor_fence": {
            "version": 1,
            "subscription_id": subscription_id,
            "staff_id": 10,
            "kol_pool_id": 1,
            "generation": 1,
        },
    }
    monitor_conn.execute(
        "UPDATE vkpi_kol_pool SET handle='other', profile_url='https://www.youtube.com/@other' WHERE id=1"
    )
    monitor_conn.commit()
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: monitor_conn)
    monkeypatch.setattr(
        "app.domains.kol.video_metric_refresh.authorize_video_metric_refresh_actor",
        lambda *_a, **_k: ({"id": 10, "user_id": 110}, ""),
    )
    monkeypatch.setattr(
        url_deep_crawl_queue,
        "_validated_profile_identity",
        lambda row, _url: {
            "canonical_profile_url": str(row.get("profile_url") or ""),
            "platform": "youtube",
            "stable_identity_key": f"youtube:handle:{row.get('handle')}",
        },
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("provider reached")),
    )

    with pytest.raises(VideoTrackingError) as exc_info:
        url_deep_crawl_queue.run_profile_deep_crawl_for_job(payload)

    assert exc_info.value.code == "kol_profile_target_drifted"
