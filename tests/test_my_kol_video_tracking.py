from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_my_kol as router_mod  # noqa: E402
from app.domains import content_metric_snapshots  # noqa: E402
from app.domains.kol import video_metric_refresh, video_tracking  # noqa: E402
from app.workers.apify_job_lane import queue_service_priority  # noqa: E402
from app.workers.apify_job_resource_slots import resource_group_for_job  # noqa: E402
from app.workers import apify_jobs_worker as pg_worker  # noqa: E402
from app.workers import apify_jobs_worker_handlers as worker_handlers  # noqa: E402
from app.platform.apify_budget import (  # noqa: E402
    ApifyBudgetBlocked,
    ApifyBudgetDecision,
    ApifyProviderReplayBlocked,
)


VIDEO_URL = "https://www.youtube.com/watch?v=abcDEF12345"


def _refresh_payload(**overrides):
    return {
        "evidence_id": 101,
        "kol_pool_id": 1,
        "platform": "youtube",
        "content_url": VIDEO_URL,
        "staff_id": 10,
        "triggered_by_user_id": 110,
        **overrides,
    }


def _tracking_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("NOW", 0, lambda: "2026-08-21T12:00:00+00:00")
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
            suspended_at TEXT,
            is_owner INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE vkpi_kol_pool (
            id INTEGER PRIMARY KEY,
            duplicate_of_id INTEGER,
            viltrox_fit_score REAL,
            viltrox_fit_reason TEXT
        );
        CREATE TABLE vkpi_kol_pool_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL,
            UNIQUE(kol_pool_id, staff_id)
        );
        CREATE TABLE vkpi_kol_pool_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            staff_id INTEGER NOT NULL
        );
        CREATE TABLE vkpi_products (
            sku TEXT PRIMARY KEY,
            model_name TEXT,
            marketing_name TEXT
        );
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            content_url TEXT NOT NULL UNIQUE,
            platform TEXT,
            evidence_type TEXT DEFAULT 'video',
            is_active INTEGER DEFAULT 1,
            channel_id TEXT,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            share_count INTEGER,
            metrics_scraped_at TEXT,
            metrics_source TEXT,
            published_at_norm TEXT,
            publish_date TEXT,
            posted_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(kol_pool_id) REFERENCES vkpi_kol_pool(id)
        );
        CREATE TABLE vkpi_kol_video_product_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_id INTEGER NOT NULL,
            product_sku TEXT NOT NULL,
            relation_type TEXT NOT NULL DEFAULT 'manual',
            source TEXT NOT NULL,
            confidence REAL NOT NULL,
            created_by_staff_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(evidence_id, product_sku, relation_type),
            FOREIGN KEY(evidence_id) REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE,
            FOREIGN KEY(product_sku) REFERENCES vkpi_products(sku)
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE vkpi_kol_video_metric_tracking (
            evidence_id INTEGER PRIMARY KEY,
            tracked_by_staff_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL,
            last_enqueued_at TEXT,
            last_job_id INTEGER,
            last_enqueue_status TEXT NOT NULL DEFAULT '',
            pause_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(evidence_id) REFERENCES vkpi_kol_video_evidence(id),
            FOREIGN KEY(tracked_by_staff_id) REFERENCES staff(id)
        );
        """
    )
    conn.executemany(
        "INSERT INTO users (id, status, email) VALUES (?, 'active', ?)",
        [(110, "member@example.test"), (120, "shared@example.test"), (130, "manager@example.test")],
    )
    conn.executemany(
        """
        INSERT INTO staff (id, user_id, role, permissions_json, active)
        VALUES (?, ?, ?, ?, 1)
        """,
        [
            (10, 110, "member", '{"vkpi":"write"}'),
            (20, 120, "member", '{"vkpi":"write"}'),
            (30, 130, "manager", '{}'),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool (id, viltrox_fit_score, viltrox_fit_reason) VALUES (?, ?, ?)",
        [(1, 88.5, "stable"), (2, 73.0, "other")],
    )
    conn.execute(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (1, 10)"
    )
    conn.execute(
        "INSERT INTO vkpi_kol_pool_members (kol_pool_id, staff_id) VALUES (1, 20)"
    )
    conn.executemany(
        "INSERT INTO vkpi_products (sku) VALUES (?)",
        [("SKU-A",), ("SKU-B",), ("SKU-C",), ("SKU-D",), ("SKU-E",), ("SKU-F",)],
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, content_url, platform, evidence_type, is_active,
            channel_id, view_count, like_count, comment_count, share_count,
            metrics_scraped_at, metrics_source, published_at_norm, posted_at,
            created_at, updated_at
        ) VALUES (101, 1, 'https://www.youtube.com/watch?v=abcDEF12345',
                  'youtube', 'video', 1, 'UC-owner', 100, 10, 2, NULL,
                  '2026-08-20T00:00:00+00:00', 'legacy',
                  '2026-08-19T00:00:00+00:00', '2026-08-19T00:00:00+00:00',
                  '2026-08-19T00:00:00+00:00', '2026-08-20T00:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, content_url, platform, evidence_type, is_active,
            channel_id, created_at
        ) VALUES (202, 2, 'https://www.youtube.com/watch?v=other123456',
                  'youtube', 'video', 1, 'UC-other', '2026-07-01T00:00:00+00:00')
        """
    )
    conn.commit()
    return conn


def _fake_active_enqueue(conn, *, job_type, payload, idempotency_key):
    existing = conn.execute(
        """
        SELECT id, job_type, payload, status
        FROM apify_jobs
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
        "INSERT INTO apify_jobs (job_type, payload, idempotency_key, status) VALUES (?, ?, ?, 'queued')",
        (job_type, json.dumps(payload), idempotency_key),
    )
    return {
        "id": int(cursor.lastrowid),
        "job_type": job_type,
        "payload": payload,
        "status": "queued",
    }, True


@pytest.fixture()
def tracking_conn(monkeypatch):
    conn = _tracking_conn()
    monkeypatch.setattr(
        video_metric_refresh,
        "enqueue_active_apify_job",
        _fake_active_enqueue,
    )
    yield conn
    conn.close()


def _job_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0])


def _link_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM vkpi_kol_video_product_links").fetchone()[0]
    )


def test_existing_video_links_skus_and_refresh_enqueue_are_idempotent(
    tracking_conn, monkeypatch
):
    def provider_bomb(_url):
        raise AssertionError("HTTP enqueue path called provider")

    monkeypatch.setattr(video_metric_refresh, "_fetch_video_metadata", provider_bomb)
    staff = {"id": 10, "user_id": 110, "role": "member"}
    first = video_tracking.queue_tracked_video(
        tracking_conn,
        kol_pool_id=1,
        content_url="https://www.youtube.com/watch?v=abcDEF12345",
        product_skus=["SKU-A", "SKU-B", "SKU-A"],
        staff=staff,
    )
    again = video_tracking.queue_tracked_video(
        tracking_conn,
        kol_pool_id=1,
        content_url="https://youtu.be/abcDEF12345?feature=share",
        product_skus=["SKU-A", "SKU-B"],
        staff=staff,
    )

    assert first["status"] == "queued"
    assert again["status"] == "already_queued"
    assert first["job_id"] == again["job_id"]
    assert first["evidence_id"] == 101
    assert first["provider_calls_performed"] is False
    assert first["product_links_created"] == 2
    assert again["product_links_created"] == 0
    assert first["product_skus"] == ["SKU-A", "SKU-B"]
    assert _job_count(tracking_conn) == 1
    assert _link_count(tracking_conn) == 2
    assert tracking_conn.execute(
        "SELECT COUNT(*) FROM vkpi_kol_video_metric_tracking WHERE status='active'"
    ).fetchone()[0] == 1
    payload = json.loads(
        tracking_conn.execute("SELECT payload FROM apify_jobs").fetchone()[0]
    )
    assert payload["queue_lane"] == "interactive"
    assert payload["target_type"] == "kol_video_evidence"
    assert payload["target_id"] == "101"
    assert payload["kol_pool_id"] == 1
    assert payload["staff_id"] == 10
    assert "provider" not in payload
    score = tracking_conn.execute(
        "SELECT viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=1"
    ).fetchone()
    assert tuple(score) == (88.5, "stable")


def test_manager_can_write_but_shared_only_member_cannot(tracking_conn):
    manager = video_tracking.queue_evidence_refresh(
        tracking_conn,
        kol_pool_id=1,
        evidence_id=101,
        staff={"id": 30, "role": "manager"},
    )
    assert manager["status"] == "queued"

    with pytest.raises(video_tracking.VideoTrackingError) as shared_error:
        video_tracking.queue_evidence_refresh(
            tracking_conn,
            kol_pool_id=1,
            evidence_id=101,
            staff={"id": 20, "role": "member"},
        )
    assert shared_error.value.status_code == 403
    assert shared_error.value.code == "my_kol_video_write_forbidden"

    with pytest.raises(video_tracking.VideoTrackingError) as identity_error:
        video_tracking.queue_evidence_refresh(
            tracking_conn,
            kol_pool_id=1,
            evidence_id=101,
            staff=None,
        )
    assert identity_error.value.code == "staff_identity_required"
    assert _job_count(tracking_conn) == 1


@pytest.mark.parametrize(
    "url",
    [
        "youtube.com/watch?v=abcDEF12345",
        "ftp://www.youtube.com/watch?v=abcDEF12345",
        "https://www.youtube.com/@creator",
        "https://user:pass@www.youtube.com/watch?v=abcDEF12345",
        "https://www.youtube.com:444/watch?v=abcDEF12345",
        "https://www.youtube.com/watch?v=abcDEF12345 bad",
        "https://youtube.com.evil.test/watch?v=abcDEF12345",
        "https://localhost/watch?v=abcDEF12345",
        "https://vimeo.com/123456",
        "https://www.tiktok.com/@creator",
    ],
)
def test_invalid_or_non_video_urls_enqueue_nothing(tracking_conn, url):
    with pytest.raises(video_tracking.VideoTrackingError):
        video_tracking.queue_tracked_video(
            tracking_conn,
            kol_pool_id=1,
            content_url=url,
            product_skus=[],
            staff={"id": 10, "role": "member"},
        )
    assert _job_count(tracking_conn) == 0
    assert _link_count(tracking_conn) == 0


def test_valid_url_cannot_match_a_stored_evil_host_identity(tracking_conn):
    tracking_conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, content_url, platform, evidence_type, is_active
        ) VALUES (303, 1, 'https://instagram.com.evil.test/p/ABC123/',
                  'instagram', 'video', 1)
        """
    )
    with pytest.raises(video_tracking.VideoTrackingError) as error:
        video_tracking.queue_tracked_video(
            tracking_conn,
            kol_pool_id=1,
            content_url="https://www.instagram.com/p/ABC123/",
            product_skus=[],
            staff={"id": 10, "role": "member"},
        )
    assert error.value.code == "new_video_target_resolution_required"
    assert _job_count(tracking_conn) == 0
    assert _link_count(tracking_conn) == 0


def test_unknown_or_too_many_skus_leave_no_partial_writes(tracking_conn):
    for skus, expected_code in (
        (["SKU-A", "UNKNOWN"], "unknown_product_sku"),
        (
            ["SKU-A", "SKU-B", "SKU-C", "SKU-D", "SKU-E", "SKU-F"],
            "too_many_product_skus",
        ),
    ):
        with pytest.raises(video_tracking.VideoTrackingError) as error:
            video_tracking.queue_tracked_video(
                tracking_conn,
                kol_pool_id=1,
                content_url="https://www.youtube.com/watch?v=abcDEF12345",
                product_skus=skus,
                staff={"id": 10, "role": "member"},
            )
        assert error.value.code == expected_code
    assert _job_count(tracking_conn) == 0
    assert _link_count(tracking_conn) == 0


def test_cross_kol_conflict_and_new_url_are_fail_closed(tracking_conn):
    with pytest.raises(video_tracking.VideoTrackingError) as conflict:
        video_tracking.queue_tracked_video(
            tracking_conn,
            kol_pool_id=1,
            content_url="https://youtu.be/other123456?feature=share",
            product_skus=["SKU-A"],
            staff={"id": 10, "role": "member"},
        )
    assert conflict.value.status_code == 409
    assert conflict.value.code == "video_evidence_owned_by_other_kol"

    with pytest.raises(video_tracking.VideoTrackingError) as unseen:
        video_tracking.queue_tracked_video(
            tracking_conn,
            kol_pool_id=1,
            content_url="https://www.youtube.com/watch?v=newVideo1234",
            product_skus=["SKU-A"],
            staff={"id": 10, "role": "member"},
        )
    assert unseen.value.code == "new_video_target_resolution_required"
    assert _job_count(tracking_conn) == 0
    assert _link_count(tracking_conn) == 0


def test_evidence_refresh_rejects_wrong_kol_and_missing_evidence(tracking_conn):
    with pytest.raises(video_tracking.VideoTrackingError) as conflict:
        video_tracking.queue_evidence_refresh(
            tracking_conn,
            kol_pool_id=1,
            evidence_id=202,
            staff={"id": 10, "role": "member"},
        )
    assert conflict.value.status_code == 409
    with pytest.raises(video_tracking.VideoTrackingError) as missing:
        video_tracking.queue_evidence_refresh(
            tracking_conn,
            kol_pool_id=1,
            evidence_id=999,
            staff={"id": 10, "role": "member"},
        )
    assert missing.value.status_code == 404
    assert _job_count(tracking_conn) == 0


def test_router_maps_domain_error_and_rolls_back(tracking_conn, monkeypatch):
    monkeypatch.setattr(router_mod, "get_conn", lambda: tracking_conn)
    with pytest.raises(HTTPException) as error:
        router_mod.my_kol_track_video_endpoint(
            kol_pool_id=1,
            body={"content_url": "https://www.youtube.com/@creator"},
            staff={"id": 10, "role": "member"},
        )
    assert error.value.status_code == 422
    assert error.value.detail == "explicit_video_url_required"
    assert _job_count(tracking_conn) == 0


def test_router_enqueue_failure_rolls_back_product_links(tracking_conn, monkeypatch):
    monkeypatch.setattr(router_mod, "get_conn", lambda: tracking_conn)

    def enqueue_bomb(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(video_tracking, "enqueue_video_metric_refresh", enqueue_bomb)
    with pytest.raises(RuntimeError, match="queue unavailable"):
        router_mod.my_kol_track_video_endpoint(
            kol_pool_id=1,
            body={
                "content_url": "https://www.youtube.com/watch?v=abcDEF12345",
                "product_skus": ["SKU-A"],
            },
            staff={"id": 10, "role": "member"},
        )
    assert _link_count(tracking_conn) == 0
    assert _job_count(tracking_conn) == 0
    assert tracking_conn.execute(
        "SELECT COUNT(*) FROM vkpi_kol_video_metric_tracking"
    ).fetchone()[0] == 0


def test_product_link_read_projection_is_batched_and_migration_safe(tracking_conn):
    tracking_conn.execute(
        """
        INSERT INTO vkpi_kol_video_product_links (
            evidence_id, product_sku, relation_type, source, confidence,
            created_by_staff_id, created_at, updated_at
        ) VALUES (101, 'SKU-A', 'manual', 'my_kol_video_tracking', 1, 10, NOW(), NOW())
        """
    )
    links = video_tracking.product_links_for_evidence(tracking_conn, [101, 202])
    assert [item["product_sku"] for item in links[101]] == ["SKU-A"]
    assert links[202] == []

    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    assert video_tracking.product_links_for_evidence(empty, [101]) == {101: []}
    empty.close()


def test_worker_refresh_success_appends_snapshot_without_touching_score(
    tracking_conn, monkeypatch
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: {
            "platform": "youtube",
            "content_url": "https://youtu.be/abcDEF12345?feature=share",
            "scrape_source": "youtube_api",
            "scrape_status": "success",
            "channel_id": "UC-owner",
            "view_count": 150,
            "like_count": 15,
            "comment_count": 3,
            "share_count": None,
        },
    )
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(),
        conn=tracking_conn,
    )
    assert result["status"] == "success"
    evidence = dict(
        tracking_conn.execute(
            "SELECT view_count, like_count, comment_count, share_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
        ).fetchone()
    )
    assert evidence == {
        "view_count": 150,
        "like_count": 15,
        "comment_count": 3,
        "share_count": None,
        "metrics_source": "youtube_api",
    }
    snapshot = dict(
        tracking_conn.execute(
            "SELECT status, views, likes, comments, shares FROM vkpi_content_metric_snapshots"
        ).fetchone()
    )
    assert snapshot == {
        "status": "success",
        "views": 150,
        "likes": 15,
        "comments": 3,
        "shares": None,
    }
    score = tracking_conn.execute(
        "SELECT viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=1"
    ).fetchone()
    assert tuple(score) == (88.5, "stable")


def test_worker_all_metrics_missing_records_failure_and_preserves_latest(
    tracking_conn, monkeypatch
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: {
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=abcDEF12345",
            "scrape_source": "apify",
            "scrape_status": "success",
            "channel_id": "UC-owner",
            "view_count": None,
            "like_count": None,
            "comment_count": None,
            "share_count": None,
            "apify_run_id": "run-empty",
        },
    )
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(),
        conn=tracking_conn,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "all_metrics_missing"
    evidence = tracking_conn.execute(
        "SELECT view_count, like_count, comment_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
    ).fetchone()
    assert tuple(evidence) == (100, 10, 2, "legacy")
    snapshot = tracking_conn.execute(
        "SELECT status, views, likes, comments, shares, error_code FROM vkpi_content_metric_snapshots"
    ).fetchone()
    assert tuple(snapshot) == (
        "failed",
        None,
        None,
        None,
        None,
        "all_metrics_missing",
    )


def test_worker_provider_exception_is_reduced_to_bounded_error_code(
    tracking_conn, monkeypatch
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)

    def provider_failure(_url):
        raise RuntimeError("secret-token-should-not-be-persisted")

    monkeypatch.setattr(video_metric_refresh, "_fetch_video_metadata", provider_failure)
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(),
        conn=tracking_conn,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "runtimeerror"
    assert "secret-token" not in json.dumps(result)
    snapshot = tracking_conn.execute(
        "SELECT status, error_code FROM vkpi_content_metric_snapshots"
    ).fetchone()
    assert tuple(snapshot) == ("failed", "runtimeerror")


@pytest.mark.parametrize(
    "guard_error",
    [
        ApifyBudgetBlocked(
            ApifyBudgetDecision(
                allowed=False,
                scope="provider:apify",
                estimated_cost_usd=1,
                reason="hard_stop_or_projected_cap",
                operation="evidence_video_metadata",
                actor_id="streamers/youtube-scraper",
                platform="youtube",
                source="test",
            )
        ),
        ApifyProviderReplayBlocked("ambiguous_provider_start"),
    ],
    ids=["budget_blocked", "provider_replay_blocked"],
)
def test_worker_provider_guards_propagate_without_snapshot_or_latest_write(
    tracking_conn, monkeypatch, guard_error
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)

    def guarded(_url):
        raise guard_error

    monkeypatch.setattr(video_metric_refresh, "_fetch_video_metadata", guarded)
    with pytest.raises(type(guard_error)):
        video_metric_refresh.run_video_metric_refresh_for_job(
            _refresh_payload(),
            conn=tracking_conn,
        )
    assert tracking_conn.execute(
        "SELECT COUNT(*) FROM vkpi_content_metric_snapshots"
    ).fetchone()[0] == 0
    latest = tracking_conn.execute(
        "SELECT view_count, like_count, comment_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
    ).fetchone()
    assert tuple(latest) == (100, 10, 2, "legacy")


@pytest.mark.parametrize(
    ("guard_error", "claim_state"),
    [
        (
            ApifyBudgetBlocked(
                ApifyBudgetDecision(
                    allowed=False,
                    scope="provider:apify",
                    estimated_cost_usd=1,
                    reason="hard_stop_or_projected_cap",
                    operation="evidence_video_metadata",
                    actor_id="streamers/youtube-scraper",
                    platform="youtube",
                    source="test",
                )
            ),
            "blocked",
        ),
        (ApifyProviderReplayBlocked("ambiguous_provider_start"), "unknown"),
    ],
    ids=["budget_blocked", "provider_replay_unknown"],
)
def test_pg_worker_preserves_guard_claim_and_blocks_durable_job(
    monkeypatch, guard_error, claim_state
):
    finalized: list[tuple[str, int, str]] = []

    monkeypatch.setattr(pg_worker, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(
        pg_worker,
        "_running_job_heartbeat",
        lambda *_args: nullcontext(),
    )
    monkeypatch.setattr(
        pg_worker,
        "acquire_provider_execution_claim",
        lambda *_args, **_kwargs: 7,
    )

    def guarded_process(_conn, _job):
        raise guard_error

    monkeypatch.setattr(pg_worker, "_process_claimed_job", guarded_process)
    monkeypatch.setattr(
        pg_worker,
        "finalize_provider_execution_claim",
        lambda task_id, fence, state: finalized.append((task_id, fence, state)) or True,
    )
    with pytest.raises(type(guard_error)):
        pg_worker._execute_claimed_job(
            object(),
            {"id": 77, "job_type": "kol_video_metric_refresh", "lease_owner": "worker-test"},
        )
    assert finalized == [("apify-job:77", 7, claim_state)]

    writes: list[tuple[str, object]] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            writes.append((str(sql), params))

    class Conn:
        def transaction(self):
            return nullcontext()

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(pg_worker, "_sync_search_session_job", lambda *_args, **_kwargs: None)
    pg_worker._fail_job(Conn(), 77, guard_error)
    assert len(writes) == 1
    assert "status='blocked'" in writes[0][0]
    assert guard_error.code in str(writes[0][1])


def test_worker_blocks_if_evidence_identity_changes_after_enqueue(
    tracking_conn, monkeypatch
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)
    tracking_conn.execute(
        "UPDATE vkpi_kol_video_evidence SET content_url=? WHERE id=101",
        ("https://www.youtube.com/watch?v=changedVideo9",),
    )

    def provider_bomb(_url):
        raise AssertionError("provider must not run after evidence identity changes")

    monkeypatch.setattr(video_metric_refresh, "_fetch_video_metadata", provider_bomb)
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(),
        conn=tracking_conn,
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "video_evidence_identity_changed_after_enqueue"
    assert result["provider_calls_performed"] is False
    assert tracking_conn.execute(
        "SELECT COUNT(*) FROM vkpi_content_metric_snapshots"
    ).fetchone()[0] == 0
    latest = tracking_conn.execute(
        "SELECT view_count, like_count, comment_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
    ).fetchone()
    assert tuple(latest) == (100, 10, 2, "legacy")


def test_worker_blocks_stored_evil_host_before_provider_or_snapshot(
    tracking_conn, monkeypatch
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)
    evil_url = "https://instagram.com.evil.test/p/ABC123/"
    tracking_conn.execute(
        "UPDATE vkpi_kol_video_evidence SET content_url=?, platform='instagram' WHERE id=101",
        (evil_url,),
    )

    def provider_bomb(_url):
        raise AssertionError("evil host must never reach provider")

    monkeypatch.setattr(video_metric_refresh, "_fetch_video_metadata", provider_bomb)
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(platform="instagram", content_url=evil_url),
        conn=tracking_conn,
    )
    assert result["status"] == "blocked"
    assert result["error_code"] == "video_evidence_identity_invalid"
    assert result["provider_calls_performed"] is False
    assert tracking_conn.execute(
        "SELECT COUNT(*) FROM vkpi_content_metric_snapshots"
    ).fetchone()[0] == 0
    latest = tracking_conn.execute(
        "SELECT view_count, like_count, comment_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
    ).fetchone()
    assert tuple(latest) == (100, 10, 2, "legacy")


def test_worker_rejects_same_channel_different_video_without_any_snapshot(
    tracking_conn, monkeypatch
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: {
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=different99",
            "scrape_source": "youtube_api",
            "scrape_status": "success",
            "channel_id": "UC-owner",
            "view_count": 999999,
            "like_count": 9999,
            "comment_count": 999,
            "share_count": 99,
        },
    )
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(),
        conn=tracking_conn,
    )
    assert result == {
        "status": "blocked",
        "evidence_id": 101,
        "error_code": "provider_video_mismatch",
        "snapshot_id": None,
        "provider_calls_performed": True,
    }
    assert tracking_conn.execute(
        "SELECT COUNT(*) FROM vkpi_content_metric_snapshots"
    ).fetchone()[0] == 0
    latest = tracking_conn.execute(
        "SELECT view_count, like_count, comment_count, share_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
    ).fetchone()
    assert tuple(latest) == (100, 10, 2, None, "legacy")


@pytest.mark.parametrize("media_kind", ["image", "carousel", "post"])
def test_worker_rejects_explicit_non_video_provider_media_kind(
    tracking_conn, monkeypatch, media_kind
):
    content_metric_snapshots.ensure_sqlite_schema(tracking_conn)
    monkeypatch.setattr(
        video_metric_refresh,
        "_fetch_video_metadata",
        lambda _url: {
            "platform": "youtube",
            "content_url": VIDEO_URL,
            "scrape_source": "apify",
            "scrape_status": "success",
            "media_kind": media_kind,
            "channel_id": "UC-owner",
            "view_count": 500,
            "like_count": 50,
            "comment_count": 5,
        },
    )
    result = video_metric_refresh.run_video_metric_refresh_for_job(
        _refresh_payload(),
        conn=tracking_conn,
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "provider_media_kind_mismatch"
    snapshot = tracking_conn.execute(
        "SELECT status, views, likes, comments, error_code FROM vkpi_content_metric_snapshots"
    ).fetchone()
    assert tuple(snapshot) == (
        "failed",
        None,
        None,
        None,
        "provider_media_kind_mismatch",
    )
    latest = tracking_conn.execute(
        "SELECT view_count, like_count, comment_count, metrics_source FROM vkpi_kol_video_evidence WHERE id=101"
    ).fetchone()
    assert tuple(latest) == (100, 10, 2, "legacy")


def test_refresh_job_is_in_reviewed_lane_and_resource_group():
    job = {"job_type": video_metric_refresh.VIDEO_METRIC_REFRESH_JOB_TYPE, "payload": {}}
    assert resource_group_for_job(job) == "profile_media"
    assert queue_service_priority(job["job_type"]) == 2


def test_worker_handler_persists_only_bounded_refresh_summary(monkeypatch):
    captured: dict[str, object] = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            captured["sql"] = str(sql)
            captured["params"] = params

    class QueueConn:
        def transaction(self):
            return nullcontext()

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(worker_handlers, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(
        video_metric_refresh,
        "run_video_metric_refresh_for_job",
        lambda _payload: {
            "status": "success",
            "evidence_id": 101,
            "snapshot_id": 501,
            "latest_updated": True,
            "provider": "youtube_api",
            "provider_calls_performed": True,
        },
    )
    payload = {"evidence_id": 101, "kol_pool_id": 1}
    worker_handlers._process_kol_video_metric_refresh(
        QueueConn(),
        {"id": 99},
        payload,
    )
    params = captured["params"]
    assert params[0] == "done"
    persisted = json.loads(params[2])
    assert persisted["video_metric_refresh_result"]["snapshot_id"] == 501
    assert "raw_provider_response" not in persisted

    monkeypatch.setattr(
        video_metric_refresh,
        "run_video_metric_refresh_for_job",
        lambda _payload: {
            "status": "blocked",
            "evidence_id": 101,
            "snapshot_id": None,
            "error_code": "provider_video_mismatch",
            "provider_calls_performed": True,
        },
    )
    worker_handlers._process_kol_video_metric_refresh(
        QueueConn(),
        {"id": 100},
        {"evidence_id": 101, "kol_pool_id": 1},
    )
    assert captured["params"][0] == "blocked"


def test_migration_284_contract_is_narrow_and_reversible():
    root = Path(__file__).resolve().parents[1]
    up = (root / "migrations/284_vkpi_kol_video_product_links.sql").read_text(
        encoding="utf-8"
    )
    down = (
        root / "migrations/284_vkpi_kol_video_product_links_down.sql"
    ).read_text(encoding="utf-8")
    assert "REFERENCES vkpi_kol_video_evidence(id) ON DELETE CASCADE" in up
    assert "REFERENCES vkpi_products(sku) ON UPDATE CASCADE ON DELETE RESTRICT" in up
    assert "UNIQUE (evidence_id, product_sku, relation_type)" in up
    assert "relation_type IN ('manual', 'detected', 'confirmed')" in up
    assert "DROP TABLE IF EXISTS vkpi_kol_video_product_links" in down
    assert "284_vkpi_kol_video_product_links.sql" in down
    assert "mainline migration 283" in up
