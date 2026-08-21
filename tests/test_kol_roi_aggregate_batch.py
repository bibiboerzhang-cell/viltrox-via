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
        if "COUNT(DISTINCT pka.project_id) AS projects" in compact:
            return _Rows([{"kol_pool_id": 1, "projects": 3}, {"kol_pool_id": 2, "projects": 2}])
        if "WITH project_cardinality AS" in compact:
            return _Rows(
                [
                    {"kol_pool_id": 1, "total_projects": 3, "attributable_projects": 3},
                    {"kol_pool_id": 2, "total_projects": 2, "attributable_projects": 1},
                ]
            )
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

    result = roi_aggregate.list_high_value_kols(limit=2, staff={"id": 1, "role": "manager"})

    assert result["count"] == 2
    alpha = next(item for item in result["items"] if item["kol_pool_id"] == 1)
    assert alpha["roi"] == 2.0
    assert alpha["revenue_cents"] == 300
    assert alpha["recommendation_weight"] == 0.56
    beta = next(item for item in result["items"] if item["kol_pool_id"] == 2)
    assert beta["status"] == "unavailable"
    assert beta["roi"] is None
    assert beta["revenue_cents"] is None
    assert beta["attribution_coverage"]["ambiguous_projects"] == 1
    assert len(conn.queries) == 7


def test_single_kol_roi_fails_closed_when_any_project_has_multiple_assignments(monkeypatch):
    class _CoverageConn:
        def execute(self, sql, _params=()):
            compact = " ".join(str(sql).split())
            assert "COUNT(all_assignments.id) AS assignment_count" in compact
            return _Rows(
                [
                    {"project_id": 10, "assignment_count": 1},
                    {"project_id": 11, "assignment_count": 2},
                ]
            )

    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: _CoverageConn())
    monkeypatch.setattr(roi_aggregate, "table_exists", lambda _name: True)
    monkeypatch.setattr(roi_aggregate, "_kol_roi_accessible", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        roi_aggregate.metrics_agg,
        "_sum_cost",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ambiguous cost must not be read")),
    )
    monkeypatch.setattr(
        roi_aggregate.metrics_agg,
        "_sum_revenue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ambiguous revenue must not be read")),
    )

    result = roi_aggregate.get_kol_roi_summary(9, staff={"id": 1, "role": "manager"})

    assert result["status"] == "unavailable"
    assert result["unavailable_reason"] == "assignment_level_allocation_missing"
    assert result["cost_cents"] is None
    assert result["revenue_cents"] is None
    assert result["roi"] is None
    assert result["attribution_coverage"] == {
        "available": True,
        "total_projects": 2,
        "attributable_projects": 1,
        "ambiguous_projects": 1,
        "ratio": 0.5,
        "complete": False,
        "basis": "project_has_exactly_one_kol_assignment",
    }


def test_single_assignment_projects_keep_existing_roi_contract(monkeypatch):
    class _CoverageConn:
        def execute(self, _sql, _params=()):
            return _Rows([{"project_id": 10, "assignment_count": 1}])

    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: _CoverageConn())
    monkeypatch.setattr(roi_aggregate, "table_exists", lambda _name: True)
    monkeypatch.setattr(roi_aggregate, "_kol_roi_accessible", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(roi_aggregate.metrics_agg, "_sum_cost", lambda *_args, **_kwargs: 100)
    monkeypatch.setattr(
        roi_aggregate.metrics_agg,
        "_sum_revenue",
        lambda *_args, **_kwargs: {
            "revenue_cents": 300,
            "commission_cents": 30,
            "orders": 2,
            "currency": "USD",
        },
    )

    result = roi_aggregate.get_kol_roi_summary(9, staff={"id": 1, "role": "manager"})

    assert result["status"] == "ready"
    assert result["cost_cents"] == 100
    assert result["revenue_cents"] == 300
    assert result["roi"] == 2.0
    assert result["attribution_coverage"]["complete"] is True
