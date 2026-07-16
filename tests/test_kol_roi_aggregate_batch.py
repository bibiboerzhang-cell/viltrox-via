from __future__ import annotations

from app.domains.kol import roi_aggregate


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self):
        self.queries: list[str] = []

    def execute(self, sql, _params=()):
        compact = " ".join(str(sql).split())
        self.queries.append(compact)
        if "COUNT(DISTINCT project_id) AS projects" in compact:
            return _Rows([{"kol_pool_id": 1, "projects": 3}, {"kol_pool_id": 2, "projects": 2}])
        if "COALESCE(display_name, handle" in compact:
            return _Rows([{"id": 1, "label": "Alpha"}, {"id": 2, "label": "Beta"}])
        if "SUM(c.amount_cents)" in compact:
            return _Rows([{"kol_pool_id": 1, "cost_cents": 100}])
        if "SUM(s.revenue_cents)" in compact:
            return _Rows(
                [
                    {
                        "kol_pool_id": 1,
                        "currency": "USD",
                        "revenue_cents": 300,
                        "commission_cents": 30,
                        "orders": 2,
                    }
                ]
            )
        if "FROM ranked WHERE rn <= 50" in compact:
            return _Rows([{"kol_pool_id": 1, "total": 2, "claimed": 2, "agreed": 1, "published": 1}])
        if "FROM ranked WHERE rn <= 20" in compact:
            return _Rows([{"entity_id": "1", "total": 2, "successes": 1, "failures": 1}])
        raise AssertionError(compact)


def test_high_value_leaderboard_uses_constant_batch_queries(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: conn)
    monkeypatch.setattr(roi_aggregate, "table_exists", lambda _name: True)
    monkeypatch.setattr(
        roi_aggregate,
        "get_kol_roi_summary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("per-KOL ROI read")),
    )
    monkeypatch.setattr(
        roi_aggregate,
        "compute_next_recommendation_weight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("per-KOL weight read")),
    )

    result = roi_aggregate.list_high_value_kols(limit=2)

    assert result["count"] == 2
    alpha = next(item for item in result["items"] if item["kol_pool_id"] == 1)
    assert alpha["roi"] == 2.0
    assert alpha["revenue_cents"] == 300
    assert alpha["recommendation_weight"] == 0.56
    assert len(conn.queries) == 6
