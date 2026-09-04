from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_jobs as router_mod
from app.api.routers import vkpi_my_kol as my_kol_router
from app.api.routers import vkpi_projects as projects_router
from app.domains.kol import (
    url_deep_crawl,
    url_deep_crawl_queue,
    video_tracking,
)
from app.workers import apify_jobs_worker_handlers as worker_handlers


PROFILE_URL = "https://www.youtube.com/@Creator"


def test_legacy_id_only_profile_crawl_route_is_terminal_and_provider_free(monkeypatch):
    calls = {"db": 0, "provider": 0}

    def db_bomb():
        calls["db"] += 1
        raise AssertionError("retired route must not access business DB")

    def provider_bomb(*_args, **_kwargs):
        calls["provider"] += 1
        raise AssertionError("retired route must not enqueue a provider job")

    monkeypatch.setattr("app.db.connection.get_conn", db_bomb)
    monkeypatch.setattr(url_deep_crawl, "enqueue_profile_deep_crawl_job", provider_bomb)

    with pytest.raises(HTTPException) as caught:
        projects_router.enqueue_kol_profile_crawl(
            999999,
            staff={"id": 10, "user_id": 110, "role": "member"},
        )

    assert caught.value.status_code == 410
    assert caught.value.detail == {
        "code": "kol_profile_crawl_route_retired",
        "replacement": "/api/admin/vkpi/kol-pool/profile-deep-crawl/enqueue",
    }
    assert calls == {"db": 0, "provider": 0}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            email TEXT NOT NULL
        );
        CREATE TABLE staff (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            permissions_json TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            is_owner INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            duplicate_of_id INTEGER,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            raw_platform_data TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE vkpi_kol_pool_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL
        );
        CREATE TABLE vkpi_kol_pool_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_url_deep_crawl_runs (
            kol_pool_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO users (id, status, email) VALUES (?, 'active', ?)",
        [(110, "owner@example.test"), (120, "shared@example.test")],
    )
    conn.executemany(
        """
        INSERT INTO staff (id, user_id, role, permissions_json, active)
        VALUES (?, ?, 'member', '{"vkpi":"write"}', 1)
        """,
        [(10, 110), (20, 120)],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool (id, platform, handle, profile_url) VALUES (?, 'youtube', ?, ?)",
        [(1, "@Creator", PROFILE_URL), (2, "@Other", "https://www.youtube.com/@Other")],
    )
    conn.execute(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (1, 10)"
    )
    conn.execute(
        "INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id) VALUES (1, 20)"
    )
    conn.commit()
    return conn


def _fake_enqueue(conn, *, job_type, payload, idempotency_key):
    existing = conn.execute(
        "SELECT id, job_type, payload, status FROM apify_jobs WHERE idempotency_key=? AND status IN ('queued','running')",
        (idempotency_key,),
    ).fetchone()
    if existing:
        item = dict(existing)
        item["payload"] = json.loads(item["payload"])
        return item, False
    cursor = conn.execute(
        "INSERT INTO apify_jobs (job_type, payload, idempotency_key, status) VALUES (?, ?, ?, 'queued')",
        (job_type, json.dumps(payload), idempotency_key),
    )
    return {"id": int(cursor.lastrowid), "job_type": job_type, "payload": payload, "status": "queued"}, True


@pytest.fixture()
def crawl_conn(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(url_deep_crawl_queue, "get_conn", lambda: conn)
    monkeypatch.setattr(url_deep_crawl_queue, "enqueue_active_apify_job", _fake_enqueue)
    yield conn
    conn.close()


def _owned_staff() -> dict:
    return {"id": 10, "user_id": 110, "role": "member", "permissions_json": '{"vkpi":"write"}', "active": 1}


def _shared_staff() -> dict:
    return {"id": 20, "user_id": 120, "role": "member", "permissions_json": '{"vkpi":"write"}', "active": 1}


def _queued_payload(conn: sqlite3.Connection) -> dict:
    return json.loads(conn.execute("SELECT payload FROM apify_jobs ORDER BY id DESC LIMIT 1").fetchone()[0])


@pytest.mark.parametrize("subscription_status", [None, "paused"])
def test_one_shot_refresh_endpoint_never_registers_or_reactivates_subscription(
    monkeypatch,
    subscription_status,
):
    state = {"status": subscription_status}

    class Conn:
        commits = 0

        def commit(self):
            self.commits += 1

        def rollback(self):
            raise AssertionError("successful one-shot refresh must not roll back")

    conn = Conn()

    def queue_once(_conn, **kwargs):
        assert _conn is conn
        assert kwargs["register_tracking"] is False
        # Model the subscription side effect explicitly: only a true register
        # flag may create/reactivate the durable tracking row.
        if kwargs["register_tracking"]:
            state["status"] = "active"
        return {"status": "queued", "metric_tracking_status": "not_registered"}

    monkeypatch.setattr(my_kol_router, "get_conn", lambda: conn)
    monkeypatch.setattr(video_tracking, "queue_evidence_refresh", queue_once)
    result = my_kol_router.my_kol_refresh_video_endpoint(
        kol_pool_id=1,
        evidence_id=101,
        staff=_owned_staff(),
    )
    assert result["metric_tracking_status"] == "not_registered"
    assert state["status"] == subscription_status
    assert conn.commits == 1


def test_profile_deep_crawl_freshness_keeps_nonempty_id_probe(crawl_conn):
    assert url_deep_crawl_queue.profile_deep_crawl_is_fresh(None) is False
    assert url_deep_crawl_queue.profile_deep_crawl_is_fresh(1) is False
    crawl_conn.execute(
        "INSERT INTO vkpi_kol_url_deep_crawl_runs (kol_pool_id, status, created_at) VALUES (1, 'ready', ?)",
        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    crawl_conn.commit()
    assert url_deep_crawl_queue.profile_deep_crawl_is_fresh(1) is True


def test_route_rejects_shared_idor_and_profile_url_mismatch_before_enqueue(crawl_conn):
    with pytest.raises(HTTPException) as shared_error:
        router_mod.enqueue_kol_profile_deep_crawl(
            body={"url": PROFILE_URL, "kol_pool_id": 1},
            staff=_shared_staff(),
        )
    assert shared_error.value.status_code == 403
    assert shared_error.value.detail == "my_kol_video_write_forbidden"

    with pytest.raises(HTTPException) as mismatch_error:
        router_mod.enqueue_kol_profile_deep_crawl(
            body={"url": "https://www.youtube.com/@Other", "kol_pool_id": 1},
            staff=_owned_staff(),
        )
    assert mismatch_error.value.status_code == 409
    assert mismatch_error.value.detail == "kol_profile_identity_mismatch"
    assert crawl_conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0] == 0


def test_equivalent_platform_identity_is_canonicalized_to_stored_profile(crawl_conn):
    result = router_mod.enqueue_kol_profile_deep_crawl(
        body={"url": "http://youtube.com/c/creator/?utm_source=test", "kol_pool_id": 1},
        staff=_owned_staff(),
    )
    assert result["status"] == "queued"
    payload = _queued_payload(crawl_conn)
    assert payload["url"] == "https://youtube.com/@Creator"
    assert payload["target_write_fence"]["stable_identity_key"] == "youtube:handle:creator"
    assert payload["target_write_fence"]["staff_id"] == 10


@pytest.mark.parametrize(
    ("revocation_sql", "expected_code"),
    [
        ("UPDATE staff SET active=0 WHERE id=10", "kol_profile_actor_revoked"),
        ("UPDATE staff SET permissions_json='{}' WHERE id=10", "kol_profile_write_permission_revoked"),
        ("DELETE FROM vkpi_kol_pool_favorites WHERE kol_pool_id=1 AND staff_id=10", "my_kol_video_write_forbidden"),
    ],
)
def test_worker_rechecks_actor_permission_and_ownership_before_provider(
    crawl_conn,
    monkeypatch,
    revocation_sql,
    expected_code,
):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        staff=_owned_staff(),
        enforce_target_write=True,
    )
    payload = _queued_payload(crawl_conn)
    crawl_conn.execute(revocation_sql)
    crawl_conn.commit()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda body: provider_calls.append(dict(body)) or {"status": "ready"},
    )

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_queue.run_profile_deep_crawl_for_job(payload)
    assert error.value.code == expected_code
    assert provider_calls == []


def test_durable_handler_terminalizes_revoked_fence_as_blocked_without_provider(
    crawl_conn,
    monkeypatch,
):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        staff=_owned_staff(),
        enforce_target_write=True,
    )
    payload = _queued_payload(crawl_conn)
    crawl_conn.execute("UPDATE staff SET active=0 WHERE id=10")
    crawl_conn.commit()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda body: provider_calls.append(dict(body)) or {"status": "ready"},
    )
    monkeypatch.setattr(worker_handlers, "_resolve_job_staff", lambda *_args: _owned_staff())
    monkeypatch.setattr(worker_handlers, "db_connection_sync_scope", nullcontext)

    state: dict[str, object] = {"status": "running", "last_error": None}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            assert "status='blocked'" in " ".join(str(sql).split())
            state["status"] = "blocked"
            state["last_error"] = params[0]

    class WorkerConn:
        def transaction(self):
            return nullcontext()

        def cursor(self, **_kwargs):
            return Cursor()

    worker_handlers._process_kol_profile_deep_crawl(
        WorkerConn(),
        {"id": 1},
        payload,
    )
    assert state == {"status": "blocked", "last_error": "kol_profile_actor_revoked"}
    assert provider_calls == []


@pytest.mark.parametrize("drift", ["target", "payload", "stored"])
def test_worker_rejects_profile_url_toctou_before_provider(crawl_conn, monkeypatch, drift):
    url_deep_crawl_queue.enqueue_profile_deep_crawl_job(
        PROFILE_URL,
        kol_pool_id=1,
        staff=_owned_staff(),
        enforce_target_write=True,
    )
    payload = _queued_payload(crawl_conn)
    if drift == "target":
        payload["kol_pool_id"] = 2
    elif drift == "payload":
        payload["url"] = "https://www.youtube.com/@Other"
    else:
        crawl_conn.execute(
            "UPDATE vkpi_kol_pool SET handle='@Other', profile_url='https://www.youtube.com/@Other' WHERE id=1"
        )
        crawl_conn.commit()
    provider_calls: list[dict] = []
    monkeypatch.setattr(
        url_deep_crawl,
        "dry_run_url_deep_crawl",
        lambda body: provider_calls.append(dict(body)) or {"status": "ready"},
    )

    with pytest.raises(video_tracking.VideoTrackingError) as error:
        url_deep_crawl_queue.run_profile_deep_crawl_for_job(payload)
    assert error.value.code in {"kol_profile_identity_mismatch", "kol_profile_target_drifted"}
    assert provider_calls == []
