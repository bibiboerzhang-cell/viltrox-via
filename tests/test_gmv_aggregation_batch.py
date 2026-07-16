from __future__ import annotations

from app.domains.attribution import gmv_aggregation


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        compact = " ".join(str(sql).split())
        self.calls.append((compact, tuple(params)))
        return _Rows(
            [
                {"link_id": 1, "slug": "a", "clicks": 10, "orders": 2, "revenue_cents": 5000, "commission_cents": 500},
                {"link_id": 2, "slug": "b", "clicks": 0, "orders": 0, "revenue_cents": 0, "commission_cents": 0},
            ]
        )


def test_link_gmv_uses_one_set_aggregation_and_shapes_truthful_totals(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(gmv_aggregation, "get_conn", lambda: conn)
    monkeypatch.setattr(gmv_aggregation, "table_exists", lambda _name: True)
    monkeypatch.setattr(gmv_aggregation.scope, "can_view_all", lambda _staff: True)

    result = gmv_aggregation.aggregate_link_gmv(staff={"id": 1, "role": "admin"}, limit=500)

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "WITH click_agg AS" in sql
    assert "sales_agg AS" in sql
    assert "SELECT COUNT(*) FROM vkpi_link_clicks" not in sql
    assert params == (500,)
    assert result["items"][0]["conversion_rate"] == 0.2
    assert result["items"][1]["conversion_rate"] is None
    assert result["totals"] == {
        "links": 2,
        "clicks": 10,
        "orders": 2,
        "revenue_cents": 5000,
        "commission_cents": 500,
    }
