import json

from app.domains.dashboard import summary_roster


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _Cursor(self.row)


def test_postgres_roster_tabs_use_one_materialized_scan(monkeypatch):
    row = {
        "by_views": json.dumps([{"kol_id": 1, "view_count": 90}]),
        "by_activity": json.dumps([{"kol_id": 2, "value": 4}]),
        "by_recent": json.dumps([{"kol_id": 3, "view_count": 70}]),
        "by_engagement": json.dumps([{"kol_id": 4, "engagement_count": 6}]),
    }
    conn = _Conn(row)
    monkeypatch.setattr(summary_roster, "get_conn", lambda: conn)

    result = summary_roster._postgres_mover_rows("TRUE")

    assert [items[0]["kol_id"] for items in result] == [1, 2, 3, 4]
    assert len(conn.calls) == 1
    sql = conn.calls[0][0]
    assert "WITH base AS MATERIALIZED" in sql
    assert sql.count("jsonb_agg") == 4


def test_roster_tab_projection_preserves_public_contract(monkeypatch):
    monkeypatch.setattr(summary_roster, "is_postgres_runtime", lambda: True)
    monkeypatch.setattr(
        summary_roster,
        "_postgres_mover_rows",
        lambda _predicate: (
            [{"kol_id": 1, "kol_name": "A", "view_count": 90, "like_count": 3}],
            [{"kol_id": 2, "kol_name": "B", "value": 4}],
            [{"kol_id": 3, "kol_name": "C", "view_count": 70}],
            [{"kol_id": 4, "kol_name": "D", "engagement_count": 6}],
        ),
    )

    result = summary_roster.build_roster_movers_tabs("TRUE")

    assert list(result) == ["by_views", "by_activity", "by_recent", "by_engagement"]
    assert result["by_views"][0]["value"] == 90
    assert result["by_activity"][0] == {
        "kol_id": 2,
        "kol_name": "B",
        "handle": None,
        "profile_url": None,
        "platform": None,
        "value": 4,
    }
    assert result["by_recent"][0]["value"] == 70
    assert result["by_engagement"][0]["value"] == 6


def test_roster_tabs_keep_serial_fallback_for_sqlite(monkeypatch):
    monkeypatch.setattr(summary_roster, "is_postgres_runtime", lambda: False)
    monkeypatch.setattr(
        summary_roster,
        "_serial_mover_rows",
        lambda _predicate: ([], [], [], []),
    )
    monkeypatch.setattr(
        summary_roster,
        "_postgres_mover_rows",
        lambda _predicate: (_ for _ in ()).throw(AssertionError("postgres path not expected")),
    )

    assert summary_roster.build_roster_movers_tabs("TRUE") == {
        "by_views": [],
        "by_activity": [],
        "by_recent": [],
        "by_engagement": [],
    }
