"""活动共享 + 写收口验证(2026-06-14,镜像 test_vkpi_scope.py 的项目共享段)。

证明:
(a) 成员 A 在 list_events 里看不到 B 的活动(owner/team 都不沾);
(b) 把 B 的活动共享给 A 后,A 的 list_events 纳入该活动(ADDITIVE 加宽生效);
(c) viewer 共享成员:assert_event_access 读放行、写 ScopeDenied;
(d) editor 共享成员:读写均放行;
(e) 非成员但有 vkpi:write 的员工:不能改别人的活动(写收口生效)——
    且 admin(can_view_all)与 owner 保持完全读写(收紧未误伤)。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.domains.access import scope
from app.domains.events import service


# ── fakes ───────────────────────────────────────────────────────────────────
class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row if isinstance(self._row, list) else []


class _RoutingConn:
    """按 SQL 命中的表路由:vkpi_events(活动行)vs vkpi_event_members(成员行)。

    assert_event_access 先查 vkpi_events 拿 owner_id/team_ids,再(若非 owner/team)
    查 vkpi_event_members 拿 role。单行 fake 模不出两步,故按表名路由。
    """

    def __init__(self, event_row, member_row):
        self.event_row = event_row
        self.member_row = member_row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "vkpi_event_members" in sql:
            return _FakeResult(self.member_row)
        return _FakeResult(self.event_row)


def staff(**overrides):
    data = {"id": 10, "role": "employee", "is_owner": 0}
    data.update(overrides)
    return data


# ── (a)/(b) list_events 可见性:真 in-memory sqlite ───────────────────────────
def _seed_events_sqlite():
    """A(staff 10)、B(staff 20)各有一个活动;A 非 admin。

    team_ids 存 JSON 文本(镜像 jsonb 的字符串回读);可见性 OR 子查询用 sqlite 验。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_events (
            id TEXT PRIMARY KEY,
            owner_id INTEGER,
            team_ids TEXT DEFAULT '[]',
            is_public INTEGER DEFAULT 0,
            start_date TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE vkpi_event_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            staff_id INTEGER,
            role TEXT DEFAULT 'viewer'
        )
        """
    )
    conn.execute(
        "INSERT INTO vkpi_events (id, owner_id, team_ids, start_date, created_at) VALUES (?,?,?,?,?)",
        ("evt_A", 10, json.dumps([10]), "2026-06-01", "2026-06-01"),
    )
    conn.execute(
        "INSERT INTO vkpi_events (id, owner_id, team_ids, start_date, created_at) VALUES (?,?,?,?,?)",
        ("evt_B", 20, json.dumps([20]), "2026-06-02", "2026-06-02"),
    )
    conn.commit()
    return conn


def _visible_event_ids_sqlite(conn, actor_staff):
    """复刻 service.list_events 非 admin 分支的可见性逻辑(sqlite 无 jsonb,用 LIKE 近似 team 命中)。

    关键被测点是 ADDITIVE 的第三条 OR(vkpi_event_members 子查询),其语义在 sqlite 等价。
    """
    sid = scope.actor_staff_id(actor_staff)
    rows = conn.execute(
        "SELECT id FROM vkpi_events "
        "WHERE owner_id = ? "
        "OR team_ids LIKE ? "
        "OR id IN (SELECT event_id FROM vkpi_event_members WHERE staff_id = ?) "
        "OR COALESCE(is_public, 0) = 1",  # 公共活动「加宽读」(镜像 service.list_events 的 OR is_public)
        (sid, f"%{sid}%", sid),
    ).fetchall()
    return {r["id"] for r in rows}


def test_list_events_excludes_other_employees_event():
    # (a) 员工 A 看不到 B 的活动(owner/team 都非 A,未共享不暴露)。
    conn = _seed_events_sqlite()
    visible = _visible_event_ids_sqlite(conn, staff(id=10))
    assert "evt_A" in visible
    assert "evt_B" not in visible


def test_list_events_includes_shared_event_via_members():
    # (b) 把 B 的活动 evt_B 显式共享给 A 后,A 的 list 经 IN 子查询纳入 evt_B(加宽生效)。
    conn = _seed_events_sqlite()
    conn.execute(
        "INSERT INTO vkpi_event_members (event_id, staff_id, role) VALUES (?,?,?)",
        ("evt_B", 10, "viewer"),
    )
    conn.commit()
    visible = _visible_event_ids_sqlite(conn, staff(id=10))
    assert "evt_A" in visible
    assert "evt_B" in visible  # 加宽生效:共享活动可见
    # B 不因 A 的共享而看到 A 的活动(共享单向、显式)。
    assert _visible_event_ids_sqlite(conn, staff(id=20)) == {"evt_B"}


def test_real_list_events_uses_exact_sqlite_json_membership(monkeypatch):
    """Exercise the production query instead of the historical LIKE mirror."""
    conn = _seed_events_sqlite()
    # A distinct id proves exact JSON membership: staff 1 must not match [10].
    conn.execute(
        "INSERT INTO vkpi_events (id, owner_id, team_ids, start_date, created_at) VALUES (?,?,?,?,?)",
        ("evt_team_10", 20, json.dumps([10]), "2026-06-03", "2026-06-03"),
    )
    conn.execute(
        "INSERT INTO vkpi_events (id, owner_id, team_ids, start_date, created_at) VALUES (?,?,?,?,?)",
        ("evt_invalid_team_json", 20, "not-json", "2026-06-04", "2026-06-04"),
    )
    conn.commit()
    monkeypatch.setattr(service, "get_conn", lambda: conn)
    monkeypatch.setattr(service, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(scope, "event_organization_context", lambda _staff, _conn: (1, False))

    visible_for_10 = {item["id"] for item in service.list_events(staff(id=10))["items"]}
    visible_for_1 = {item["id"] for item in service.list_events(staff(id=1))["items"]}

    assert "evt_team_10" in visible_for_10
    assert "evt_team_10" not in visible_for_1
    assert "evt_invalid_team_json" not in visible_for_10
    executed_sql = service._event_visibility_sql()
    assert "json_each" in executed_sql
    assert "@>" not in executed_sql


# ── (c)/(d)/(e) assert_event_access 写收口 ────────────────────────────────────
def test_assert_event_access_shared_viewer_can_read_not_write(monkeypatch):
    # (c) viewer 共享成员:读放行,写 ScopeDenied。活动 owner=20,actor=10(非 owner/team)。
    conn = _RoutingConn(
        event_row={"owner_id": 20, "team_ids": json.dumps([20])},
        member_row={"role": "viewer"},
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_event_access("evt_B", staff(id=10))  # read OK
    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_B", staff(id=10), write=True)


def test_assert_event_access_shared_editor_can_write(monkeypatch):
    # (d) editor 共享成员:读写均放行。
    conn = _RoutingConn(
        event_row={"owner_id": 20, "team_ids": json.dumps([20])},
        member_row={"role": "editor"},
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_event_access("evt_B", staff(id=10))  # read OK
    scope.assert_event_access("evt_B", staff(id=10), write=True)  # write OK


def test_assert_event_access_non_member_with_write_cannot_edit_others_event(monkeypatch):
    # (e) 收口核心:非 owner/非 team/非共享成员的员工(即便持 vkpi:write)写别人的活动必 403。
    conn = _RoutingConn(
        event_row={"owner_id": 20, "team_ids": json.dumps([20])},
        member_row=None,
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_B", staff(id=99), write=True)
    # 读同样拒(他既不 own 也未被共享)。
    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_B", staff(id=99))


def test_assert_event_access_admin_keeps_full_write(monkeypatch):
    # 红线守护:admin(can_view_all,demo 用户)收紧后仍保持完全读写(不误伤)。
    conn = _RoutingConn(event_row={"owner_id": 20, "team_ids": json.dumps([20])}, member_row=None)
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_event_access("evt_B", staff(role="admin", is_owner=1), write=True)
    scope.assert_event_access("evt_B", staff(role="manager"), write=True)


def test_assert_event_access_owner_and_team_keep_full_write(monkeypatch):
    # 红线守护:owner 与 team 成员收紧后仍完全读写。
    conn = _RoutingConn(event_row={"owner_id": 10, "team_ids": json.dumps([10, 33])}, member_row=None)
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    # owner(actor=10)
    scope.assert_event_access("evt_A", staff(id=10), write=True)
    # team 成员(actor=33,在 team_ids 里)
    scope.assert_event_access("evt_A", staff(id=33), write=True)


def test_assert_event_access_missing_event_row_passes(monkeypatch):
    # 活动行不存在 → 放行(交由下游 CRUD 自然空操作,不泄露存在性;镜像项目侧)。
    conn = _RoutingConn(event_row=None, member_row=None)
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    # Authenticated dependencies attach the active workspace explicitly; the
    # missing business row must not be confused with a missing staff identity.
    scope.assert_event_access("evt_missing", staff(id=99, organization_id=1), write=True)


# ── (f)/(g) 公司公共活动接线(2026-06-14,ADDITIVE,迁移 133 is_public)──────────
# public 活动对所有员工「加宽读」;写仍严格 gate;admin 不变。
def test_list_events_includes_public_event_for_non_member():
    # (f) 把 B 的活动 evt_B 标记 is_public 后,非 owner/非 team/非共享的员工 A 的 list
    #     经 COALESCE(is_public,0)=1 分支纳入 evt_B(加宽生效,无需逐个共享)。
    conn = _seed_events_sqlite()
    conn.execute("UPDATE vkpi_events SET is_public = 1 WHERE id = 'evt_B'")
    conn.commit()
    visible = _visible_event_ids_sqlite(conn, staff(id=10))
    assert "evt_A" in visible
    assert "evt_B" in visible  # public 活动对所有员工可见


def test_assert_event_access_public_can_read_not_write(monkeypatch):
    # (g) 公共活动(is_public=1):非 owner/非 team/非成员的员工可「读」,不可「写」。
    conn = _RoutingConn(
        event_row={"owner_id": 20, "team_ids": json.dumps([20]), "is_public": 1},
        member_row=None,
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_event_access("evt_B", staff(id=99))  # read OK(公共可读)
    with pytest.raises(scope.ScopeDenied, match="event scope denied"):
        scope.assert_event_access("evt_B", staff(id=99), write=True)  # 写仍被拒(public 不放开写)


def test_assert_event_access_public_admin_unchanged(monkeypatch):
    # 红线守护:admin / manager 对 public 活动仍完全读写(加宽未误伤 can_view_all 路径)。
    conn = _RoutingConn(
        event_row={"owner_id": 20, "team_ids": json.dumps([20]), "is_public": 1},
        member_row=None,
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_event_access("evt_B", staff(role="admin", is_owner=1), write=True)
    scope.assert_event_access("evt_B", staff(role="manager"), write=True)
