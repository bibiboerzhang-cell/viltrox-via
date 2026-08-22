"""Contract tests for ``my_kol_video_recovery_v1`` (unified task state + keyset paging)."""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_my_kol as router_mod
from app.domains.kol import my_kol_paid_action_access, my_kol_video_recovery, pool, pool_detail


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY,
            kol_pool_id INTEGER NOT NULL,
            project_id INTEGER,
            content_url TEXT NOT NULL DEFAULT 'https://www.youtube.com/watch?v=x',
            platform TEXT DEFAULT 'youtube',
            title TEXT, video_title TEXT, thumbnail_url TEXT,
            view_count INTEGER, like_count INTEGER, comment_count INTEGER, share_count INTEGER,
            duration_seconds INTEGER,
            publish_date TEXT, posted_at TEXT,
            evidence_type TEXT NOT NULL DEFAULT 'video',
            image_urls TEXT, source TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT, created_at TEXT
        );
        CREATE TABLE vkpi_analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            derive_method TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
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
        CREATE TABLE vkpi_kol_url_deep_crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kol_pool_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT
        );
        """
    )
    # publish_date deliberately NOT monotonic with id and view_count drifts: the
    # keyset order must follow (published_at DESC, id DESC) only.
    conn.executemany(
        """
        INSERT INTO vkpi_kol_video_evidence
            (id, kol_pool_id, view_count, publish_date, created_at, updated_at)
        VALUES (?, 101, ?, ?, '2026-01-01T00:00:00Z', '2026-08-21T00:00:00Z')
        """,
        [
            (1, 100, "2026-08-05T10:00:00Z"),
            (2, None, "2026-08-07T10:00:00Z"),
            (3, 300, "2026-08-07T10:00:00Z"),  # ties with id 2 on published_at
            (4, 9_000, "2026-08-01T10:00:00Z"),
            (5, 500, "2026-08-03T10:00:00Z"),
            (6, 600, None),  # falls back to created_at, sorts last
            (7, 700, "2026-08-09T10:00:00Z"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO vkpi_analysis_cache
            (target_type, target_id, derive_method, status, updated_at)
        VALUES ('video', ?, 'video_analysis_final_v1', ?, ?)
        """,
        [
            ("1", "ready", "2026-08-20T10:00:00Z"),
            ("2", "ready", "2026-08-10T10:00:00Z"),
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
    created_at: str = "2026-08-21T10:00:00Z",
) -> int:
    payload = {"target_id": target_id, "kol_pool_id": 101}
    if derive_method:
        payload["derive_method"] = derive_method
    cursor = conn.execute(
        """
        INSERT INTO apify_jobs (
            job_type, payload, status, attempts, next_retry_at, last_error,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'provider_secret_prompt=do-not-leak', ?, '2026-08-21T10:01:00Z')
        """,
        (job_type, json.dumps(payload), status, attempts, next_retry_at, created_at),
    )
    return int(cursor.lastrowid)


def _video(evidence_id: int, *, freshness: str = "never", published_at: str | None = None) -> dict:
    return {
        "id": evidence_id,
        "evidence_id": evidence_id,
        "kol_pool_id": 101,
        "published_at": published_at,
        "freshness": freshness,
        "tracking_status": "tracked" if freshness == "fresh" else "insufficient_history",
        "last_attempt": {"fetched_at": "2026-08-21T09:00:00Z"},
        "last_success": {"fetched_at": "2026-08-21T09:00:00Z"} if freshness == "fresh" else None,
        "sample_count": 2 if freshness == "fresh" else 0,
        "attempt_count": 3,
    }


def _evidence_env(monkeypatch, conn: sqlite3.Connection) -> None:
    monkeypatch.setattr(pool_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: False)


# ── unified TaskState across the three job classes ───────────────────────


def test_page_unifies_profile_metric_and_final_v1_task_states_and_keeps_freshness_separate() -> None:
    conn = _conn()
    try:
        final = "video_analysis_final_v1"
        _job(conn, job_type="kol_profile_deep_crawl", target_id=101, status="done")
        profile_job = _job(
            conn, job_type="kol_profile_deep_crawl", target_id=101, status="queued", attempts=2,
            next_retry_at="2026-08-21T10:05:00Z",
        )
        _job(conn, job_type="kol_profile_deep_crawl", target_id=999, status="running")
        _job(conn, job_type="video", target_id=1, status="failed", derive_method=final)
        _job(conn, job_type="video", target_id=2, status="running", derive_method=final)  # newer than cache
        _job(conn, job_type="video", target_id=3, status="blocked", derive_method=final)
        _job(conn, job_type="video", target_id=4, status="triage", derive_method=final)
        _job(conn, job_type="video", target_id=5, status="done", derive_method=final)  # done, no cache
        _job(conn, job_type="kol_video_metric_refresh", target_id=1, status="queued")
        _job(conn, job_type="kol_video_metric_refresh", target_id=2, status="done")
        conn.commit()

        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        page = my_kol_video_recovery.build_video_recovery_page(
            conn,
            kol_pool_id=101,
            videos=[_video(index, freshness="fresh" if index == 1 else "stale") for index in range(1, 8)],
            limit=20,
        )
        conn.set_trace_callback(None)

        assert page["contract"] == "my_kol_video_recovery_v1"
        assert page["read_only"] is True
        profile = page["profile_crawl"]
        assert (profile["status"], profile["job_id"]) == ("retrying", profile_job)
        assert profile["requested_at"] == "2026-08-21T10:00:00Z"
        assert profile["data"] == {
            "status": "none", "freshness": "never", "updated_at": None, "superseded_by_job": True,
        }

        rows = {row["evidence_id"]: row for row in page["items"]}
        final_states = {key: rows[key]["tasks"]["final_v1"]["status"] for key in rows}
        assert final_states == {
            1: "failed",        # failed job; cache still shown as data
            2: "running",       # newer re-analysis must not be hidden by the ready cache
            3: "blocked",
            4: "failed",        # triage collapses to failed
            5: "failed",        # "done" without a ready cache is a broken promise
            6: "not_requested",
            7: "not_requested", # stale cache, no job
        }
        assert rows[1]["tasks"]["final_v1"]["data"] == {
            "status": "ready", "freshness": "fresh", "updated_at": "2026-08-20T10:00:00Z", "superseded_by_job": False,
        }
        assert rows[2]["tasks"]["final_v1"]["data"]["status"] == "ready"
        assert rows[2]["tasks"]["final_v1"]["data"]["superseded_by_job"] is True
        assert rows[7]["tasks"]["final_v1"]["data"]["status"] == "stale"
        assert rows[6]["tasks"]["final_v1"]["data"] == {
            "status": "none", "freshness": "never", "updated_at": None, "superseded_by_job": False,
        }

        metric_1 = rows[1]["tasks"]["metric_refresh"]
        assert metric_1["status"] == "queued"
        assert metric_1["data"]["status"] == "ready" and metric_1["data"]["freshness"] == "fresh"
        assert metric_1["data"]["superseded_by_job"] is True  # job requested after the snapshot
        assert metric_1["data"]["tracking_status"] == "tracked"
        assert metric_1["data"]["sample_count"] == 2
        metric_2 = rows[2]["tasks"]["metric_refresh"]
        assert metric_2["status"] == "ready" and metric_2["data"]["status"] == "stale"
        assert rows[6]["tasks"]["metric_refresh"]["status"] == "not_requested"

        for row in page["items"]:
            assert row["viltrox_modalities"] == []          # fixture caches carry no evidence block
            for task in row["tasks"].values():
                assert task["status"] in my_kol_video_recovery.TASK_STATUSES
                assert set(task) == {"status", "job_id", "requested_at", "updated_at", "data"}

        assert page["summary"] == {"total": 7, "views_total": 11_200, "views_measured": 6, "final_v1_ready": 2}
        assert page["page"] == {
            "limit": 20, "returned": 7, "has_more": False, "next_cursor": None,
            "cursor_kind": "published_at_id", "order": "published_at_desc_id_desc",
        }
        assert "provider_secret" not in json.dumps(page, ensure_ascii=False)
        assert all(statement.lstrip().upper().startswith(("SELECT", "WITH")) for statement in statements)
    finally:
        conn.close()


def test_ready_cache_yields_only_to_a_newer_active_reanalysis() -> None:
    cache = {"status": "ready", "updated_at": "2026-08-21T10:00:00Z"}
    newer = {"job_id": 11, "status": "running", "requested_at": "2026-08-21T11:00:00Z", "updated_at": None}
    older = {"job_id": 10, "status": "queued", "requested_at": "2026-08-21T09:00:00Z", "updated_at": None}
    finished = {"job_id": 12, "status": "ready", "requested_at": "2026-08-21T09:30:00Z", "updated_at": None}

    refreshing = my_kol_video_recovery.final_v1_task_state(cache, newer)
    assert refreshing["status"] == "running" and refreshing["job_id"] == 11
    assert refreshing["data"]["status"] == "ready" and refreshing["data"]["superseded_by_job"] is True

    stale_request = my_kol_video_recovery.final_v1_task_state(cache, older)
    assert stale_request["status"] == "queued" and stale_request["data"]["superseded_by_job"] is False

    settled = my_kol_video_recovery.final_v1_task_state(cache, finished)
    assert settled["status"] == "ready" and settled["data"]["superseded_by_job"] is False

    legacy = my_kol_video_recovery.final_v1_task_state(cache, None)
    assert legacy["status"] == "ready" and legacy["job_id"] is None

    broken = my_kol_video_recovery.final_v1_task_state(None, finished)
    assert broken["status"] == "failed" and broken["data"]["status"] == "none"


def test_profile_crawl_state_uses_ready_runs_for_freshness() -> None:
    conn = _conn()
    try:
        assert my_kol_video_recovery.profile_crawl_task_state(conn, 101) == {
            "status": "not_requested", "job_id": None, "requested_at": None, "updated_at": None,
            "data": {"status": "none", "freshness": "never", "updated_at": None, "superseded_by_job": False},
        }
        fresh_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn.execute(
            "INSERT INTO vkpi_kol_url_deep_crawl_runs (kol_pool_id, status, created_at) VALUES (101, 'ready', ?)",
            (fresh_at,),
        )
        job_id = _job(conn, job_type="kol_profile_deep_crawl", target_id=101, status="done")
        state = my_kol_video_recovery.profile_crawl_task_state(conn, 101)
        assert state["status"] == "ready" and state["job_id"] == job_id
        assert state["data"]["status"] == "ready" and state["data"]["freshness"] == "fresh"

        conn.execute("UPDATE vkpi_kol_url_deep_crawl_runs SET created_at='2026-01-01T00:00:00Z'")
        running = _job(
            conn, job_type="kol_profile_deep_crawl", target_id=101, status="running",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        state = my_kol_video_recovery.profile_crawl_task_state(conn, 101)
        assert state["status"] == "running" and state["job_id"] == running
        assert state["data"]["status"] == "stale" and state["data"]["superseded_by_job"] is True
    finally:
        conn.close()


# ── keyset cursor ─────────────────────────────────────────────────────────


def test_cursor_round_trips_published_at_and_id() -> None:
    token = my_kol_video_recovery.encode_cursor("2026-08-07T10:00:00Z", 3)
    assert my_kol_video_recovery.decode_cursor(token) == ("2026-08-07T10:00:00Z", 3)
    null_token = my_kol_video_recovery.encode_cursor(None, 6)
    assert my_kol_video_recovery.decode_cursor(null_token) == (None, 6)
    assert my_kol_video_recovery.decode_cursor(None) is None
    assert my_kol_video_recovery.decode_cursor("") is None


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "MQ",
        base64.urlsafe_b64encode(b"v2:0:7").decode("ascii").rstrip("="),  # offset-era cursor
        base64.urlsafe_b64encode(json.dumps({"k": "offset", "p": None, "i": 3}).encode()).decode().rstrip("="),
        base64.urlsafe_b64encode(json.dumps({"k": "published_at_id", "p": None, "i": 0}).encode()).decode().rstrip("="),
        base64.urlsafe_b64encode(json.dumps({"k": "published_at_id", "p": None, "i": True}).encode()).decode().rstrip("="),
        base64.urlsafe_b64encode(json.dumps({"k": "published_at_id", "p": 5, "i": 3}).encode()).decode().rstrip("="),
        my_kol_video_recovery.encode_cursor("2026-08-07T10:00:00Z", 3) + "=",  # non-canonical
    ],
)
def test_invalid_cursor_fails_closed(value: str) -> None:
    with pytest.raises(ValueError, match="invalid videos cursor"):
        my_kol_video_recovery.decode_cursor(value)


def test_keyset_pages_follow_published_at_then_id_and_survive_metric_drift(monkeypatch) -> None:
    conn = _conn()
    try:
        _evidence_env(monkeypatch, conn)
        seen: list[int] = []
        before = None
        for _ in range(10):
            rows = pool_detail._video_evidence_for_kol(101, limit=3, stable_order=True, before=before)
            page = my_kol_video_recovery.build_video_recovery_page(conn, kol_pool_id=101, videos=rows, limit=2)
            seen.extend(row["evidence_id"] for row in page["items"])
            # view counts drift between pages; ordering must not.
            conn.execute("UPDATE vkpi_kol_video_evidence SET view_count = COALESCE(view_count, 0) * 7 + id")
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            before = my_kol_video_recovery.decode_cursor(page["next_cursor"])
        # 7 (08-09), 3 then 2 (08-07 tie -> id DESC), 1 (08-05), 5 (08-03), 4 (08-01), 6 (NULL publish -> created_at)
        assert seen == [7, 3, 2, 1, 5, 4, 6]
        assert len(seen) == len(set(seen))
    finally:
        conn.close()


def test_keyset_page_is_stable_when_new_evidence_is_inserted_mid_walk(monkeypatch) -> None:
    conn = _conn()
    try:
        _evidence_env(monkeypatch, conn)
        first_rows = pool_detail._video_evidence_for_kol(101, limit=4, stable_order=True, before=None)
        first = my_kol_video_recovery.build_video_recovery_page(conn, kol_pool_id=101, videos=first_rows, limit=3)
        assert [row["evidence_id"] for row in first["items"]] == [7, 3, 2]
        assert first["has_more"] is True
        # A newly crawled video with a *newer* publish date sorts before the
        # cursor and must not shift or duplicate the remaining pages.
        conn.execute(
            "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, view_count, publish_date, created_at) "
            "VALUES (8, 101, 1, '2026-08-30T10:00:00Z', '2026-08-30T10:00:00Z')"
        )
        before = my_kol_video_recovery.decode_cursor(first["next_cursor"])
        second_rows = pool_detail._video_evidence_for_kol(101, limit=4, stable_order=True, before=before)
        second = my_kol_video_recovery.build_video_recovery_page(conn, kol_pool_id=101, videos=second_rows, limit=3)
        assert [row["evidence_id"] for row in second["items"]] == [1, 5, 4]
        assert second["total"] == 8  # summary is live truth, page walk stays stable
        before = my_kol_video_recovery.decode_cursor(second["next_cursor"])
        third_rows = pool_detail._video_evidence_for_kol(101, limit=4, stable_order=True, before=before)
        third = my_kol_video_recovery.build_video_recovery_page(conn, kol_pool_id=101, videos=third_rows, limit=3)
        assert [row["evidence_id"] for row in third["items"]] == [6]
        assert third["has_more"] is False
    finally:
        conn.close()


def test_default_order_is_unchanged_for_legacy_callers(monkeypatch) -> None:
    conn = _conn()
    try:
        _evidence_env(monkeypatch, conn)
        rows = pool_detail._video_evidence_for_kol(101, limit=200)
        # legacy: publish/posted/updated/created DESC, then view_count DESC, id DESC —
        # row 6 (NULL publish_date) floats to the top on updated_at, which is
        # exactly the drift the keyset order refuses to depend on.
        assert [row["evidence_id"] for row in rows][:3] == [6, 7, 3]
        assert all("published_at" in row for row in rows)
    finally:
        conn.close()


# ── route ────────────────────────────────────────────────────────────────


def test_route_checks_target_read_scope_before_loading_evidence(monkeypatch) -> None:
    calls = {"load": 0}
    conn = object()

    def denied(*_args, **_kwargs):
        raise my_kol_paid_action_access.MyKolPaidActionError("my_kol_paid_action_read_forbidden", 403)

    def load_bomb(*_args, **_kwargs):
        calls["load"] += 1
        raise AssertionError("arbitrary pool id must not load evidence")

    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)
    monkeypatch.setattr(my_kol_paid_action_access, "assert_target_readable", denied)
    monkeypatch.setattr(pool, "_video_evidence_for_kol", load_bomb)

    with pytest.raises(HTTPException) as caught:
        router_mod.my_kol_videos_recovery_endpoint(
            kol_pool_id=999, limit=60, cursor=None, staff={"id": 20, "user_id": 120, "role": "member"},
        )
    assert caught.value.status_code == 403
    assert caught.value.detail == "my_kol_paid_action_read_forbidden"
    assert calls == {"load": 0}


def test_route_rejects_offset_cursor_and_passes_keyset_to_loader(monkeypatch) -> None:
    conn = object()
    calls: dict[str, object] = {}

    def allowed(db, *, kol_pool_id, staff):
        calls["scope"] = (db, kol_pool_id, staff["id"])
        return staff["id"]

    def load(kol_pool_id, *, limit, stable_order, before):
        calls["load"] = (kol_pool_id, limit, stable_order, before)
        return [{"evidence_id": 8, "kol_pool_id": kol_pool_id, "published_at": "2026-08-01T00:00:00Z"}]

    def build(db, **kwargs):
        calls["build"] = (db, kwargs)
        return {"kol_pool_id": kwargs["kol_pool_id"], "items": kwargs["videos"]}

    monkeypatch.setattr(router_mod, "get_conn", lambda: conn)
    monkeypatch.setattr(my_kol_paid_action_access, "assert_target_readable", allowed)
    monkeypatch.setattr(pool, "_video_evidence_for_kol", load)
    monkeypatch.setattr(my_kol_video_recovery, "build_video_recovery_page", build)

    with pytest.raises(HTTPException) as bad:
        router_mod.my_kol_videos_recovery_endpoint(
            kol_pool_id=88, limit=60, cursor=base64.urlsafe_b64encode(b"v2:40:7").decode().rstrip("="),
            staff={"id": 20, "user_id": 120, "role": "member"},
        )
    assert bad.value.status_code == 400
    assert "load" not in calls

    token = my_kol_video_recovery.encode_cursor("2026-08-07T10:00:00Z", 3)
    result = router_mod.my_kol_videos_recovery_endpoint(
        kol_pool_id=88, limit=50, cursor=token, staff={"id": 20, "user_id": 120, "role": "member"},
    )
    assert calls["scope"] == (conn, 88, 20)
    assert calls["load"] == (88, 51, True, ("2026-08-07T10:00:00Z", 3))
    assert calls["build"][1]["limit"] == 50
    assert result["items"][0]["evidence_id"] == 8


# ── U2: viltrox_modalities projection (final_v1 brand_product_evidence) ───


def _final_v1_result(modalities: list[str], *, layer1_only: bool = False) -> str:
    evidence = [
        {"modality": modality, "timestamp": "00:1%d" % index, "detail": "secret-detail-%d" % index, "confidence": 0.9}
        for index, modality in enumerate(modalities)
    ]
    block = {"viltrox_status": "present" if evidence else "unknown", "viltrox_evidence": evidence}
    raw: dict = {"viltrox_detected": bool(evidence), "viltrox_products_all": []}
    if layer1_only:
        raw["video_analysis_final_v1"] = {"layer1_visual_content": {"brand_product_evidence": block}}
    else:
        raw["brand_product_evidence"] = block
    return json.dumps({"raw_gemini_video": raw})


def _cache(conn: sqlite3.Connection, target_id: int, result: str | None, *, status: str = "ready") -> None:
    conn.execute(
        "INSERT INTO vkpi_analysis_cache (target_type, target_id, derive_method, status, result, updated_at) "
        "VALUES ('video', ?, 'video_analysis_final_v1', ?, ?, '2026-08-20T10:00:00Z')",
        (str(target_id), status, result),
    )


def test_viltrox_modalities_projects_three_combinations_without_leaking_detail() -> None:
    conn = _conn()
    try:
        conn.execute("DELETE FROM vkpi_analysis_cache")
        _cache(conn, 1, _final_v1_result(["visual"]))                                   # visual only
        _cache(conn, 2, _final_v1_result(["audio", "subtitle", "audio"]))               # two kinds, dup + unordered
        _cache(conn, 3, _final_v1_result(["metadata", "audio", "visual", "subtitle"]))  # all three, metadata dropped
        _cache(conn, 4, _final_v1_result(["subtitle"], layer1_only=True))              # layer1 copy only
        _cache(conn, 5, json.dumps({"raw_gemini_video": {"viltrox_detected": True}}))  # legacy result, no block
        _cache(conn, 6, "{not json")                                                    # malformed
        _cache(conn, 7, _final_v1_result(["visual"]), status="stale")                  # not ready -> ignored
        # a newer ready cache row wins over an older one for the same evidence
        _cache(conn, 1, _final_v1_result(["audio"]))
        conn.commit()

        page = my_kol_video_recovery.build_video_recovery_page(
            conn, kol_pool_id=101, videos=[_video(index) for index in range(1, 8)], limit=20,
        )
        rows = {row["evidence_id"]: row["viltrox_modalities"] for row in page["items"]}
        assert rows == {
            1: ["audio"],
            2: ["subtitle", "audio"],
            3: ["visual", "subtitle", "audio"],
            4: ["subtitle"],
            5: [],
            6: [],
            7: [],
        }
        assert "secret-detail" not in json.dumps(page, ensure_ascii=False)
    finally:
        conn.close()


def test_viltrox_modalities_normaliser_is_order_stable_and_fail_closed() -> None:
    from app.domains.kol import video_evidence_projection as projection

    assert projection.viltrox_modalities(None) == []
    assert projection.viltrox_modalities("") == []
    assert projection.viltrox_modalities("not-json") == []
    assert projection.viltrox_modalities({"modality": "visual"}) == []
    assert projection.viltrox_modalities(["AUDIO", " visual ", "metadata", "audio"]) == ["visual", "audio"]
    assert projection.viltrox_modalities('[{"modality": "subtitle"}, {"modality": "visual"}]') == ["visual", "subtitle"]
    assert projection.merge_modalities('["audio"]', [{"modality": "visual"}], None) == ["visual", "audio"]
    # the Postgres projection only ever reads modality strings (detail never leaves the DB)
    assert "[*].modality" in projection.FINAL_V1_MODALITIES_PG_EXPR
    assert "detail" not in projection.FINAL_V1_MODALITIES_PG_EXPR
    assert "%" not in projection.FINAL_V1_MODALITIES_PG_EXPR


def test_viltrox_modalities_degrade_to_empty_when_cache_schema_is_narrow() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "CREATE TABLE vkpi_analysis_cache (id INTEGER PRIMARY KEY, target_type TEXT, target_id TEXT, "
            "derive_method TEXT, status TEXT, updated_at TEXT);"
        )
        from app.domains.kol import video_evidence_projection as projection

        assert projection.final_v1_modalities_for_evidence(conn, [1, 2]) == {}
        assert projection.final_v1_modalities_for_evidence(conn, []) == {}
    finally:
        conn.close()
