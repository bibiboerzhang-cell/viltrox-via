"""波 C·C5 观察清单:收藏即登记(best-effort)+ 分组进度总览(纯读)。

假库单元测试无需 PG;``pg`` 标记的用隔离库(prod 备份克隆)真数据,全部在一个事务里、
commit 被打成空操作,测试结束回滚,零残留::

    VKPI_PYTEST_ALLOW_LIVE_SERVICES=1 DATABASE_URL=postgresql://.../vkpi_closeout_test \
        PYTHONPATH=backend .venv/bin/python -m pytest tests/test_my_kol_watchlist.py -q
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domains.kol import favorite_side_effects, video_tracking_budget, video_tracking_enroll  # noqa: E402
from app.domains.kol import watchlist_overview as wo  # noqa: E402


NOW = datetime.now(timezone.utc)
_STAFF = {"id": 84, "user_id": 108, "role": "employee", "permissions": {"vkpi": "write"}}


class _FakeConn:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def execute(self, *_args, **_kwargs):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


# ── 收藏副作用(假库) ──────────────────────────────────────────────────────────


def test_side_effect_enrolls_once_and_commits(monkeypatch) -> None:
    conn = _FakeConn()
    calls: list[dict] = []
    monkeypatch.setattr(favorite_side_effects, "get_conn", lambda: conn)
    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": True, "reason": "within_cap"})

    def fake_enroll(c, **kwargs):
        assert c is conn
        calls.append(kwargs)
        return {"inserted": 3, "candidates": 5, "already_active": 2, "skipped": {"url_invalid": 0}}

    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", fake_enroll)
    out = favorite_side_effects.enroll_tracking_after_favorite(4321, staff=_STAFF)
    assert calls == [{"apply": True, "kol_pool_ids": [4321], "fallback_staff_id": 84}]
    assert conn.commits == 1
    assert out == {
        "tracking_enrolled": 3,
        "tracking_candidates": 5,
        "tracking_already_active": 2,
        "tracking_skipped": {"url_invalid": 0},
        "tracking_enroll_reason": None,
    }


def test_side_effect_failure_is_logged_not_raised(monkeypatch, caplog) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(favorite_side_effects, "get_conn", lambda: conn)
    monkeypatch.setattr(video_tracking_budget, "budget_gate", lambda c, **kw: {"allowed": True})

    def boom(*_a, **_k):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", boom)
    with caplog.at_level(logging.WARNING):
        out = favorite_side_effects.enroll_tracking_after_favorite(4321, staff=_STAFF)
    assert out["tracking_enrolled"] == 0 and out["tracking_enroll_reason"] == "enroll_failed"
    assert conn.commits == 0 and conn.rollbacks == 1
    assert any("favorite.tracking_enroll_failed" in rec.getMessage() for rec in caplog.records)


def test_side_effect_respects_monthly_budget_gate(monkeypatch, caplog) -> None:
    conn = _FakeConn()
    monkeypatch.setattr(favorite_side_effects, "get_conn", lambda: conn)
    monkeypatch.setattr(
        video_tracking_budget, "budget_gate",
        lambda c, **kw: {"allowed": False, "reason": "hard_stop_or_projected_cap:metric_tracking", "spend_usd": 31.0, "cap_usd": 30.0},
    )
    monkeypatch.setattr(video_tracking_enroll, "enroll_my_kol_evidence", lambda *a, **k: pytest.fail("must not enroll"))
    with caplog.at_level(logging.WARNING):
        out = favorite_side_effects.enroll_tracking_after_favorite(4321, staff=_STAFF)
    assert out["tracking_enrolled"] == 0
    assert out["tracking_enroll_reason"] == "hard_stop_or_projected_cap:metric_tracking"
    assert any("favorite.tracking_enroll_skipped" in rec.getMessage() for rec in caplog.records)
    assert favorite_side_effects.enroll_tracking_after_favorite(4321, staff=None)["tracking_enroll_reason"] == "staff_identity_required"


def test_favorite_endpoint_returns_favorite_even_if_enroll_blows_up(monkeypatch) -> None:
    """路由层:主写成功 + 副作用炸 → 仍 200,响应带 tracking_enrolled=0。"""
    from app.api.routers import vkpi_kol_pool
    from app.domains.audit import decorator as audit_decorator
    from app.domains.kol import pool_favorites

    monkeypatch.setattr(audit_decorator, "_safe_log_audit", lambda **_kw: None)
    monkeypatch.setattr(vkpi_kol_pool, "_record_pool_feedback_signal", lambda *a, **k: None)
    monkeypatch.setattr(pool_favorites, "add_favorite", lambda pid, **kw: {"status": "favorited", "kol_pool_id": pid, "favorite_id": 9})

    def db_down():
        raise RuntimeError("db down")

    monkeypatch.setattr(favorite_side_effects, "get_conn", db_down)

    result = vkpi_kol_pool.favorite_kol_pool_item(kol_pool_id=77, body={}, staff=_STAFF)
    assert result["status"] == "favorited" and result["favorite_id"] == 9
    assert result["tracking_enrolled"] == 0 and result["tracking_enroll_reason"] == "enroll_failed"


# ── 总览纯函数(假库) ──────────────────────────────────────────────────────────


def test_group_visibility_rules() -> None:
    group = {"member_ids": [6760, 84], "created_by": 40}
    assert wo.group_visible_to(group, {"id": 84, "role": "employee"})
    assert wo.group_visible_to(group, {"id": 40, "role": "employee"})
    assert not wo.group_visible_to(group, {"id": 99, "role": "employee"})
    assert wo.group_visible_to(group, {"id": 99, "role": "admin", "is_owner": 1})
    assert not wo.group_visible_to(group, None)


def test_build_rows_with_no_kols_is_honest_empty() -> None:
    built = wo.build_kol_rows(_FakeConn(), [], now=NOW)
    assert built["rows"] == [] and built["missing_kol_ids"] == [] and built["tracked_truncated"] is False
    summary = wo.summarize_rows([])
    assert summary["kol_count"] == 0 and summary["empty_reason"] == "no_kols_in_group"
    assert summary["views_delta_7d"] is None and summary["views_delta_7d_reason"] == "no_tracked_videos"
    assert summary["deep_analysis"]["completion_ratio"] is None


def test_delta_7d_reasons() -> None:
    never = {"tracking": {"history": "never_measured"}, "windows": {"7d": {"views": {"status": "insufficient_history", "delta": None}}}}
    single = {"tracking": {"history": "single_sample"}, "windows": {"7d": {"views": {"status": "insufficient_history", "delta": None}}}}
    ready = {"tracking": {"history": "ready"}, "windows": {"7d": {"views": {"status": "ready", "delta": 120}}}}
    partial = {"tracking": {"history": "ready"}, "windows": {"7d": {"views": {"status": "partial", "delta": 30}}}}
    assert wo._delta_7d([])["reason"] == "no_tracked_videos"
    assert wo._delta_7d([never])["reason"] == "never_measured"
    assert wo._delta_7d([never, single])["reason"] == "insufficient_samples"
    assert wo._delta_7d([ready, partial, never]) == {"value": 150, "reason": None, "videos": 2, "status": "ready"}
    assert wo._delta_7d([partial])["status"] == "partial"


def test_routes_registered_and_whitelisted() -> None:
    from app.api.routers import ADMIN_ROUTER_MODULES, vkpi_my_kol_watchlist
    from app.core import release_validation

    assert "vkpi_my_kol_watchlist" in ADMIN_ROUTER_MODULES
    paths = {(tuple(sorted(r.methods)), r.path) for r in vkpi_my_kol_watchlist.router.routes}
    assert (("GET",), "/api/admin/vkpi/my-kol/watch-overview") in paths
    assert (("GET",), "/api/admin/vkpi/my-kol/groups/{group_id}/watch-overview") in paths
    assert release_validation.release_validation_request_allowed("GET", "/api/admin/vkpi/my-kol/watch-overview")
    assert release_validation.release_validation_request_allowed(
        "GET", "/api/admin/vkpi/my-kol/groups/grp_1782952055020/watch-overview",
    )
    assert not release_validation.release_validation_request_allowed("POST", "/api/admin/vkpi/my-kol/watch-overview")


def test_group_endpoint_maps_lookup_and_permission_errors(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.api.dependencies.perms as perms_mod
    from app.api.dependencies.auth import get_user_required
    from app.api.routers import vkpi_my_kol_watchlist

    staff = {"id": 84, "user_id": 108, "role": "employee", "permissions": {"vkpi": "read"}, "is_owner": 0}
    monkeypatch.setattr(perms_mod, "staff_context_for_user", lambda user: staff)
    app = FastAPI()
    app.include_router(vkpi_my_kol_watchlist.router)
    app.dependency_overrides[get_user_required] = lambda: {"id": 108}

    def fake_group_overview(conn, *, group_id, staff, kol_limit):
        if group_id == "missing":
            raise LookupError("group not found")
        if group_id == "foreign":
            raise PermissionError("nope")
        return {"contract": wo.CONTRACT, "group": {"group_id": group_id}, "items": []}

    monkeypatch.setattr(vkpi_my_kol_watchlist, "get_conn", lambda: object())
    monkeypatch.setattr(wo, "group_overview", fake_group_overview)
    monkeypatch.setattr(wo, "watch_overview", lambda conn, *, staff, group_limit: {"contract": wo.CONTRACT, "groups": []})
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/admin/vkpi/my-kol/groups/missing/watch-overview").status_code == 404
    assert client.get("/api/admin/vkpi/my-kol/groups/foreign/watch-overview").status_code == 403
    ok = client.get("/api/admin/vkpi/my-kol/groups/grp_1/watch-overview")
    assert ok.status_code == 200 and ok.json()["group"]["group_id"] == "grp_1"
    assert client.get("/api/admin/vkpi/my-kol/watch-overview").json()["groups"] == []


# ── 隔离库真数据(pg) ──────────────────────────────────────────────────────────


def _seed_actor(conn) -> int:
    seed = 910_000_000 + (uuid.uuid4().int % 80_000_000)
    conn.execute(
        """
        INSERT INTO users (id, email, password_hash, name, status, role, email_verified)
        VALUES (?, ?, '!pg-test-only!', 'Watchlist PG', 'active', 'creator', 1)
        """,
        (seed, f"watchlist-pg-{seed}@example.invalid"),
    )
    conn.execute(
        """
        INSERT INTO staff (id, user_id, role, permissions_json, active, is_owner, accepted_at)
        VALUES (?, ?, 'employee', ?, 1, 0, NOW())
        """,
        (seed, seed, json.dumps({"vkpi": "write", "kol_ops": "read"})),
    )
    return seed


def _seed_kol(conn, *, platform: str = "youtube") -> int:
    uid = uuid.uuid4().hex[:12]
    row = conn.execute(
        "INSERT INTO vkpi_kol_pool (pool_uid, platform, handle, display_name) VALUES (?, ?, ?, ?) RETURNING id",
        (f"wl-{uid}", platform, f"wl_{uid}", f"Watch {uid}"),
    ).fetchone()
    return int(dict(row)["id"])


def _seed_evidence(conn, *, kol_id: int, url: str, published_at: datetime, platform: str = "youtube") -> int:
    row = conn.execute(
        """
        INSERT INTO vkpi_kol_video_evidence (
            kol_pool_id, content_url, platform, source, evidence_type, is_active,
            media_kind, published_at_norm, posted_at, channel_id
        ) VALUES (?, ?, ?, 'pgtest', 'video', TRUE, 'video', ?, ?, 'UC-wl')
        RETURNING id
        """,
        (kol_id, url, platform, published_at, published_at),
    ).fetchone()
    return int(dict(row)["id"])


def _snapshot(conn, *, evidence_id: int, at: datetime, views: int | None, status: str = "success") -> None:
    conn.execute(
        """
        INSERT INTO vkpi_content_metric_snapshots (evidence_id, capture_key, provider, fetched_at, views, status, error_code)
        VALUES (?, ?, 'pgtest', ?, ?, ?, ?)
        """,
        (evidence_id, f"wl-{uuid.uuid4().hex}", at, views, status, None if status == "success" else "provider_error"),
    )


def _seed_group(conn, *, name: str, member_ids: list[int], kol_ids: list[int], created_by: int) -> str:
    gid = f"grp_{int(NOW.timestamp() * 1000)}_{uuid.uuid4().hex[:6]}"
    perms = {"shared_kol_pool_ids": kol_ids, "shared_projects": [], "shared_kol_pool": "", "kpi_goal": "", "reminder_rule": ""}
    conn.execute(
        """
        INSERT INTO vkpi_staff_groups (id, name, description, member_ids, permissions_json, created_by)
        VALUES (?, ?, '', ?::jsonb, ?::jsonb, ?)
        """,
        (gid, name, json.dumps(member_ids), json.dumps(perms), created_by),
    )
    return gid


@pytest.fixture()
def watch_pg(pg_compat, monkeypatch):
    monkeypatch.setattr(pg_compat, "commit", lambda: None)
    return pg_compat, _seed_actor(pg_compat)


@pytest.mark.pg
def test_favorite_side_effect_registers_tracking_on_real_schema(watch_pg, monkeypatch) -> None:
    conn, actor = watch_pg
    monkeypatch.setattr(favorite_side_effects, "get_conn", lambda: conn)
    video_tracking_budget.ensure_budget_scope(conn, now=NOW)
    kol_id = _seed_kol(conn)
    tag = uuid.uuid4().hex[:8]
    hot = _seed_evidence(conn, kol_id=kol_id, url=f"https://www.youtube.com/watch?v=a{tag}", published_at=NOW - timedelta(days=1))
    cold = _seed_evidence(conn, kol_id=kol_id, url=f"https://www.youtube.com/watch?v=b{tag}", published_at=NOW - timedelta(days=60))
    staff = {"id": actor, "user_id": actor, "role": "employee", "permissions": {"vkpi": "write"}}
    conn.execute("INSERT INTO vkpi_kol_pool_favorites (kol_pool_id, staff_id) VALUES (?, ?)", (kol_id, actor))

    first = favorite_side_effects.enroll_tracking_after_favorite(kol_id, staff=staff)
    assert first["tracking_enrolled"] == 2 and first["tracking_candidates"] == 2 and first["tracking_enroll_reason"] is None
    rows = conn.execute(
        "SELECT evidence_id, status, source, tracked_by_staff_id FROM vkpi_kol_video_metric_tracking WHERE evidence_id IN (?, ?) ORDER BY evidence_id",
        (min(hot, cold), max(hot, cold)),
    ).fetchall()
    assert [(dict(r)["status"], dict(r)["source"], int(dict(r)["tracked_by_staff_id"])) for r in rows] == [
        ("active", video_tracking_enroll.ENROLL_SOURCE, actor)] * 2
    second = favorite_side_effects.enroll_tracking_after_favorite(kol_id, staff=staff)
    assert second["tracking_enrolled"] == 0 and second["tracking_already_active"] == 2
    assert second["tracking_enroll_reason"] == "already_enrolled"
    assert conn.execute("SELECT COUNT(*) AS n FROM apify_jobs WHERE payload->>'evidence_id' IN (?, ?)",
                        (str(hot), str(cold))).fetchone()["n"] == 0


@pytest.mark.pg
def test_group_overview_rows_on_real_schema(watch_pg) -> None:
    conn, actor = watch_pg
    tracked_kol = _seed_kol(conn)
    untracked_kol = _seed_kol(conn, platform="instagram")
    empty_kol = _seed_kol(conn, platform="tiktok")
    tag = uuid.uuid4().hex[:8]
    measured = _seed_evidence(conn, kol_id=tracked_kol, url=f"https://www.youtube.com/watch?v=m{tag}", published_at=NOW - timedelta(days=20))
    failing = _seed_evidence(conn, kol_id=tracked_kol, url=f"https://www.youtube.com/watch?v=f{tag}", published_at=NOW - timedelta(days=3))
    _seed_evidence(conn, kol_id=untracked_kol, url=f"https://www.instagram.com/reel/U{tag}/", published_at=NOW - timedelta(days=3), platform="instagram")
    for eid in (measured, failing):
        conn.execute(
            "INSERT INTO vkpi_kol_video_metric_tracking (evidence_id, tracked_by_staff_id, status, source) VALUES (?, ?, 'active', 'pgtest')",
            (eid, actor),
        )
    _snapshot(conn, evidence_id=measured, at=NOW - timedelta(days=9), views=1000)
    _snapshot(conn, evidence_id=measured, at=NOW - timedelta(hours=2), views=1450)
    _snapshot(conn, evidence_id=failing, at=NOW - timedelta(hours=1), views=None, status="failed")
    cache = conn.execute(
        """
        INSERT INTO vkpi_analysis_cache (target_type, target_id, derive_method, status, result)
        VALUES ('video', ?, 'video_analysis_final_v1', 'ready', '{}'::jsonb) RETURNING id
        """,
        (str(measured),),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO apify_jobs (job_type, status, payload)
        VALUES ('video', 'failed', ?::jsonb)
        """,
        (json.dumps({"derive_method": "video_analysis_final_v1", "target_type": "video", "target_id": str(failing)}),),
    )
    for key, name, count in (("af75mmf12pro", "AF 75mm F1.2 Pro", 5), ("af27mmf12pro", "AF 27mm F1.2 Pro", 2), ("af23mmf14", "AF 23mm F1.4", 1), ("af85mmf18ii", "AF 85mm F1.8 II", 1)):
        conn.execute(
            """
            INSERT INTO vkpi_kol_lens_evidence (cache_id, evidence_id, kol_pool_id, mention_text, mention_norm, resolution, lens_key, display_name, category_main, mention_count)
            VALUES (?, ?, ?, ?, ?, 'family', ?, ?, 'Lens', ?)
            """,
            (int(dict(cache)["id"]), measured, tracked_kol, name, f"{key}-{tag}", key, name, count),
        )
    gid = _seed_group(conn, name="观察组", member_ids=[actor], kol_ids=[tracked_kol, untracked_kol, empty_kol, 999_999_999], created_by=actor)
    staff = {"id": actor, "user_id": actor, "role": "employee", "permissions": {"vkpi": "read"}}

    body = wo.group_overview(conn, group_id=gid, staff=staff, now=NOW)
    assert body["contract"] == wo.CONTRACT and body["read_only"] is True
    assert body["group"]["group_id"] == gid and body["group"]["member_count"] == 1
    assert body["missing_kol_ids"] == [999_999_999] and body["kol_ids_configured"] == 4
    by_id = {row["kol_pool_id"]: row for row in body["items"]}
    assert [row["kol_pool_id"] for row in body["items"]] == [tracked_kol, untracked_kol, empty_kol]

    tracked = by_id[tracked_kol]
    assert tracked["tracking_state"] == "tracked" and tracked["tracked_videos"] == 2
    assert tracked["views_delta_7d"] == 450 and tracked["views_delta_7d_reason"] is None
    assert tracked["views_delta_7d_videos"] == 1 and tracked["views_delta_7d_status"] == "ready"
    assert tracked["last_snapshot_at"] == (NOW - timedelta(hours=2)).isoformat(timespec="seconds")
    assert tracked["last_metric_attempt_at"] == (NOW - timedelta(hours=1)).isoformat(timespec="seconds")
    assert tracked["deep_analysis"] == {
        "completed": 1, "in_progress": 0, "failed": 1, "not_requested": 0,
        "scope_videos": 2, "scope_limit": 20, "completion_ratio": 0.5,
    }
    assert tracked["open_failures"] == 2
    assert tracked["open_failures_breakdown"] == {"deep_analysis": 1, "metric_refresh": 1}
    assert [lens["lens_key"] for lens in tracked["lens_families"]] == ["af75mmf12pro", "af27mmf12pro", "af23mmf14"]
    assert tracked["lens_families"][0]["mentions"] == 5 and tracked["lens_families"][0]["display_name"] == "AF 75mm F1.2 Pro"
    assert tracked["last_activity_at"] is not None

    assert by_id[untracked_kol]["tracking_state"] == "untracked"
    assert by_id[untracked_kol]["views_delta_7d_reason"] == "no_tracked_videos"
    assert by_id[untracked_kol]["deep_analysis"]["not_requested"] == 1
    assert by_id[empty_kol]["tracking_state"] == "no_videos" and by_id[empty_kol]["lens_families"] == []

    summary = body["summary"]
    assert summary["kol_count"] == 3 and summary["tracked_count"] == 1
    assert summary["untracked_count"] == 1 and summary["no_videos_count"] == 1
    assert summary["tracked_videos_total"] == 2 and summary["views_delta_7d"] == 450
    assert summary["open_failures"] == 2 and summary["deep_analysis"]["completed"] == 1
    assert summary["empty_reason"] is None

    with pytest.raises(PermissionError):
        wo.group_overview(conn, group_id=gid, staff={"id": actor + 1, "role": "employee"}, now=NOW)
    with pytest.raises(LookupError):
        wo.group_overview(conn, group_id="grp_does_not_exist", staff=staff, now=NOW)

    overview = wo.watch_overview(conn, staff=staff, now=NOW)
    cards = {card["group_id"]: card for card in overview["groups"]}
    assert gid in cards and cards[gid]["summary"]["kol_count"] == 3
    assert cards[gid]["summary"]["views_delta_7d"] == 450
    assert overview["totals"]["group_count"] >= 1 and overview["empty_reason"] is None
    assert overview["totals"]["favorites_not_in_any_group"] == 0
    assert overview["viewer_scope"]["scope_mode"] == "own"
    # 非成员看不到这个组(管理层以外只见本人分组)
    other = wo.watch_overview(conn, staff={"id": actor + 1, "user_id": actor + 1, "role": "employee"}, now=NOW)
    assert gid not in {card["group_id"] for card in other["groups"]}


@pytest.mark.pg
def test_real_favorites_batch_rows_are_consistent(pg_compat) -> None:
    """prod 备份里的全部收藏 KOL 一次批量出行:行数 = 收藏 KOL 数,追踪总数 = 订阅表 active 行数。"""
    conn = pg_compat
    fav_ids = [int(dict(r)["kol_pool_id"]) for r in conn.execute(
        "SELECT DISTINCT kol_pool_id FROM vkpi_kol_pool_favorites ORDER BY kol_pool_id").fetchall()]
    if not fav_ids:
        pytest.skip("isolated db has no favorites")
    built = wo.build_kol_rows(conn, fav_ids, now=NOW)
    assert len(built["rows"]) + len(built["missing_kol_ids"]) == len(fav_ids)
    expected_tracked = conn.execute(
        """
        SELECT COUNT(*) AS n FROM vkpi_kol_video_metric_tracking t
        JOIN vkpi_kol_video_evidence e ON e.id = t.evidence_id
        WHERE t.status = 'active' AND e.is_active IS NOT FALSE
          AND EXISTS (SELECT 1 FROM vkpi_kol_pool_favorites f WHERE f.kol_pool_id = e.kol_pool_id)
        """
    ).fetchone()["n"]
    summary = wo.summarize_rows(built["rows"])
    if not built["tracked_truncated"]:
        assert summary["tracked_videos_total"] == int(expected_tracked)
    for row in built["rows"]:
        assert row["tracking_state"] in {"tracked", "untracked", "no_videos"}
        assert (row["views_delta_7d"] is None) == (row["views_delta_7d_reason"] is not None)
        assert len(row["lens_families"]) <= wo.LENS_TOP_N
        deep = row["deep_analysis"]
        assert deep["completed"] + deep["in_progress"] + deep["failed"] + deep["not_requested"] == deep["scope_videos"]
