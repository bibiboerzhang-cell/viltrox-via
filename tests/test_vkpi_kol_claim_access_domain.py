import pytest

from app.domains.kol import claim_access
from app.domains.access import scope


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        return _Result(self.rows.pop(0) if self.rows else None)


def test_kol_claim_access_allows_manager_without_db(monkeypatch):
    monkeypatch.setattr(claim_access.scope, "can_view_all", lambda staff: True)
    monkeypatch.setattr(claim_access, "get_conn", lambda: pytest.fail("db should not be used"))

    claim_access.assert_kol_access(9, {"role": "manager"})


def test_kol_claim_access_allows_active_claim_owner(monkeypatch):
    conn = _Conn([
        {"assigned_staff_id": 4, "created_by_staff_id": 5},
        {"staff_id": 7},
    ])
    monkeypatch.setattr(claim_access, "get_conn", lambda: conn)

    claim_access.assert_kol_access(9, {"id": 7})
    assert len(conn.queries) == 2


def test_kol_claim_access_denies_other_active_claim(monkeypatch):
    conn = _Conn([
        {"assigned_staff_id": 4, "created_by_staff_id": 5},
        {"staff_id": 8},
    ])
    monkeypatch.setattr(claim_access, "get_conn", lambda: conn)

    with pytest.raises(scope.ScopeDenied):
        claim_access.assert_kol_access(9, {"id": 7})


def test_kol_claim_access_allows_unclaimed_when_requested(monkeypatch):
    conn = _Conn([
        {"assigned_staff_id": None, "created_by_staff_id": None},
        None,
    ])
    monkeypatch.setattr(claim_access, "get_conn", lambda: conn)

    claim_access.assert_kol_access(9, {"id": 7}, allow_unclaimed=True)
