from __future__ import annotations

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
        "(p.assigned_staff_id = ? OR p.created_by_staff_id = ?)",
        [15, 15],
    )
    assert scope.project_filter("", staff(id=15))[0] == "(assigned_staff_id = ? OR created_by_staff_id = ?)"
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


def test_assert_project_access_denies_out_of_scope_staff(monkeypatch):
    monkeypatch.setattr(scope, "get_conn", lambda: _FakeConn({"assigned_staff_id": 10, "created_by_staff_id": 20}))

    with pytest.raises(scope.ScopeDenied, match="project scope denied"):
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
