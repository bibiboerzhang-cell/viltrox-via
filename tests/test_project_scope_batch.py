from __future__ import annotations

import pytest

from app.domains.access import scope


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(str(sql).split()), tuple(params)))
        return _Rows(self.rows)


def test_batch_project_access_uses_one_query_and_preserves_read_paths(monkeypatch):
    conn = _Conn(
        [
            {"id": 1, "assigned_staff_id": 7, "created_by_staff_id": None, "restricted": True, "is_public": False, "member_role": None},
            {"id": 2, "assigned_staff_id": 99, "created_by_staff_id": None, "restricted": False, "is_public": True, "member_role": None},
            {"id": 3, "assigned_staff_id": 99, "created_by_staff_id": None, "restricted": True, "is_public": False, "member_role": "viewer"},
            {"id": 4, "assigned_staff_id": None, "created_by_staff_id": None, "restricted": False, "is_public": False, "member_role": None},
        ]
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    scope.assert_project_access_many([1, 2, 3, 4, 4], {"id": 7, "role": "staff"})

    assert len(conn.calls) == 1
    assert conn.calls[0][1] == (7, 1, 2, 3, 4)


def test_batch_project_access_denies_unrelated_owned_project(monkeypatch):
    conn = _Conn(
        [
            {"id": 8, "assigned_staff_id": 99, "created_by_staff_id": 98, "restricted": False, "is_public": False, "member_role": None},
        ]
    )
    monkeypatch.setattr(scope, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied):
        scope.assert_project_access_many([8], {"id": 7, "role": "staff"})


def test_batch_project_access_short_circuits_manager_without_db(monkeypatch):
    monkeypatch.setattr(
        scope,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("manager batch must not read DB")),
    )

    scope.assert_project_access_many(range(1, 250), {"id": 1, "role": "admin"})
