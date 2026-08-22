"""board-ext recent_videos 闭环增量(U2 modality / U3 任务态+游标 / U9 reason_class)契约测试。

覆盖:
  1. SQL 静态审查:keyset 游标段 7 参 + published_at + modality 投影(只读 modality
     字符串,detail 不出库)+ compat 红线(零 percent / 零 LIKE / 零注释)。
  2. 同一 evidence 在 my-kol videos 与 board-ext recent_videos 两端点 TaskState /
     viltrox_modalities 字节一致(共用 attach_task_states;混合 conn:apify_jobs /
     analysis cache / 指标快照走 SQLite,board-ext 的 Postgres-only CTE 由按同一
     keyset 语义过滤的等价行替身提供)。
  3. 游标翻页无重无漏(含中途插入更新视频不扰动后续页),无游标首页行为不变。
  4. 路由:坏游标 400 fail-closed、游标只下推到 recent_videos、单组翻页端点同款 scope 闸。
  5. pg 车道(VKPI_PYTEST_ALLOW_LIVE_SERVICES=1 + DATABASE_URL 指隔离库,否则自动跳过):
     真 SQL 走一遍游标链(无重、序严格递减、modality 已归一),只读。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_my_kol as router_mod  # noqa: E402
from app.domains import content_metric_snapshots  # noqa: E402
from app.domains.kol import my_kol_board_ext as ext  # noqa: E402
from app.domains.kol import my_kol_video_recovery as recovery  # noqa: E402
from app.domains.kol import pool_detail  # noqa: E402
from app.domains.kol import video_evidence_projection as projection  # noqa: E402


# ── 1. SQL 静态审查 ─────────────────────────────────────────────────────


def test_recent_videos_sql_projects_keyset_modalities_and_keeps_compat_redlines():
    sql = ext.RECENT_VIDEOS_SQL
    assert sql.startswith(ext.V_CONTENT_CLASSIFIED_CTE)
    assert "COALESCE(e.publish_date, e.posted_at, e.created_at) AS published_at" in sql
    assert "vc.final_v1_viltrox_modalities AS llm_viltrox_modalities" in sql
    assert "e.metrics_scraped_at AS metrics_scraped_at" in sql
    assert "jsonb_path_query_array" in ext.V_CONTENT_CLASSIFIED_CTE
    assert "[*].modality" in ext.V_CONTENT_CLASSIFIED_CTE
    assert "__FINAL_V1_MODALITIES_EXPR__" not in ext.V_CONTENT_CLASSIFIED_CTE
    assert "detail" not in ext.V_CONTENT_CLASSIFIED_CTE
    assert "ORDER BY COALESCE(e.publish_date, e.posted_at, e.created_at) DESC NULLS LAST, e.id DESC" in sql
    # keyset 段:NOT ? 短路 + (p, id) 严格之后 + NULL 尾段
    assert sql.count("CAST(? AS TIMESTAMPTZ)") == 4
    assert "AND e.id < ?" in sql
    assert ext.RECENT_KEYSET_PARAM_COUNT == 7
    assert sql.count("?") == len(ext.VILTROX_TITLE_TOKENS) + 4 + ext.RECENT_KEYSET_PARAM_COUNT + 1
    assert "%" not in sql
    assert " LIKE " not in f" {sql.upper()} ".replace("\n", " ")
    assert "--" not in sql


def test_recent_keyset_params_shape():
    assert ext._recent_keyset_params(None) == (False, None, None, None, 0, None, 0)
    assert ext._recent_keyset_params(("2026-08-07T10:00:00Z", 3)) == (
        True, "2026-08-07T10:00:00Z", "2026-08-07T10:00:00Z", "2026-08-07T10:00:00Z", 3, "2026-08-07T10:00:00Z", 3,
    )
    assert ext._recent_keyset_params((None, 6)) == (True, None, None, None, 6, None, 6)
    assert ext._recent_keyset_params(("x", 0)) == (False, None, None, None, 0, None, 0)


# ── 混合 conn:SQLite 真表 + board-ext CTE 等价替身 ────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _final_v1_result(modalities: list[str]) -> str:
    evidence = [{"modality": m, "timestamp": "00:0%d" % i, "detail": "secret-%d" % i} for i, m in enumerate(modalities)]
    return json.dumps({
        "raw_gemini_video": {
            "viltrox_detected": bool(evidence),
            "viltrox_products_all": ["AF 85mm"] if evidence else [],
            "brand_product_evidence": {"viltrox_status": "present" if evidence else "unknown", "viltrox_evidence": evidence},
        }
    })


class _HybridConn:
    """SQLite 真表承载 apify_jobs / analysis cache / 指标快照;board-ext 的
    Postgres-only CTE 查询由等价行替身按同一 keyset 语义过滤后返回。"""

    def __init__(self, sqlite_conn: sqlite3.Connection):
        self._db = sqlite_conn
        self.calls: list[tuple[str, tuple]] = []
        self.row_factory = sqlite_conn.row_factory

    # _is_sqlite 探针依赖 executescript 可调用
    def executescript(self, script: str):
        return self._db.executescript(script)

    def commit(self):
        self._db.commit()

    def rollback(self):
        self._db.rollback()

    def execute(self, sql: str, params=()):
        self.calls.append((sql, tuple(params)))
        if sql == ext.RECENT_VIDEOS_SQL:
            return _Result(self._recent_rows(tuple(params)))
        return self._db.execute(sql, params)

    def _recent_rows(self, params: tuple) -> list[dict[str, Any]]:
        tokens = len(ext.VILTROX_TITLE_TOKENS)
        scope_sid = params[tokens]
        use_keyset, p1, _p2, _p3, kid, _p5, _kid2 = params[tokens + 4: tokens + 4 + ext.RECENT_KEYSET_PARAM_COUNT]
        limit = params[-1]
        rows = self._db.execute(
            """
            SELECT e.id AS evidence_id, e.kol_pool_id, e.project_id, e.content_url, e.platform,
                   COALESCE(e.title, '') AS title, COALESCE(e.video_title, '') AS video_title,
                   e.thumbnail_url, e.view_count, e.like_count, e.publish_date, e.posted_at, e.created_at,
                   e.metrics_scraped_at AS metrics_scraped_at,
                   COALESCE(e.publish_date, e.posted_at, e.created_at) AS published_at,
                   COALESCE(e.evidence_type, 'video') AS evidence_type,
                   'KOL ' || e.kol_pool_id AS kol_name, '@kol' || e.kol_pool_id AS kol_handle,
                   (fv.id IS NOT NULL) AS has_final_v1_cache,
                   lower(COALESCE(json_extract(fv.result, '$.raw_gemini_video.brand_product_evidence.viltrox_status'), '')) AS llm_viltrox_status,
                   lower(COALESCE(json_extract(fv.result, '$.raw_gemini_video.viltrox_detected'), '')) AS llm_viltrox_detected_text,
                   json_extract(fv.result, '$.raw_gemini_video.viltrox_products_all') AS llm_viltrox_products,
                   NULL AS llm_competitor_mentions,
                   json_extract(fv.result, '$.raw_gemini_video.brand_product_evidence.viltrox_evidence') AS llm_viltrox_modalities,
                   'undetermined' AS v_tier
            FROM vkpi_kol_video_evidence e
            LEFT JOIN (
                SELECT c.* FROM vkpi_analysis_cache c
                WHERE c.target_type='video' AND c.derive_method='video_analysis_final_v1' AND c.status='ready'
                  AND c.id = (SELECT MAX(c2.id) FROM vkpi_analysis_cache c2 WHERE c2.target_id=c.target_id
                              AND c2.target_type='video' AND c2.derive_method='video_analysis_final_v1' AND c2.status='ready')
            ) fv ON fv.target_id = CAST(e.id AS TEXT)
            WHERE COALESCE(e.is_active, 1) != 0 AND (? = 0 OR e.kol_pool_id IN (SELECT kol_pool_id FROM fav WHERE staff_id=?))
            """,
            (scope_sid, scope_sid),
        ).fetchall()
        items = [dict(r) for r in rows]
        if use_keyset:
            def after(row: dict[str, Any]) -> bool:
                published = row.get("published_at")
                if p1 is None:
                    return published is None and row["evidence_id"] < kid
                if published is None:
                    return True
                return published < p1 or (published == p1 and row["evidence_id"] < kid)
            items = [row for row in items if after(row)]
        # published_at DESC NULLS LAST, id DESC(与真 SQL ORDER BY 同语义)
        dated = sorted(
            (row for row in items if row.get("published_at") is not None),
            key=lambda row: (row["published_at"], row["evidence_id"]),
            reverse=True,
        )
        undated = sorted(
            (row for row in items if row.get("published_at") is None),
            key=lambda row: row["evidence_id"],
            reverse=True,
        )
        return (dated + undated)[: int(limit)]


def _sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_video_evidence (
            id INTEGER PRIMARY KEY, kol_pool_id INTEGER NOT NULL, project_id INTEGER,
            content_url TEXT NOT NULL DEFAULT 'https://www.youtube.com/watch?v=abcdefghijk',
            platform TEXT DEFAULT 'youtube', title TEXT, video_title TEXT, thumbnail_url TEXT,
            view_count INTEGER, like_count INTEGER, comment_count INTEGER, share_count INTEGER,
            duration_seconds INTEGER, publish_date TEXT, posted_at TEXT,
            evidence_type TEXT NOT NULL DEFAULT 'video', image_urls TEXT, source TEXT,
            is_active INTEGER NOT NULL DEFAULT 1, updated_at TEXT, created_at TEXT,
            metrics_scraped_at TEXT
        );
        CREATE TABLE vkpi_analysis_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
            derive_method TEXT NOT NULL, status TEXT NOT NULL, result TEXT, updated_at TEXT
        );
        CREATE TABLE apify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT,
            last_error TEXT, last_error_category TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE vkpi_kol_url_deep_crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kol_pool_id INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT
        );
        CREATE TABLE fav (kol_pool_id INTEGER, staff_id INTEGER);
        """
    )
    content_metric_snapshots.ensure_sqlite_schema(conn)
    return conn


def _seed(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, view_count, publish_date, created_at, updated_at) "
        "VALUES (?, 101, ?, ?, '2026-01-01T00:00:00Z', '2026-08-21T00:00:00Z')",
        [
            (1, 100, "2026-08-05T10:00:00Z"),
            (2, None, "2026-08-07T10:00:00Z"),
            (3, 300, "2026-08-07T10:00:00Z"),
            (4, 9000, "2026-08-01T10:00:00Z"),
            (5, 500, "2026-08-03T10:00:00Z"),
            (6, 600, None),
            (7, 700, "2026-08-09T10:00:00Z"),
        ],
    )
    # metrics_scraped_at 故意留空:pool_detail 的 SQLite 镜像查询投 NULL(真 Postgres 两端同取
    # e.metrics_scraped_at,隔离库实跑已验证两端 data.updated_at 回退值一致)。
    conn.execute("INSERT INTO fav VALUES (101, 7684)")
    conn.executemany(
        "INSERT INTO vkpi_analysis_cache (target_type, target_id, derive_method, status, result, updated_at) "
        "VALUES ('video', ?, 'video_analysis_final_v1', ?, ?, '2026-08-20T10:00:00Z')",
        [
            ("1", "ready", _final_v1_result(["visual", "audio"])),
            ("2", "ready", _final_v1_result(["subtitle"])),
            ("3", "ready", json.dumps({"raw_gemini_video": {"viltrox_detected": False}})),  # legacy result
            ("7", "stale", _final_v1_result(["visual"])),
        ],
    )
    final = "video_analysis_final_v1"
    jobs = [
        ("video", 1, "failed", "download", "RuntimeError: yt-dlp video download failed", final),
        ("video", 2, "running", None, "", final),
        ("video", 3, "blocked", "blocked", '{"reason": "budget_guard_blocked", "reason_detail": "budget_hard_stop"}', final),
        ("video", 4, "triage", "code_error", "NameError: name 'x' is not defined", final),
        ("kol_video_metric_refresh", 1, "queued", None, "", None),
        ("kol_video_metric_refresh", 5, "cancelled", None, "cancelled by operator", None),
        ("kol_profile_deep_crawl", 101, "done", None, "", None),
    ]
    for job_type, target_id, status, category, text, derive in jobs:
        payload = {"target_id": target_id, "kol_pool_id": 101}
        if derive:
            payload["derive_method"] = derive
        conn.execute(
            "INSERT INTO apify_jobs (job_type, payload, status, attempts, next_retry_at, last_error, last_error_category, "
            "created_at, updated_at) VALUES (?, ?, ?, 0, NULL, ?, ?, '2026-08-21T10:00:00Z', '2026-08-21T10:01:00Z')",
            (job_type, json.dumps(payload), status, text, category),
        )
    # 指标快照:evidence 1 有两次成功采样(metric_refresh data 非空态)
    conn.executemany(
        "INSERT INTO vkpi_content_metric_snapshots (evidence_id, capture_key, fetched_at, views, likes, comments, shares, status) "
        "VALUES (?, ?, ?, ?, 5, 1, 0, 'success')",
        [
            (1, "k1", "2026-08-20T09:00:00Z", 90),
            (1, "k2", "2026-08-21T09:00:00Z", 100),
        ],
    )
    conn.commit()


def _env(monkeypatch, conn: _HybridConn) -> None:
    monkeypatch.setattr(pool_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(pool_detail, "is_postgres_runtime", lambda: False)


def _strip_rank_noise(state: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(state, sort_keys=True))


# ── 2. 同一 evidence 两端点 TaskState / modality 一致 ──────────────────────


def test_same_evidence_yields_identical_task_state_and_modalities_on_both_endpoints(monkeypatch):
    db = _sqlite()
    _seed(db)
    conn = _HybridConn(db)
    _env(monkeypatch, conn)

    my_kol_rows = pool_detail._video_evidence_for_kol(101, limit=50, stable_order=True, before=None)
    my_kol = recovery.build_video_recovery_page(conn, kol_pool_id=101, videos=my_kol_rows, limit=50)
    board = ext.build_recent_videos_page(conn, staff_scope_id=7684, before=None)

    assert board["status"] == "ready" and board["method"] == ext.BOARD_METHOD
    by_my_kol = {row["evidence_id"]: row for row in my_kol["items"]}
    by_board = {row["evidence_id"]: row for row in board["items"]}
    assert set(by_my_kol) == set(by_board) == {1, 2, 3, 4, 5, 6, 7}
    for evidence_id in by_board:
        assert _strip_rank_noise(by_board[evidence_id]["tasks"]) == _strip_rank_noise(by_my_kol[evidence_id]["tasks"]), evidence_id
        assert by_board[evidence_id]["viltrox_modalities"] == by_my_kol[evidence_id]["viltrox_modalities"], evidence_id
        assert by_board[evidence_id]["published_at"] == by_my_kol[evidence_id]["published_at"], evidence_id

    # 三种 modality 组合 + 旧结果/stale 结果诚实空
    assert by_board[1]["viltrox_modalities"] == ["visual", "audio"]
    assert by_board[2]["viltrox_modalities"] == ["subtitle"]
    assert by_board[3]["viltrox_modalities"] == []
    assert by_board[7]["viltrox_modalities"] == []
    # 任务态 + reason_class 闭集(两端点同值已在上面逐条比过)
    final = {key: (by_board[key]["tasks"]["final_v1"]["status"], by_board[key]["tasks"]["final_v1"]["reason_class"]) for key in by_board}
    assert final == {
        1: ("failed", "provider_error"),
        2: ("running", None),
        3: ("blocked", "budget"),
        4: ("failed", "code_error"),
        5: ("not_requested", None),
        6: ("not_requested", None),
        7: ("not_requested", None),
    }
    metric_1 = by_board[1]["tasks"]["metric_refresh"]
    assert metric_1["status"] == "queued"
    assert metric_1["data"]["sample_count"] == 2 and metric_1["data"]["status"] in {"ready", "stale"}
    assert by_board[5]["tasks"]["metric_refresh"]["reason_class"] == "revoked"
    # 行契约:旧字段全在,新字段只增不改;原文/敏感不出
    for key in ("evidence_id", "kol_pool_id", "content_url", "platform", "title", "view_count", "publish_date",
                "has_final_v1_cache", "llm_viltrox_status", "llm_viltrox_detected", "v_tier", "best_thumbnail",
                "viltrox_modalities", "tasks", "published_at"):
        assert key in by_board[1], key
    assert set(by_board[1]["tasks"]) == {"metric_refresh", "final_v1"}
    blob = json.dumps(board, ensure_ascii=False)
    for raw in ("secret-", "yt-dlp", "budget_hard_stop", "NameError", "cancelled by operator", "contact_value"):
        assert raw not in blob, raw
    assert board["page"] == {
        "limit": ext.RECENT_VIDEOS_LIMIT, "returned": 7, "has_more": False, "next_cursor": None,
        "cursor_kind": recovery.CURSOR_KIND, "order": recovery.ORDER,
    }
    # 纯读(PRAGMA table_info = SQLite 本地镜像的结构探针,同属只读)
    for sql, _params in conn.calls:
        head = sql.strip().upper()
        assert head.startswith(("SELECT", "WITH", "PRAGMA TABLE_INFO")), sql[:60]
        for verb in ("INSERT ", "UPDATE ", "DELETE "):
            assert verb not in head


# ── 3. 游标翻页无重无漏 ────────────────────────────────────────────────


def test_recent_videos_keyset_walk_has_no_duplicates_and_no_gaps(monkeypatch):
    db = _sqlite()
    _seed(db)
    conn = _HybridConn(db)
    monkeypatch.setattr(ext, "RECENT_VIDEOS_LIMIT", 3)

    seen: list[int] = []
    before = None
    pages = 0
    for _ in range(10):
        page = ext.build_recent_videos_page(conn, staff_scope_id=7684, before=before)
        pages += 1
        assert page["status"] in {"ready", "empty"}
        assert page["page"]["limit"] == 3 and page["page"]["returned"] == len(page["items"]) <= 3
        seen.extend(row["evidence_id"] for row in page["items"])
        # 指标漂移不改变游标序
        db.execute("UPDATE vkpi_kol_video_evidence SET view_count = COALESCE(view_count, 0) * 7 + id")
        if pages == 1:
            # 中途有更新的视频进来:排在游标之前,绝不挤进后续页也不重复
            db.execute(
                "INSERT INTO vkpi_kol_video_evidence (id, kol_pool_id, view_count, publish_date, created_at) "
                "VALUES (8, 101, 1, '2026-08-30T10:00:00Z', '2026-08-30T10:00:00Z')"
            )
        if not page["page"]["has_more"]:
            assert page["page"]["next_cursor"] is None
            break
        assert page["page"]["next_cursor"]
        before = recovery.decode_cursor(page["page"]["next_cursor"])
    # 7 (08-09), 3/2 (08-07 tie -> id DESC), 1 (08-05), 5 (08-03), 4 (08-01), 6 (NULL -> 尾段)
    assert seen == [7, 3, 2, 1, 5, 4, 6]
    assert len(seen) == len(set(seen))
    assert pages == 3
    # 首页(无游标)与 build_board_ext 旧调用同 SQL 同参
    first_call = next(p for s, p in conn.calls if s == ext.RECENT_VIDEOS_SQL)
    assert first_call[len(ext.VILTROX_TITLE_TOKENS) + 4:] == (False, None, None, None, 0, None, 0, 4)


def test_board_ext_cursor_only_affects_recent_videos_and_keeps_legacy_first_page(monkeypatch):
    db = _sqlite()
    _seed(db)
    conn = _HybridConn(db)
    monkeypatch.setattr(ext, "RECENT_VIDEOS_LIMIT", 4)

    first = ext.build_board_ext(conn, staff_scope_id=7684, days=30)
    assert [row["evidence_id"] for row in first["recent_videos"]["items"]] == [7, 3, 2, 1]
    assert first["recent_videos"]["page"]["has_more"] is True
    token = first["recent_videos"]["page"]["next_cursor"]
    assert recovery.decode_cursor(token) == (first["recent_videos"]["items"][-1]["published_at"], 1)

    second = ext.build_board_ext(conn, staff_scope_id=7684, days=30, recent_videos_before=recovery.decode_cursor(token))
    assert [row["evidence_id"] for row in second["recent_videos"]["items"]] == [5, 4, 6]
    assert second["recent_videos"]["page"]["has_more"] is False
    assert second["recent_videos"]["page"]["next_cursor"] is None
    # 其余七组不受游标影响(同 conn 同 scope,逐组相同)
    for group in ("kpi_series", "funnel", "platform_dist", "fit_dist", "contact_coverage", "views_top", "v_content"):
        assert first[group] == second[group], group

    # 游标之后再无行:诚实 empty + 末页 reason
    tail = ext.build_recent_videos_page(conn, staff_scope_id=7684, before=(None, 6))
    assert tail["status"] == "empty" and tail["items"] == [] and "末页" in tail["reason"]


# ── 4. 路由 ──────────────────────────────────────────────────────────────


def _route_env(monkeypatch, captured: dict):
    monkeypatch.setattr(router_mod, "get_conn", lambda: object())
    monkeypatch.setattr(router_mod.scope, "can_view_all", lambda staff, **kw: bool(staff.get("can_view_all")))
    monkeypatch.setattr(
        router_mod.scope, "effective_staff_id",
        lambda staff, sid=None: sid if staff.get("can_view_all") else staff.get("sid"),
    )
    monkeypatch.setattr(router_mod.scope, "scope_context", lambda staff, sid=None: {"mode": "test"})

    def fake_board(conn, *, staff_scope_id, days, recent_videos_before=None):
        captured["board"] = (staff_scope_id, days, recent_videos_before)
        return {"status": "ready"}

    def fake_page(conn, *, staff_scope_id, before=None):
        captured["page"] = (staff_scope_id, before)
        return {"status": "ready", "items": []}

    monkeypatch.setattr(router_mod.my_kol_board_ext, "build_board_ext", fake_board)
    monkeypatch.setattr(router_mod.my_kol_board_ext, "build_recent_videos_page", fake_page)


def test_board_ext_routes_pass_keyset_cursor_and_fail_closed_on_bad_cursor(monkeypatch):
    captured: dict = {}
    _route_env(monkeypatch, captured)
    manager = {"can_view_all": True, "sid": 84}
    employee = {"can_view_all": False, "sid": 5}
    nobody = {"can_view_all": False, "sid": None}
    token = recovery.encode_cursor("2026-08-07T10:00:00Z", 3)

    # 旧调用:无游标 → before=None,行为不变
    router_mod.my_kol_board_ext_endpoint(days=30, staff_id=None, cursor=None, staff=manager)
    assert captured["board"] == (None, 30, None)
    # 游标下推;员工 own-only 压回本人
    router_mod.my_kol_board_ext_endpoint(days=7, staff_id=None, cursor=token, staff=employee)
    assert captured["board"] == (5, 7, ("2026-08-07T10:00:00Z", 3))
    # 坏游标 400,且在 scope/构建之前就拒
    captured.clear()
    with pytest.raises(HTTPException) as bad:
        router_mod.my_kol_board_ext_endpoint(days=30, staff_id=None, cursor="v2:40:7", staff=manager)
    assert bad.value.status_code == 400 and captured == {}

    # 单组翻页端点:同款 scope 闸 + 游标
    router_mod.my_kol_board_ext_recent_videos_endpoint(staff_id=9, cursor=token, staff=manager)
    assert captured["page"] == (9, ("2026-08-07T10:00:00Z", 3))
    with pytest.raises(HTTPException) as denied:
        router_mod.my_kol_board_ext_recent_videos_endpoint(staff_id=None, cursor=None, staff=nobody)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as bad_page:
        router_mod.my_kol_board_ext_recent_videos_endpoint(staff_id=None, cursor="garbage", staff=manager)
    assert bad_page.value.status_code == 400


# ── 5. pg 车道:隔离 Postgres 真 SQL 游标链 ─────────────────────────────


@pytest.mark.pg
def test_recent_videos_keyset_walk_on_postgres(monkeypatch):
    from app.db.connection import get_conn, is_postgres_runtime

    if not is_postgres_runtime():
        pytest.skip("Postgres runtime not active (set VKPI_PYTEST_ALLOW_LIVE_SERVICES=1 with DATABASE_URL)")
    monkeypatch.setattr(ext, "RECENT_VIDEOS_LIMIT", 25)
    conn = get_conn()
    seen: list[int] = []
    keys: list[tuple] = []
    before = None
    for _ in range(40):
        page = ext.build_recent_videos_page(conn, staff_scope_id=None, before=before)
        assert page["status"] in {"ready", "empty"}
        for row in page["items"]:
            seen.append(row["evidence_id"])
            keys.append((row["published_at"] is not None, row["published_at"] or "", row["evidence_id"]))
            assert row["viltrox_modalities"] == projection.viltrox_modalities(row["viltrox_modalities"])
            for task in row["tasks"].values():
                assert task["status"] in recovery.TASK_STATUSES
        if not page["page"]["has_more"]:
            break
        before = recovery.decode_cursor(page["page"]["next_cursor"])
    assert len(seen) == len(set(seen))
    assert keys == sorted(keys, reverse=True)
