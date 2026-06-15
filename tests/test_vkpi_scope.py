from __future__ import annotations

import sqlite3

import pytest

from app.domains.access import scope


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _FakeResult(self.row)


class _RoutingConn:
    """Fake conn that returns project-row vs member-row by SQL table name.

    assert_project_access does two queries: vkpi_projects (project row) then
    vkpi_project_members (membership row). A single-row _FakeConn cannot model
    both, so this routes by which table the SQL hits.
    """

    def __init__(self, project_row, member_row):
        self.project_row = project_row
        self.member_row = member_row
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "vkpi_project_members" in sql:
            return _FakeResult(self.member_row)
        return _FakeResult(self.project_row)


def staff(**overrides):
    data = {"id": 10, "role": "employee", "is_owner": 0}
    data.update(overrides)
    return data


def test_actor_staff_id_accepts_common_identity_keys():
    assert scope.actor_staff_id({"id": 7}) == 7
    assert scope.actor_staff_id({"staff_id": "8"}) == 8
    assert scope.actor_staff_id({"user_id": 9}) == 9
    assert scope.actor_staff_id(None) == 0
    assert scope.actor_staff_id({"id": "bad"}) == 0


def test_can_view_all_roles_and_finance_domain():
    assert scope.can_view_all(staff(role="admin")) is True
    assert scope.can_view_all(staff(role="admin", permissions={"vkpi": "write"})) is False
    assert scope.can_view_all(staff(role="marketing-manager")) is True
    assert scope.can_view_all(staff(is_owner=1, role="employee")) is True
    assert scope.can_view_all(staff(role="finance")) is False
    assert scope.can_view_all(staff(role="finance"), domain="cost") is True
    assert scope.can_view_all(staff(role="employee")) is False


def test_effective_staff_id_reduces_employee_to_actor():
    assert scope.effective_staff_id(staff(id=11), requested_staff_id=99) == 11
    assert scope.effective_staff_id(staff(role="manager"), requested_staff_id=99) == 99
    assert scope.effective_staff_id(staff(role="manager"), requested_staff_id=None) is None
    assert scope.effective_staff_id(None, requested_staff_id=99) is None


def test_scope_context_exposes_reduction_decision():
    own = scope.scope_context(staff(id=12), requested_staff_id=99)
    assert own["actor_staff_id"] == 12
    assert own["requested_staff_id"] == 99
    assert own["effective_staff_id"] == 12
    assert own["scope_mode"] == "own"
    assert own["can_view_all"] is False

    requested = scope.scope_context(staff(role="manager"), requested_staff_id=99)
    assert requested["effective_staff_id"] == 99
    assert requested["scope_mode"] == "requested_staff"
    assert requested["can_view_all"] is True

    all_scope = scope.scope_context(staff(role="admin"), requested_staff_id=None)
    assert all_scope["effective_staff_id"] is None
    assert all_scope["scope_mode"] == "all"


def test_filter_builders_return_safe_where_clauses():
    assert scope.staff_filter("p.assigned_staff_id", staff(id=14)) == ("p.assigned_staff_id = ?", [14])
    assert scope.staff_filter("p.assigned_staff_id", staff(role="admin")) == ("", [])
    assert scope.project_filter("p", staff(id=15)) == (
        "(COALESCE(p.restricted, FALSE) = FALSE AND "
        "(p.assigned_staff_id = ? OR p.created_by_staff_id = ? OR "
        "p.id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id = ?)))",
        [15, 15, 15],
    )
    assert scope.project_filter("", staff(id=15))[0] == (
        "(COALESCE(restricted, FALSE) = FALSE AND "
        "(assigned_staff_id = ? OR created_by_staff_id = ? OR "
        "id IN (SELECT project_id FROM vkpi_project_members WHERE staff_id = ?)))"
    )
    assert scope.link_filter("l", staff(id=16)) == (
        "(l.staff_id = ? OR l.created_by_staff_id = ?)",
        [16, 16],
    )
    assert scope.row_staff_filter("c", staff(id=17), column="owner_id") == ("c.owner_id = ?", [17])


def test_assert_staff_access_denies_out_of_scope_staff():
    scope.assert_staff_access(10, staff(id=10))
    scope.assert_staff_access(99, staff(role="manager"))
    scope.assert_staff_access(None, staff(id=10))
    with pytest.raises(scope.ScopeDenied, match="staff scope denied"):
        scope.assert_staff_access(99, staff(id=10))


def test_assert_project_access_allows_assigned_or_created_staff(monkeypatch):
    conn = _FakeConn({"assigned_staff_id": 10, "created_by_staff_id": 20})
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_project_access(123, staff(id=10))
    scope.assert_project_access(123, staff(id=20))
    assert conn.calls[-1][1] == (123,)


def test_assert_project_access_denies_out_of_scope_staff_on_restricted(monkeypatch):
    # PV-3 对齐:受限项目仍只认 assigned/creator/全可见角色,越权员工必 403。
    # _RoutingConn:project 行 restricted=1,且 actor 非成员(member_row=None)→ 必拒。
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _RoutingConn(
            project_row={"assigned_staff_id": 10, "created_by_staff_id": 20, "restricted": 1},
            member_row=None,
        ),
    )

    with pytest.raises(scope.ScopeDenied, match="project scope denied"):
        scope.assert_project_access(123, staff(id=30))


def test_assert_project_access_allows_out_of_scope_staff_on_non_restricted(monkeypatch):
    # PV-3 对齐:非受限项目对员工读写放行(存量 75% 双归属 NULL,只开读会全盘 403)。
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _FakeConn({"assigned_staff_id": 10, "created_by_staff_id": 20, "restricted": 0}),
    )

    scope.assert_project_access(123, staff(id=30))


def test_assert_project_access_allows_missing_row_and_manager(monkeypatch):
    monkeypatch.setattr(scope, "get_conn", lambda: _FakeConn(None))

    scope.assert_project_access(404, staff(id=30))
    scope.assert_project_access(123, staff(role="manager"))


def test_assert_link_access_allows_direct_project_related_staff(monkeypatch):
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _FakeConn(
            {
                "staff_id": 10,
                "created_by_staff_id": 20,
                "assigned_staff_id": 30,
                "project_creator_id": 40,
            }
        ),
    )

    for actor in (10, 20, 30, 40):
        scope.assert_link_access(55, staff(id=actor))


def test_assert_link_access_denies_out_of_scope_staff(monkeypatch):
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: _FakeConn(
            {
                "staff_id": 10,
                "created_by_staff_id": 20,
                "assigned_staff_id": 30,
                "project_creator_id": 40,
            }
        ),
    )

    with pytest.raises(scope.ScopeDenied, match="link scope denied"):
        scope.assert_link_access(55, staff(id=99))


# ---------------------------------------------------------------------------
# 项目共享接线(2026-06-14,ADDITIVE)— project_filter 加宽 + member 读写区分
# ---------------------------------------------------------------------------


def _seed_share_sqlite():
    """真 in-memory sqlite,建 vkpi_projects + vkpi_project_members 验证 EXISTS/IN 子查询。

    A(staff 10)与 B(staff 20)各有一个项目;A 非 admin。
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE vkpi_projects (
            id INTEGER PRIMARY KEY,
            assigned_staff_id INTEGER,
            created_by_staff_id INTEGER,
            restricted INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE vkpi_project_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            staff_id INTEGER,
            role TEXT DEFAULT 'viewer'
        )
        """
    )
    # project 100 属 A,project 200 属 B(都非 restricted)。
    conn.execute("INSERT INTO vkpi_projects (id, assigned_staff_id, created_by_staff_id, restricted) VALUES (100, 10, 10, 0)")
    conn.execute("INSERT INTO vkpi_projects (id, assigned_staff_id, created_by_staff_id, restricted) VALUES (200, 20, 20, 0)")
    return conn


def _visible_project_ids(conn, actor_staff):
    where_sql, params = scope.project_filter("p", actor_staff)
    sql = "SELECT p.id FROM vkpi_projects p"
    if where_sql:
        sql += f" WHERE {where_sql}"
    return {row["id"] for row in conn.execute(sql, params).fetchall()}


def test_project_filter_excludes_other_employees_project():
    # (a) 员工 A 看不到 B 的项目(own-only 仍生效,未共享不暴露)。
    conn = _seed_share_sqlite()
    visible = _visible_project_ids(conn, staff(id=10))
    assert 100 in visible
    assert 200 not in visible


def test_project_filter_includes_shared_project_via_members():
    # (b) 把 B 的项目 200 显式共享给 A 后,A 的 project_filter 经 IN 子查询纳入 200。
    conn = _seed_share_sqlite()
    conn.execute("INSERT INTO vkpi_project_members (project_id, staff_id, role) VALUES (200, 10, 'viewer')")
    visible = _visible_project_ids(conn, staff(id=10))
    assert 100 in visible
    assert 200 in visible  # 加宽生效:共享项目可见
    # B 不因 A 的共享而看到 A 的项目(共享是单向、显式的)。
    assert _visible_project_ids(conn, staff(id=20)) == {200}


def test_assert_project_access_shared_viewer_can_read_not_write(monkeypatch):
    # (c) viewer 共享成员:读放行,写 ScopeDenied。
    conn = _RoutingConn(
        project_row={"assigned_staff_id": 20, "created_by_staff_id": 20, "restricted": 1},
        member_row={"role": "viewer"},
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_project_access(200, staff(id=10))  # read OK
    with pytest.raises(scope.ScopeDenied, match="project scope denied"):
        scope.assert_project_access(200, staff(id=10), write=True)


def test_assert_project_access_shared_editor_can_write(monkeypatch):
    # (d) editor 共享成员:读写均放行(即便项目 restricted —— member 路径先于 restricted 判定)。
    conn = _RoutingConn(
        project_row={"assigned_staff_id": 20, "created_by_staff_id": 20, "restricted": 1},
        member_row={"role": "editor"},
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_project_access(200, staff(id=10))  # read OK
    scope.assert_project_access(200, staff(id=10), write=True)  # write OK


def test_assert_project_access_non_member_restricted_still_denied(monkeypatch):
    # ADDITIVE 守护:非成员 + restricted 仍必拒(既有 own-only 收紧未被加宽破坏)。
    conn = _RoutingConn(
        project_row={"assigned_staff_id": 20, "created_by_staff_id": 20, "restricted": 1},
        member_row=None,
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied, match="project scope denied"):
        scope.assert_project_access(200, staff(id=10))
