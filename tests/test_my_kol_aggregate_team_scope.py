"""MY KOL aggregate ?scope=team(管理层全团队收藏集)契约测试 —— 零 DB 依赖。

覆盖点(2026-07-12 库 scope 补刀):
  1. 路由硬闸:manager(can_view_all)+ scope=team → team_scope=True;
     员工传 scope=team 恒被压回 own-only(team_scope=False,后端硬闸);
     显式 ?staff_id= 优先于 scope=team(manager 跨看单人)。
  2. _pool_favorites_team 行形状与 _pool_favorites 对齐(favorite_id/is_shared/
     projects/contacts 防御性解析;is_shared 强转 bool —— BOOLEAN 读回 int 1/0 陷阱)。
  3. build_my_kol_aggregate(team_scope=True) 走团队查询并打 scope_mode=team 标记。
红线:纯读契约,不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routers import vkpi_my_kol as router_mod  # noqa: E402
from app.domains.kol import my_kol_aggregate as agg  # noqa: E402


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """按 SQL 关键词路由的假连接(只服务本文件的两条查询)。"""

    def __init__(self, team_rows=None, staff_rows=None):
        self.team_rows = team_rows or []
        self.staff_rows = staff_rows or []
        self.calls: list[str] = []

    def execute(self, sql, params=()):
        self.calls.append(sql)
        if "GROUP BY kp.id" in sql:
            return _FakeResult(self.team_rows)
        if "FROM staff s" in sql:
            return _FakeResult(self.staff_rows)
        return _FakeResult([])


_TEAM_ROW = {
    "favorite_id": 11,
    "kol_pool_id": 6224,
    "note": "",
    "created_at": "2026-07-01T00:00:00",
    "is_shared": 0,  # compat BOOLEAN 读回 int 0/1 —— 必须被强转 bool
    "shared_by_staff_id": None,
    "shared_by_name": "",
    "platform": "youtube",
    "handle": "@alpha",
    "display_name": "Alpha Cam",
    "followers": 120000,
    "viltrox_fit_score": 82,
    "profile_url": "",
    "avatar_url": "",
    "country": "US",
    "projects_json": '[{"project_id": 7, "stage": "shipped"}]',
    "contacts_json": '[{"contact_type":"email","contact_value":"secret@example.com","contact_source":"bio","consent_basis":"public"}]',
}


def test_pool_favorites_team_shape_and_bool_coercion():
    conn = _FakeConn(team_rows=[dict(_TEAM_ROW)])
    rows = agg._pool_favorites_team(conn, actor={"id": 84, "role": "owner"})
    assert len(rows) == 1
    row = rows[0]
    # 形状与 _pool_favorites 对齐(前端 mapLibraryRows 零改映射)
    for key in ("favorite_id", "kol_pool_id", "is_shared", "shared_by_name", "projects", "contacts", "created_at"):
        assert key in row
    assert row["is_shared"] is False  # int 0 → bool False(读回陷阱防线)
    assert row["projects"] == [{"project_id": 7, "stage": "shipped"}]  # 字符串 jsonb 防御性解析
    assert row["contacts"] == [{
        "consent_basis": "public",
        "contact_masked": True,
    }]
    assert row["contact_masked"] is True
    assert "secret@example.com" not in str(row)
    assert "s***@e***" not in str(row)
    assert "contact_value" not in str(row["contacts"])
    # SQL 红线:团队查询零参数(无 ? 占位)、SQL 字符串内零注释
    team_sql = next(sql for sql in conn.calls if "GROUP BY kp.id" in sql)
    assert "?" not in team_sql
    assert "--" not in team_sql


def test_staff_favorite_projection_uses_project_scope_and_masks_contacts():
    class _ProjectionConn:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, tuple(params)))
            return _FakeResult([{
                **_TEAM_ROW,
                "is_shared": 0,
                "projects_json": '[{"project_id":11,"project_name":"Mine","stage":"discovered"}]',
            }])

    conn = _ProjectionConn()
    rows = agg._pool_favorites(conn, 84, actor={"id": 84, "role": "staff"})

    sql, params = conn.calls[0]
    assert "p.assigned_staff_id = ?" in sql
    assert "p.created_by_staff_id = ?" in sql
    assert "vkpi_project_members" in sql
    assert "p.is_public" in sql
    assert params == (84, 84, 84, 84, 84)
    assert rows[0]["projects"] == [{"project_id": 11, "project_name": "Mine", "stage": "discovered"}]
    assert rows[0]["contacts"] == [{
        "consent_basis": "public",
        "contact_masked": True,
    }]
    assert "secret@example.com" not in str(rows[0])
    assert "s***@e***" not in str(rows[0])
    assert "contact_value" not in str(rows[0]["contacts"])


def test_team_scope_is_denied_in_domain_for_non_manager(monkeypatch):
    monkeypatch.setattr(agg, "_staff_row", lambda *_: {"id": 84, "role": "staff"})
    with pytest.raises(agg.scope.ScopeDenied, match="team MY KOL scope denied"):
        agg.build_my_kol_aggregate(object(), 84, actor={"id": 84, "role": "staff"}, team_scope=True)


def test_build_aggregate_team_scope_marks_and_routes(monkeypatch):
    conn = _FakeConn(
        team_rows=[dict(_TEAM_ROW)],
        staff_rows=[{"id": 84, "role": "owner", "active": 1, "user_id": 1, "name": "Boss", "email": "b@x.com", "avatar_url": ""}],
    )
    monkeypatch.setattr(agg, "_projects", lambda *a, **k: [])
    monkeypatch.setattr(agg, "_official_matrix", lambda *a, **k: {"platforms": [], "account_count": 0})
    body = agg.build_my_kol_aggregate(conn, 84, actor={"id": 84, "role": "owner"}, team_scope=True)
    assert body["scope_mode"] == "team"
    assert body["kpi_summary"]["favorites_count"] == 1
    assert body["pool_favorites"][0]["kol_pool_id"] == 6224
    body_own = agg.build_my_kol_aggregate(conn, 84, actor={"id": 84}, team_scope=False)
    assert body_own["scope_mode"] == "staff"


def test_router_scope_team_hard_gate(monkeypatch):
    captured = {}

    def fake_build(conn, staff_id, window_days=30, *, actor=None, team_scope=False):
        captured["team_scope"] = team_scope
        captured["staff_id"] = staff_id
        return {"ok": True}

    monkeypatch.setattr(router_mod.my_kol_aggregate, "build_my_kol_aggregate", fake_build)
    monkeypatch.setattr(router_mod, "get_conn", lambda: object())
    monkeypatch.setattr(router_mod.scope, "can_view_all", lambda staff, **kw: bool(staff.get("can_view_all")))
    monkeypatch.setattr(
        router_mod.scope,
        "effective_staff_id",
        lambda staff, sid=None: sid if staff.get("can_view_all") else staff.get("sid"),
    )
    monkeypatch.setattr(router_mod.scope, "actor_staff_id", lambda staff: staff.get("sid"))

    manager = {"can_view_all": True, "sid": 84}
    employee = {"can_view_all": False, "sid": 5}

    # manager + scope=team → 全团队口径
    router_mod.my_kol_aggregate_endpoint(staff_id=None, window_days=30, scope_mode="team", staff=manager)
    assert captured["team_scope"] is True and captured["staff_id"] == 84

    # 员工 + scope=team → 后端硬闸压回 own-only(拿到的仍是自己的)
    router_mod.my_kol_aggregate_endpoint(staff_id=None, window_days=30, scope_mode="team", staff=employee)
    assert captured["team_scope"] is False and captured["staff_id"] == 5

    # manager + 显式 staff_id → 单人跨看优先,scope=team 让位
    router_mod.my_kol_aggregate_endpoint(staff_id=7, window_days=30, scope_mode="team", staff=manager)
    assert captured["team_scope"] is False and captured["staff_id"] == 7

    # 缺省(无 scope 参数)行为不变:manager 自查回落本人 own-only
    router_mod.my_kol_aggregate_endpoint(staff_id=None, window_days=30, scope_mode="", staff=manager)
    assert captured["team_scope"] is False and captured["staff_id"] == 84
