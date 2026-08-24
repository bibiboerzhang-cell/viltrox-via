"""波 D·B 一键数据关注 + 按产品聚合播放总览(sqlite 假库,零 provider)。

覆盖:SKU 三级解析(manual/existing/auto)全走既有追踪路径;解析不出诚实
sku_required + 候选;总览分组 + d1/d7/d30 增量口径;未实测一律 null 不编 0;
收藏集 scope(员工只看本人,管理层全团队)。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains import content_metric_snapshots  # noqa: E402
from app.domains.kol import sku_play_overview, video_data_watch, video_metric_refresh, video_tracking  # noqa: E402


NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
STAFF_A = {"id": 10, "user_id": 110, "role": "member"}
STAFF_B = {"id": 40, "user_id": 140, "role": "member"}
MANAGER = {"id": 30, "user_id": 130, "role": "manager"}


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function("NOW", 0, lambda: _iso(NOW))
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
            display_name TEXT,
            handle TEXT,
            platform TEXT
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
            title TEXT,
            video_title TEXT,
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
            FOREIGN KEY(evidence_id) REFERENCES vkpi_kol_video_evidence(id)
        );
        """
    )
    content_metric_snapshots.ensure_sqlite_schema(conn)
    conn.executemany(
        "INSERT INTO users (id, status, email) VALUES (?, 'active', ?)",
        [(110, "a@example.test"), (140, "b@example.test"), (130, "m@example.test")],
    )
    conn.executemany(
        "INSERT INTO staff (id, user_id, role, permissions_json, active) VALUES (?, ?, ?, ?, 1)",
        [
            (10, 110, "member", '{"vkpi":"write"}'),
            (40, 140, "member", '{"vkpi":"write"}'),
            (30, 130, "manager", "{}"),
        ],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool (id, display_name, handle, platform) VALUES (?, ?, ?, 'youtube')",
        [(1, "Alice", "alice"), (2, "Bob", "bob"), (3, "Carol", "carol")],
    )
    conn.executemany(
        "INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (?, ?)",
        [(1, 10), (2, 10), (3, 40)],
    )
    conn.executemany(
        "INSERT INTO vkpi_products (sku, model_name, marketing_name) VALUES (?, ?, ?)",
        [
            ("SKU-A", "AF 85mm F1.8", "Viltrox AF 85mm F1.8 FE"),
            ("SKU-B", "AF 35mm F1.8", "Viltrox AF 35mm F1.8 FE"),
            ("SKU-C", "EPIC 2035", "EPIC 20-35mm T2.1 Cine"),
        ],
    )
    conn.executemany(
        """
        INSERT INTO vkpi_kol_video_evidence (
            id, kol_pool_id, content_url, platform, evidence_type, is_active,
            video_title, published_at_norm, created_at, updated_at
        ) VALUES (?, ?, ?, 'youtube', 'video', 1, ?, ?, ?, ?)
        """,
        [
            (101, 1, "https://www.youtube.com/watch?v=aaaDEF12345", "Best portrait lens 2026",
             _iso(NOW - timedelta(days=10)), _iso(NOW - timedelta(days=10)), _iso(NOW)),
            (102, 2, "https://www.youtube.com/watch?v=bbbDEF12345", "Viltrox AF 85mm f/1.8 review",
             _iso(NOW - timedelta(days=9)), _iso(NOW - timedelta(days=9)), _iso(NOW)),
            (103, 1, "https://www.youtube.com/watch?v=cccDEF12345", "random vlog untitled",
             _iso(NOW - timedelta(days=8)), _iso(NOW - timedelta(days=8)), _iso(NOW)),
            (104, 1, "https://www.youtube.com/watch?v=dddDEF12345", "AF 85mm F1.8 vs AF 35mm F1.8 shootout",
             _iso(NOW - timedelta(days=7)), _iso(NOW - timedelta(days=7)), _iso(NOW)),
            (301, 3, "https://www.youtube.com/watch?v=eeeDEF12345", "Carol street photography",
             _iso(NOW - timedelta(days=6)), _iso(NOW - timedelta(days=6)), _iso(NOW)),
        ],
    )
    conn.commit()
    return conn


def _fake_active_enqueue(conn, *, job_type, payload, idempotency_key):
    import json

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
    return {"id": int(cursor.lastrowid), "job_type": job_type, "payload": payload, "status": "queued"}, True


@pytest.fixture()
def conn(monkeypatch):
    db = _conn()
    monkeypatch.setattr(video_metric_refresh, "enqueue_active_apify_job", _fake_active_enqueue)
    yield db
    db.close()


def _job_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM apify_jobs").fetchone()[0])


def _link_rows(conn, evidence_id: int) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT product_sku, relation_type FROM vkpi_kol_video_product_links WHERE evidence_id=? ORDER BY product_sku, relation_type",
            (evidence_id,),
        ).fetchall()
    ]


def _link_detail(conn, evidence_id: int) -> tuple[str, str, str, float]:
    row = conn.execute(
        """
        SELECT product_sku, relation_type, source, confidence
        FROM vkpi_kol_video_product_links
        WHERE evidence_id=?
        ORDER BY id DESC LIMIT 1
        """,
        (evidence_id,),
    ).fetchone()
    return (str(row[0]), str(row[1]), str(row[2]), float(row[3]))


def _seed_link(conn, evidence_id: int, sku: str, relation: str = "manual", confidence: float = 1.0) -> None:
    conn.execute(
        """
        INSERT INTO vkpi_kol_video_product_links (
            evidence_id, product_sku, relation_type, source, confidence,
            created_by_staff_id, created_at, updated_at
        ) VALUES (?, ?, ?, 'test_seed', ?, 10, NOW(), NOW())
        """,
        (evidence_id, sku, relation, confidence),
    )


def _seed_tracking(conn, evidence_id: int, staff_id: int = 10, status: str = "active") -> None:
    conn.execute(
        """
        INSERT INTO vkpi_kol_video_metric_tracking (
            evidence_id, tracked_by_staff_id, status, source, created_at, updated_at
        ) VALUES (?, ?, ?, 'my_kol_video_tracking', NOW(), NOW())
        """,
        (evidence_id, staff_id, status),
    )


def _seed_snapshot(conn, evidence_id: int, fetched_at: datetime, *, views=None, likes=None, status="success") -> None:
    conn.execute(
        """
        INSERT INTO vkpi_content_metric_snapshots (
            evidence_id, capture_key, fetched_at, views, likes, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (evidence_id, f"cap-{evidence_id}-{_iso(fetched_at)}-{status}", _iso(fetched_at), views, likes, status),
    )


# ── 一键数据关注:SKU 三级解析 ────────────────────────────────────────────────


def test_data_watch_uses_existing_links_and_queues_refresh(conn):
    _seed_link(conn, 101, "SKU-A", "detected", 0.9)
    result = video_data_watch.data_watch(
        conn, kol_pool_id=1, evidence_id=101, staff=STAFF_A
    )
    assert result == {
        "status": "tracking",
        "evidence_id": 101,
        "kol_pool_id": 1,
        "skus": ["SKU-A"],
        "sku_source": "existing",
        "sku_provenance": {
            "relation_type": "existing",
            "source": "existing_link",
            "confidence": None,
            "requires_human_confirmation": False,
        },
        "tracking": "active",
        "refresh": "queued",
    }
    # 既有关联不重复写:仍只有 detected 一行,没有多出 manual 行。
    assert _link_rows(conn, 101) == [("SKU-A", "detected")]
    assert _job_count(conn) == 1
    assert conn.execute(
        "SELECT status FROM vkpi_kol_video_metric_tracking WHERE evidence_id=101"
    ).fetchone()[0] == "active"
    # 再点一次幂等:同一 job,refresh=already_queued。
    again = video_data_watch.data_watch(conn, kol_pool_id=1, evidence_id=101, staff=STAFF_A)
    assert again["refresh"] == "already_queued"
    assert _job_count(conn) == 1


def test_data_watch_auto_detects_single_unambiguous_sku(conn):
    result = video_data_watch.data_watch(
        conn, kol_pool_id=2, evidence_id=102, staff=STAFF_A
    )
    assert result["status"] == "tracking"
    assert result["sku_source"] == "auto"
    assert result["sku_provenance"] == {
        "relation_type": "detected",
        "source": "title_alias_v1",
        "confidence": 0.6,
        "requires_human_confirmation": True,
    }
    assert result["skus"] == ["SKU-A"]
    assert result["refresh"] == "queued"
    assert _link_rows(conn, 102) == [("SKU-A", "detected")]
    assert _link_detail(conn, 102) == (
        "SKU-A",
        "detected",
        "title_alias_v1",
        0.6,
    )
    assert _job_count(conn) == 1


def test_data_watch_sku_required_when_nothing_detectable(conn):
    result = video_data_watch.data_watch(
        conn, kol_pool_id=1, evidence_id=103, staff=STAFF_A
    )
    assert result["status"] == "sku_required"
    assert result["evidence_id"] == 103
    assert {"sku_code": "SKU-A", "sku_name": "Viltrox AF 85mm F1.8 FE"} in result["candidates"]
    assert len(result["candidates"]) == 3
    assert _job_count(conn) == 0
    assert _link_rows(conn, 103) == []
    assert conn.execute("SELECT COUNT(*) FROM vkpi_kol_video_metric_tracking").fetchone()[0] == 0


def test_data_watch_ambiguous_title_returns_only_matched_candidates(conn):
    result = video_data_watch.data_watch(
        conn, kol_pool_id=1, evidence_id=104, staff=STAFF_A
    )
    assert result["status"] == "sku_required"
    assert [c["sku_code"] for c in result["candidates"]] == ["SKU-A", "SKU-B"]
    assert _job_count(conn) == 0


def test_data_watch_manual_skus_validated_and_linked(conn):
    result = video_data_watch.data_watch(
        conn, kol_pool_id=1, evidence_id=103, staff=STAFF_A,
        product_skus=["SKU-C", "SKU-C"],
    )
    assert result["status"] == "tracking"
    assert result["sku_source"] == "manual"
    assert result["sku_provenance"] == {
        "relation_type": "manual",
        "source": "my_kol_video_tracking",
        "confidence": 1.0,
        "requires_human_confirmation": False,
    }
    assert result["skus"] == ["SKU-C"]
    assert _link_rows(conn, 103) == [("SKU-C", "manual")]
    assert _job_count(conn) == 1

    with pytest.raises(video_tracking.VideoTrackingError) as not_list:
        video_data_watch.data_watch(
            conn, kol_pool_id=1, evidence_id=104, staff=STAFF_A, product_skus="SKU-A",
        )
    assert not_list.value.code == "product_skus_must_be_list"

    with pytest.raises(video_tracking.VideoTrackingError) as unknown:
        video_data_watch.data_watch(
            conn, kol_pool_id=1, evidence_id=104, staff=STAFF_A, product_skus=["NOPE"],
        )
    assert unknown.value.code == "unknown_product_sku"
    assert _link_rows(conn, 104) == []
    assert _job_count(conn) == 1


def test_data_watch_fences_and_missing_evidence(conn):
    # 别人的收藏(staff 40 未收藏 KOL 1)→ 与追踪路径同一 403 围栏。
    with pytest.raises(video_tracking.VideoTrackingError) as forbidden:
        video_data_watch.data_watch(conn, kol_pool_id=1, evidence_id=101, staff=STAFF_B)
    assert forbidden.value.status_code == 403
    # 证据不存在 / 不属于该 KOL → LookupError(路由映射 404)。
    with pytest.raises(LookupError):
        video_data_watch.data_watch(conn, kol_pool_id=1, evidence_id=999, staff=STAFF_A)
    with pytest.raises(LookupError):
        video_data_watch.data_watch(conn, kol_pool_id=1, evidence_id=102, staff=STAFF_A)
    assert _job_count(conn) == 0


# ── SKU 播放总览:分组 + 增量 + 诚实 null ─────────────────────────────────────


def _seed_overview(conn) -> None:
    _seed_link(conn, 101, "SKU-A", "manual")
    _seed_link(conn, 101, "SKU-A", "detected", 0.9)
    _seed_link(conn, 102, "SKU-A", "manual")
    _seed_link(conn, 103, "SKU-B", "manual")
    _seed_link(conn, 301, "SKU-A", "manual")
    for evidence_id, staff_id in ((101, 10), (102, 10), (103, 10), (301, 40)):
        _seed_tracking(conn, evidence_id, staff_id)
    _seed_snapshot(conn, 101, NOW - timedelta(days=8), views=1000, likes=100)
    _seed_snapshot(conn, 101, NOW - timedelta(hours=36), views=1500, likes=150)
    _seed_snapshot(conn, 101, NOW, views=1600, likes=160)
    _seed_snapshot(conn, 102, NOW - timedelta(days=1), status="failed")
    conn.commit()


def test_overview_groups_delta_math_and_null_honesty(conn):
    _seed_overview(conn)
    body = sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A, now=NOW)
    assert body["contract"] == "my_kol_sku_play_overview_v1"
    assert body["summary"] == {"skus": 2, "videos": 3, "kols": 2, "measured_videos": 1}
    assert body["empty_reason"] is None

    group_a, group_b = body["groups"]
    # 有实测的 SKU-A 排前,未实测的 SKU-B 排后(nulls last)。
    assert group_a["sku_code"] == "SKU-A"
    assert group_a["sku_name"] == "Viltrox AF 85mm F1.8 FE"
    # 同一视频 manual+detected 双关联只算一条。
    assert group_a["videos"] == 2 and group_a["kols"] == 2
    assert group_a["latest_measured_at"] == _iso(NOW)
    assert group_a["total_views"] == 1600
    assert group_a["delta"] == {"d1": 100, "d7": 600, "d30": 600}

    item_measured, item_unmeasured = group_a["items"]
    assert item_measured["evidence_id"] == 101
    assert item_measured["kol_name"] == "Alice"
    assert item_measured["view_count"] == 1600 and item_measured["like_count"] == 160
    assert item_measured["measured_at"] == _iso(NOW)
    assert item_measured["delta"] == {"d1": 100, "d7": 600, "d30": 600}
    assert item_measured["tracking_status"] == "active"
    # 只有 failed 快照 = 从未实测:一律 null,绝不编 0。
    assert item_unmeasured["evidence_id"] == 102
    assert item_unmeasured["view_count"] is None
    assert item_unmeasured["like_count"] is None
    assert item_unmeasured["measured_at"] is None
    assert item_unmeasured["delta"] == {"d1": None, "d7": None, "d30": None}

    assert group_b["sku_code"] == "SKU-B"
    assert group_b["latest_measured_at"] is None
    assert group_b["total_views"] is None
    assert group_b["delta"] == {"d1": None, "d7": None, "d30": None}


def test_overview_scope_isolates_staff_and_opens_for_manager(conn):
    _seed_overview(conn)
    # 员工 B 只看到自己收藏的 KOL 3(evidence 301),看不到员工 A 的三条。
    body_b = sku_play_overview.build_sku_play_overview(conn, staff=STAFF_B, now=NOW)
    assert body_b["summary"] == {"skus": 1, "videos": 1, "kols": 1, "measured_videos": 0}
    assert body_b["groups"][0]["sku_code"] == "SKU-A"
    assert [item["evidence_id"] for item in body_b["groups"][0]["items"]] == [301]
    # 员工 A 看不到员工 B 的 301。
    body_a = sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A, now=NOW)
    ids_a = {item["evidence_id"] for group in body_a["groups"] for item in group["items"]}
    assert 301 not in ids_a
    # 管理层全团队。
    body_m = sku_play_overview.build_sku_play_overview(conn, staff=MANAGER, now=NOW)
    assert body_m["summary"]["videos"] == 4 and body_m["summary"]["kols"] == 3


def test_overview_empty_is_honest(conn):
    body = sku_play_overview.build_sku_play_overview(conn, staff=STAFF_A, now=NOW)
    assert body["groups"] == []
    assert body["summary"] == {"skus": 0, "videos": 0, "kols": 0, "measured_videos": 0}
    assert body["empty_reason"] == "no_tracked_sku_videos"


# ── 路由挂载 + 错误映射 ───────────────────────────────────────────────────────


def test_routes_registered_and_error_mapping(conn, monkeypatch):
    from fastapi import HTTPException

    from app.api.routers import ADMIN_ROUTER_MODULES, vkpi_my_kol_sku_play
    from app.domains.audit import decorator as audit_decorator

    assert "vkpi_my_kol_sku_play" in ADMIN_ROUTER_MODULES
    paths = {(tuple(sorted(r.methods)), r.path) for r in vkpi_my_kol_sku_play.router.routes}
    assert (("POST",), "/api/admin/vkpi/my-kol/{kol_pool_id}/videos/{evidence_id}/data-watch") in paths
    assert (("GET",), "/api/admin/vkpi/my-kol/sku-play-overview") in paths

    monkeypatch.setattr(vkpi_my_kol_sku_play, "get_conn", lambda: conn)
    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **_kw: None)
    with pytest.raises(HTTPException) as missing:
        vkpi_my_kol_sku_play.my_kol_video_data_watch_endpoint(
            kol_pool_id=1, evidence_id=999, body={}, staff=STAFF_A,
        )
    assert missing.value.status_code == 404
    assert missing.value.detail == "video_evidence_not_found"
    with pytest.raises(HTTPException) as forbidden:
        vkpi_my_kol_sku_play.my_kol_video_data_watch_endpoint(
            kol_pool_id=1, evidence_id=101, body={}, staff=STAFF_B,
        )
    assert forbidden.value.status_code == 403
    assert forbidden.value.detail == "my_kol_video_write_forbidden"
