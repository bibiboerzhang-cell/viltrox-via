from __future__ import annotations

from app.domains.dashboard import account_picker


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class _Connection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls: list[tuple[str, list[int]]] = []

    def execute(self, sql, params=()):
        self.calls.append((" ".join(str(sql).split()), list(params)))
        return _Rows(self.rows)


def test_active_roster_batch_scans_pool_once_and_sums_account_types(monkeypatch):
    conn = _Connection(
        [
            {"account_type": "kol", "active_roster": 7},
            {"account_type": "media", "active_roster": 3},
            {"account_type": "company", "active_roster": 2},
        ]
    )
    monkeypatch.setattr(account_picker, "get_conn", lambda: conn)

    result = account_picker._build_dashboard_active_roster_counts_impl()

    assert result == {"all": 12, "kol": 7, "media": 3, "company": 2}
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "FROM v_dashboard_account_pool" in sql
    assert "GROUP BY account_type" in sql
    assert "WHERE (account_type <> 'kol'" not in sql
    assert params == []


def test_active_roster_batch_preserves_employee_kol_scope(monkeypatch):
    conn = _Connection(
        [
            {"account_type": "kol", "active_roster": 4},
            {"account_type": "media", "active_roster": 2},
            {"account_type": "company", "active_roster": 1},
        ]
    )
    monkeypatch.setattr(account_picker, "get_conn", lambda: conn)

    result = account_picker._build_dashboard_active_roster_counts_impl(
        staff_scope_id=17,
    )

    assert result == {"all": 7, "kol": 4, "media": 2, "company": 1}
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "WHERE (account_type <> 'kol' OR source_id IN" in sql
    assert params == [17, 17, 17, 17]


def test_active_roster_batch_cache_is_scope_partitioned(monkeypatch):
    stored: dict[str, dict[str, int]] = {}
    builds: list[int | None] = []

    monkeypatch.setattr(account_picker, "cache_get", lambda key: stored.get(key))
    monkeypatch.setattr(
        account_picker,
        "cache_set",
        lambda key, value, _ttl: stored.__setitem__(key, value),
    )
    monkeypatch.setattr(
        account_picker,
        "_build_dashboard_active_roster_counts_impl",
        lambda *, staff_scope_id=None: builds.append(staff_scope_id)
        or {"all": len(builds), "kol": 0, "media": 0, "company": 0},
    )

    first = account_picker.build_dashboard_active_roster_counts(staff_scope_id=7)
    same = account_picker.build_dashboard_active_roster_counts(staff_scope_id=7)
    other = account_picker.build_dashboard_active_roster_counts(staff_scope_id=8)
    owner = account_picker.build_dashboard_active_roster_counts(staff_scope_id=None)

    assert (first, same, other, owner) == (
        {"all": 1, "kol": 0, "media": 0, "company": 0},
        {"all": 1, "kol": 0, "media": 0, "company": 0},
        {"all": 2, "kol": 0, "media": 0, "company": 0},
        {"all": 3, "kol": 0, "media": 0, "company": 0},
    )
    assert builds == [7, 8, None]
    assert len(stored) == 3
