from __future__ import annotations

import base64
import json
import sqlite3

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_my_kol as router_mod
from app.domains.kol import my_kol_paid_action_access, my_kol_video_recovery, pool


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            evidence_type TEXT NOT NULL DEFAULT 'video',
            is_active INTEGER NOT NULL DEFAULT 1,
            view_count INTEGER
        );
        CREATE TABLE vkpi_analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            derive_method TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            last_error TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.executemany(
        """
        INSERT INTO vkpi_kol_video_evidence
            (id, kol_pool_id, evidence_type, is_active, view_count)
        VALUES (?, 101, 'video', 1, ?)
        """,
        [(1, 100), (2, None), (3, 300), (4, 400), (5, 500), (6, 600), (7, 700)],
    )
    conn.executemany(
        """
        INSERT INTO vkpi_analysis_cache
            (target_type, target_id, derive_method, status, updated_at)
        VALUES ('video', ?, 'video_analysis_final_v1', ?, ?)
        """,
        [
            ("1", "ready", "2026-08-20T10:00:00Z"),
            ("2", "stale", "2026-08-10T10:00:00Z"),
            ("7", "stale", "2026-08-09T10:00:00Z"),
        ],
    )
    return conn


def _job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    target_id: int,
    status: str,
    attempts: int = 0,
    next_retry_at: str | None = None,
    derive_method: str | None = None,
) -> int:
    payload = {"target_id": target_id, "kol_pool_id": 101}
    if derive_method:
        payload["derive_method"] = derive_method
    cursor = conn.execute(
        """
        INSERT INTO apify_jobs (
            job_type, payload, status, attempts, next_retry_at, last_error,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'provider_secret_prompt=do-not-leak',
                  '2026-08-21T10:00:00Z', '2026-08-21T10:01:00Z')
        """,
        (job_type, json.dumps(payload), status, attempts, next_retry_at),
    )
    return int(cursor.lastrowid)


def _video(evidence_id: int, *, freshness: str = "never") -> dict:
    return {
        "id": evidence_id,
        "evidence_id": evidence_id,
        "kol_pool_id": 101,
        "freshness": freshness,
        "tracking_status": "tracked" if freshness == "fresh" else "insufficient_history",
        "last_attempt": {"fetched_at": "2026-08-21T09:00:00Z"},
        "last_success": {"fetched_at": "2026-08-21T09:00:00Z"} if freshness == "fresh" else None,
        "sample_count": 2 if freshness == "fresh" else 0,
        "attempt_count": 3,
    }


def test_recovery_page_projects_exact_latest_jobs_and_separate_freshness() -> None:
    conn = _conn()
    try:
        _job(conn, job_type="kol_profile_deep_crawl", target_id=101, status="done")
        profile_job = _job(
            conn,
            job_type="kol_profile_deep_crawl",
            target_id=101,
            status="queued",
            attempts=2,
            next_retry_at="2026-08-21T10:05:00Z",
        )
        _job(conn, job_type="kol_profile_deep_crawl", target_id=999, status="running")

        final_method = "video_analysis_final_v1"
        _job(conn, job_type="video", target_id=1, status="failed", derive_method=final_method)
        _job(
            conn,
            job_type="video",
            target_id=2,
            status="queued",
            attempts=1,
            next_retry_at="2026-08-21T10:06:00Z",
            derive_method=final_method,
        )
        _job(conn, job_type="video", target_id=3, status="blocked", derive_method=final_method)
        _job(conn, job_type="video", target_id=4, status="failed", derive_method=final_method)
        _job(conn, job_type="video", target_id=5, status="done", derive_method=final_method)
        _job(conn, job_type="kol_video_metric_refresh", target_id=1, status="queued")
        _job(conn, job_type="kol_video_metric_refresh", target_id=2, status="done")
        conn.commit()

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        page = my_kol_video_recovery.build_video_recovery_page(
            conn,
            kol_pool_id=101,
            videos=[_video(index, freshness="fresh" if index == 1 else "stale") for index in range(1, 8)],
            offset=0,
            limit=20,
            snapshot_boundary_id=7,
        )
        conn.set_trace_callback(None)

        assert page["profile_crawl"] == {
            "job_id": profile_job,
            "status": "retrying",
            "created_at": "2026-08-21T10:00:00Z",
            "updated_at": "2026-08-21T10:01:00Z",
        }
        rows = {row["evidence_id"]: row for row in page["items"]}
        assert {key: rows[key]["final_v1"]["state"] for key in rows} == {
            1: "ready",
            2: "active",
            3: "blocked",
            4: "failed",
            5: "failed",
            6: "not_requested",
            7: "stale",
        }
        assert rows[1]["metric_refresh"]["latest_job"]["status"] == "queued"
        assert rows[2]["metric_refresh"]["latest_job"]["status"] == "done"
        assert rows[1]["metric_refresh"]["snapshot"] == {
            "status": "tracked",
            "freshness": "fresh",
            "last_attempt_at": "2026-08-21T09:00:00Z",
            "last_success_at": "2026-08-21T09:00:00Z",
            "sample_count": 2,
            "attempt_count": 3,
        }
        assert page["summary"] == {
            "total": 7,
            "views_total": 2600,
            "views_measured": 6,
            "final_v1_ready": 1,
        }
        assert page["returned"] == 7
        assert page["has_more"] is False
        assert "provider_secret" not in json.dumps(page, ensure_ascii=False)
        assert all(statement.lstrip().upper().startswith(("SELECT", "WITH")) for statement in statements)
    finally:
        conn.close()


def test_recovery_page_has_truthful_cursor_and_only_projects_loaded_evidence() -> None:
    conn = _conn()
    try:
        _job(conn, job_type="video", target_id=7, status="running", derive_method="video_analysis_final_v1")
        first = my_kol_video_recovery.build_video_recovery_page(
            conn,
            kol_pool_id=101,
            videos=[_video(1), _video(2)],
            offset=0,
            limit=2,
            snapshot_boundary_id=7,
        )
        assert first["total"] == 7
        assert first["returned"] == 2
        assert first["has_more"] is True
        assert my_kol_video_recovery.decode_cursor(first["next_cursor"]) == (2, 7)
        assert all(row["evidence_id"] != 7 for row in first["items"])

        second = my_kol_video_recovery.build_video_recovery_page(
            conn,
            kol_pool_id=101,
            videos=[_video(3), _video(4)],
            offset=my_kol_video_recovery.decode_cursor(first["next_cursor"])[0],
            limit=2,
            snapshot_boundary_id=7,
        )
        assert second["returned"] == 2
        assert my_kol_video_recovery.decode_cursor(second["next_cursor"]) == (4, 7)
    finally:
        conn.close()


def test_ready_cache_yields_to_only_a_newer_active_reanalysis() -> None:
    cache = {"status": "ready", "updated_at": "2026-08-21T10:00:00Z"}
    newer = {
        "job_id": 11,
        "status": "running",
        "created_at": "2026-08-21T11:00:00Z",
        "updated_at": "2026-08-21T11:01:00Z",
    }
    older = {
        "job_id": 10,
        "status": "queued",
        "created_at": "2026-08-21T09:00:00Z",
        "updated_at": "2026-08-21T09:01:00Z",
    }
    refreshing = my_kol_video_recovery._final_v1_projection(cache, newer)
    settled = my_kol_video_recovery._final_v1_projection(cache, older)
    assert refreshing["state"] == "active"
    assert refreshing["cache"] == cache
    assert settled["state"] == "ready"


def test_cursor_boundary_freezes_total_against_new_evidence() -> None:
    conn = _conn()
    try:
        boundary = my_kol_video_recovery.resolve_snapshot_boundary(conn, 101)
        assert boundary == 7
        conn.execute(
            "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, evidence_type, is_active, view_count) VALUES (8, 101, 'video', 1, 800)"
        )
        page = my_kol_video_recovery.build_video_recovery_page(
            conn,
            kol_pool_id=101,
            videos=[_video(1), _video(2)],
            offset=0,
            limit=2,
            snapshot_boundary_id=boundary,
        )
        assert page["total"] == 7
        assert page["snapshot_boundary_id"] == 7
        assert page["cursor_stable"] is True
        assert my_kol_video_recovery.decode_cursor(page["next_cursor"]) == (2, 7)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "MQ",
        # wrong version prefix
        base64.urlsafe_b64encode(b"v1:0:7").decode("ascii").rstrip("="),
        # boundary 0 never freezes a snapshot
        base64.urlsafe_b64encode(b"v2:0:0").decode("ascii").rstrip("="),
        # non-canonical encoding (kept padding) of an otherwise valid pair
        my_kol_video_recovery.encode_cursor(2, 7) + "=",
    ],
)
def test_invalid_cursor_fails_closed(value: str) -> None:
    with pytest.raises(ValueError, match="invalid videos cursor"):
        my_kol_video_recovery.decode_cursor(value)


def test_route_checks_target_read_scope_before_loading_evidence(monkeypatch) -> None:
    calls = {"load": 0, "build": 0}
    conn = object()

    def denied(*_args, **_kwargs):
        raise my_kol_paid_action_access.MyKolPaidActionError(
            "my_kol_paid_action_read_forbidden",
            403,
        )

    def load_bomb(*_args, **_kwargs):
        calls["load"] += 1
        raise AssertionError("arbitrary pool id must not load evidence")

    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)
    monkeypatch.setattr(my_kol_paid_action_access, "assert_target_readable", denied)
    monkeypatch.setattr(pool, "_video_evidence_for_kol", load_bomb)

    with pytest.raises(HTTPException) as caught:
        router_mod.my_kol_videos_recovery_endpoint(
            kol_pool_id=999,
            limit=60,
            cursor=None,
            staff={"id": 20, "user_id": 120, "role": "member"},
        )
    assert caught.value.status_code == 403
    assert caught.value.detail == "my_kol_paid_action_read_forbidden"
    assert calls == {"load": 0, "build": 0}


def test_route_decodes_cursor_and_passes_only_bounded_page(monkeypatch) -> None:
    conn = object()
    calls: dict[str, object] = {}

    def allowed(db, *, kol_pool_id, staff):
        calls["scope"] = (db, kol_pool_id, staff["id"])
        return staff["id"]

    def load(kol_pool_id, *, limit, offset, max_evidence_id):
        calls["load"] = (kol_pool_id, limit, offset, max_evidence_id)
        return [{"evidence_id": 8, "kol_pool_id": kol_pool_id}]

    def build(db, **kwargs):
        calls["build"] = (db, kwargs)
        return {"kol_pool_id": kwargs["kol_pool_id"], "items": kwargs["videos"]}

    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)
    monkeypatch.setattr(my_kol_paid_action_access, "assert_target_readable", allowed)
    monkeypatch.setattr(pool, "_video_evidence_for_kol", load)
    monkeypatch.setattr(my_kol_video_recovery, "build_video_recovery_page", build)
    monkeypatch.setattr(my_kol_video_recovery, "resolve_snapshot_boundary", lambda *_args: 120)

    result = router_mod.my_kol_videos_recovery_endpoint(
        kol_pool_id=101,
        limit=40,
        cursor=my_kol_video_recovery.encode_cursor(80, 120),
        staff={"id": 10, "user_id": 110, "role": "member"},
    )
    assert result["items"] == [{"evidence_id": 8, "kol_pool_id": 101}]
    assert calls["scope"] == (conn, 101, 10)
    assert calls["load"] == (101, 40, 80, 120)
    assert calls["build"][1]["offset"] == 80
    assert calls["build"][1]["snapshot_boundary_id"] == 120
