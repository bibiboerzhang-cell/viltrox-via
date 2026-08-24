import json

from app.domains.dashboard import summary as dashboard_summary
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


def test_evidence_metrics_reuses_main_scan_for_active_external_counts(monkeypatch):
    rows = [
        {
            "evidence_total": 20,
            "view_covered": 18,
            "total_views": 5_000,
            "total_engagement": 500,
            "window_evidence_count": 8,
            "window_total_views": 2_000,
            "window_total_engagement": 200,
            "active_kol_accounts": 4,
            "active_kol_evidence": 6,
            "active_media_accounts": 1,
            "active_media_evidence": 2,
            "last_refreshed_at": "2026-08-24T00:00:00Z",
        },
        {"active_accounts": 2, "signal_rows": 7, "snapshot_days": 30},
    ]

    class SequentialConn:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=None):
            self.calls.append((" ".join(str(sql).split()), params))
            return _Cursor(rows[len(self.calls) - 1])

    conn = SequentialConn()
    monkeypatch.setattr(dashboard_summary, "get_conn", lambda: conn)
    monkeypatch.setattr(dashboard_summary, "_build_roster_detail", lambda _counts: {})

    result = dashboard_summary._build_evidence_metrics_summary(
        window_days=1000,
        active_roster_by_scope={"all": 7, "kol": 4, "media": 1, "company": 2},
    )

    assert len(conn.calls) == 2
    evidence_sql = conn.calls[0][0]
    assert "active_kol_accounts" in evidence_sql
    assert "INTERVAL '90 days'" in evidence_sql
    assert "INTERVAL '1000 days'" in evidence_sql
    assert evidence_sql.count("pool_row_id IS NOT NULL") == 4
    assert "GROUP BY COALESCE(p.dashboard_account_type, 'kol')" not in evidence_sql
    assert result["active_30d_by_scope"] == {
        "all": 7,
        "kol": 4,
        "media": 1,
        "company": 2,
        "owned": 2,
        "window_days": 90,
        "basis": {
            "kol_media": "vkpi_kol_video_evidence.publish_date within window",
            "company": "vkpi_channel_post_metrics posted_at or positive deltas within window",
        },
        "evidence_count_by_scope": {"kol": 6, "media": 2},
        "company_signal_rows": 7,
        "company_snapshot_days": 30,
    }
