from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_agents, vkpi_metrics, vkpi_performance_card
from app.domains.actions import inbox
from app.domains.metrics import aggregation
from app.domains.projects import pipeline_sequence
from app.domains.kol import recommendation_card, roi_aggregate, twin
from app.domains.memory import agent_memory_writer, provenance


def _scope_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY);
        CREATE TABLE vkpi_kol_pool_favorites (kol_pool_id INTEGER, staff_id INTEGER);
        CREATE TABLE vkpi_kol_pool_members (kol_pool_id INTEGER, staff_id INTEGER);
        CREATE TABLE vkpi_projects (
          id INTEGER PRIMARY KEY,
          assigned_staff_id INTEGER,
          created_by_staff_id INTEGER,
          restricted INTEGER DEFAULT 0,
          is_public INTEGER DEFAULT 0
        );
        CREATE TABLE vkpi_project_members (project_id INTEGER, staff_id INTEGER, role TEXT);
        CREATE TABLE vkpi_project_kol_assignments (id INTEGER PRIMARY KEY, project_id INTEGER, kol_pool_id INTEGER);
        """
    )
    conn.executemany("INSERT INTO vkpi_kol_pool(id) VALUES (?)", [(kid,) for kid in range(1, 10)])
    conn.execute("INSERT INTO vkpi_kol_pool_favorites VALUES (?, ?)", (1, 10))
    conn.execute("INSERT INTO vkpi_kol_pool_members VALUES (?, ?)", (2, 10))
    conn.executemany(
        "INSERT INTO vkpi_projects VALUES (?, ?, ?, ?, ?)",
        [
            (30, 10, 99, 0, 0),
            (40, 99, 99, 0, 0),
            (50, 99, 99, 0, 0),
            (60, 99, 99, 0, 1),
            (70, 99, 99, 1, 0),
            (80, 10, 99, 0, 0),
            (90, 99, 99, 0, 0),
        ],
    )
    conn.execute("INSERT INTO vkpi_project_members VALUES (?, ?, ?)", (40, 10, "viewer"))
    conn.executemany(
        "INSERT INTO vkpi_project_kol_assignments VALUES (?, ?, ?)",
        [
            (1, 30, 3),
            (2, 40, 4),
            (3, 50, 5),
            (4, 60, 6),
            (5, 70, 7),
            (6, 50, 1),
            (7, 80, 8),
            (8, 90, 8),
            (9, 90, 9),
        ],
    )
    return conn


def _high_value_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kol_pool (id INTEGER PRIMARY KEY, display_name TEXT, handle TEXT);
        CREATE TABLE vkpi_projects (
          id INTEGER PRIMARY KEY,
          assigned_staff_id INTEGER,
          created_by_staff_id INTEGER,
          restricted INTEGER DEFAULT 0,
          is_public INTEGER DEFAULT 0
        );
        CREATE TABLE vkpi_project_members (project_id INTEGER, staff_id INTEGER, role TEXT);
        CREATE TABLE vkpi_project_kol_assignments (
          id INTEGER PRIMARY KEY,
          project_id INTEGER,
          kol_pool_id INTEGER
        );
        CREATE TABLE vkpi_cost_ledger (
          project_id INTEGER,
          amount_cents INTEGER,
          status TEXT,
          approved_at TEXT
        );
        INSERT INTO vkpi_kol_pool VALUES (1, 'Visible KOL', 'visible');
        INSERT INTO vkpi_kol_pool VALUES (2, 'Hidden KOL', 'hidden');
        INSERT INTO vkpi_projects VALUES (10, 10, 99, 0, 0);
        INSERT INTO vkpi_projects VALUES (11, 99, 99, 0, 0);
        INSERT INTO vkpi_projects VALUES (12, 99, 99, 0, 0);
        INSERT INTO vkpi_project_kol_assignments VALUES (1, 10, 1);
        INSERT INTO vkpi_project_kol_assignments VALUES (2, 11, 1);
        INSERT INTO vkpi_project_kol_assignments VALUES (3, 12, 2);
        INSERT INTO vkpi_cost_ledger VALUES (10, 100, 'actual', '2026-08-20T00:00:00Z');
        INSERT INTO vkpi_cost_ledger VALUES (11, 900, 'actual', '2026-08-20T00:00:00Z');
        INSERT INTO vkpi_cost_ledger VALUES (12, 500, 'actual', '2026-08-20T00:00:00Z');
        """
    )
    return conn


def test_regular_staff_roi_scope_requires_visible_project_not_favorite_or_share(monkeypatch):
    conn = _scope_db()
    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: conn)
    staff = {"id": 10, "role": "staff"}

    assert roi_aggregate._kol_roi_accessible(1, staff) is False  # favorite, but only an invisible project
    assert roi_aggregate._kol_roi_accessible(2, staff) is False  # share alone is not financial access
    assert roi_aggregate._kol_roi_accessible(3, staff) is True  # assigned project
    assert roi_aggregate._kol_roi_accessible(4, staff) is True  # shared project
    assert roi_aggregate._kol_roi_accessible(6, staff) is True  # public project
    assert roi_aggregate._kol_roi_accessible(5, staff) is False  # another staff's private project
    assert roi_aggregate._kol_roi_accessible(7, staff) is False  # restricted project


def test_favorite_with_only_invisible_project_is_not_found_before_financial_reads(monkeypatch):
    conn = _scope_db()
    staff = {"id": 10, "role": "staff"}
    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: conn)
    monkeypatch.setattr(
        roi_aggregate,
        "_project_coverage_for_kol",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("coverage read after denial")),
    )

    result = roi_aggregate.get_kol_roi_summary(1, staff=staff)

    assert result == {"status": "not_found", "kol_pool_id": 1}


def test_roi_uses_only_visible_project_intersection_for_coverage_and_money(monkeypatch):
    conn = _scope_db()
    staff = {"id": 10, "role": "staff"}
    calls: list[tuple[str, str, list[int]]] = []
    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: conn)
    monkeypatch.setattr(roi_aggregate, "table_exists", lambda name: name == "vkpi_project_kol_assignments")

    def _cost(clause, params, **_kwargs):
        calls.append(("cost", clause, list(params)))
        return 100

    def _revenue(clause, params, **_kwargs):
        calls.append(("revenue", clause, list(params)))
        return {"revenue_cents": 300, "commission_cents": 30, "orders": 1, "currency": "USD"}

    monkeypatch.setattr(roi_aggregate.metrics_agg, "_sum_cost", _cost)
    monkeypatch.setattr(roi_aggregate.metrics_agg, "_sum_revenue", _revenue)

    result = roi_aggregate.get_kol_roi_summary(8, staff=staff)

    assert result["status"] == "ready"
    assert result["total_projects"] == 1
    assert result["attribution_coverage"]["complete"] is True
    assert result["attribution_coverage"]["ambiguous_projects"] == 0
    assert calls == [
        ("cost", "AND project_id IN (?)", [80]),
        ("revenue", "AND project_id IN (?)", [80]),
    ]


def test_manager_and_finance_use_existing_company_claim_scope(monkeypatch):
    conn = _scope_db()
    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: conn)

    assert roi_aggregate._kol_roi_accessible(5, {"id": 20, "role": "manager"}) is True
    assert roi_aggregate._kol_roi_accessible(5, {"id": 21, "role": "finance"}) is True
    assert roi_aggregate._kol_roi_accessible(999, {"id": 20, "role": "manager"}) is False
    assert roi_aggregate._kol_roi_accessible(1, None) is False


def test_workspace_digest_route_high_value_block_uses_staff_visible_projects_only(monkeypatch):
    conn = _high_value_db()
    actor = {"id": 10, "role": "staff"}
    present = {"vkpi_project_kol_assignments", "vkpi_kol_pool", "vkpi_cost_ledger"}
    monkeypatch.setattr(roi_aggregate, "get_conn", lambda: conn)
    monkeypatch.setattr(roi_aggregate, "table_exists", lambda name: name in present)
    monkeypatch.setattr(inbox, "list_inbox", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(inbox, "read_execution_ledger", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(pipeline_sequence, "pipeline_readiness", lambda **_kwargs: {"breakpoints": []})
    monkeypatch.setattr(aggregation, "aggregate_portfolio_metrics", lambda **_kwargs: {"status": "awaiting_m5"})

    result = vkpi_agents.workspace_digest(action_limit=5, staff=actor)

    assert result["high_value_kols"]["count"] == 1
    assert [item["kol_pool_id"] for item in result["high_value_kols"]["items"]] == [1]
    item = result["high_value_kols"]["items"][0]
    assert item["projects"] == 1
    assert item["cost_cents"] == 100
    assert item["recommendation_weight"] is None


def test_performance_card_route_favorite_does_not_grant_hidden_project_financials(monkeypatch):
    conn = _scope_db()
    actor = {"id": 10, "role": "staff"}
    monkeypatch.setattr("app.db.connection.get_conn", lambda: conn)

    with pytest.raises(HTTPException) as caught:
        vkpi_performance_card.get_kol_performance_card(1, staff=actor)

    assert caught.value.status_code == 404


def test_roi_summary_denial_fails_closed_before_assignment_or_ledger_reads(monkeypatch):
    monkeypatch.setattr(roi_aggregate, "_kol_roi_accessible", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        roi_aggregate,
        "_project_coverage_for_kol",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("assignment read after denial")),
    )

    result = roi_aggregate.get_kol_roi_summary(9, staff={"id": 10, "role": "staff"})

    assert result == {"status": "not_found", "kol_pool_id": 9}


def test_kol_metrics_route_returns_generic_404_and_skips_weight_on_denial(monkeypatch):
    monkeypatch.setattr(
        roi_aggregate,
        "get_kol_roi_summary",
        lambda *_args, **_kwargs: {"status": "not_found", "kol_pool_id": 9},
    )
    monkeypatch.setattr(
        roi_aggregate,
        "compute_next_recommendation_weight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("weight read after denial")),
    )

    with pytest.raises(HTTPException) as caught:
        vkpi_metrics.kol_roi_metrics(9, staff={"id": 10, "role": "staff"})

    assert caught.value.status_code == 404
    assert caught.value.detail == "KOL not found"


def test_provenance_forwards_staff_to_scoped_roi(monkeypatch):
    actor = {"id": 10, "role": "staff"}
    seen: list[dict] = []
    monkeypatch.setattr(provenance, "table_exists", lambda _name: False)

    def _roi(_kid, *, staff=None):
        seen.append(staff)
        return {"status": "no_projects", "total_projects": 0}

    monkeypatch.setattr(roi_aggregate, "get_kol_roi_summary", _roi)

    result = provenance.get_kol_provenance(9, staff=actor)

    assert result["status"] == "ok"
    assert seen == [actor]


def test_recommendation_card_and_twin_preserve_staff_on_nested_calls(monkeypatch):
    actor = {"id": 10, "role": "staff"}
    seen: list[tuple[str, dict]] = []

    class _PoolResult:
        def fetchone(self):
            return {
                "id": 9,
                "display_name": "Scoped Creator",
                "handle": "scoped",
                "platform": "youtube",
                "primary_topic": "camera",
                "followers": 5000,
                "engagement_rate": 0.04,
                "email": "",
                "other_contacts_json": "[]",
                "suspect_inflation": 0,
                "avatar_url": "",
            }

    class _PoolConn:
        def execute(self, _sql, _params=()):
            return _PoolResult()

    monkeypatch.setattr(recommendation_card, "table_exists", lambda _name: True)
    monkeypatch.setattr(recommendation_card, "get_conn", lambda: _PoolConn())

    def _provenance(_kid, *, staff=None, **_kwargs):
        seen.append(("provenance", staff))
        return {"status": "ok", "provenance": {}, "citations": []}

    monkeypatch.setattr(provenance, "get_kol_provenance", _provenance)
    recommendation_card.get_recommendation_card(9, staff=actor)
    assert seen == [("provenance", actor)]

    seen.clear()
    monkeypatch.setattr(
        recommendation_card,
        "get_recommendation_card",
        lambda _kid, *, staff=None: (
            seen.append(("card", staff))
            or {"status": "ok", "data_grade": "B", "signals": {}}
        ),
    )
    monkeypatch.setattr(provenance, "get_kol_provenance", _provenance)

    def _roi(_kid, *, staff=None):
        seen.append(("roi", staff))
        return {"status": "no_projects"}

    monkeypatch.setattr(roi_aggregate, "get_kol_roi_summary", _roi)
    monkeypatch.setattr(roi_aggregate, "compute_next_recommendation_weight", lambda _kid: None)
    monkeypatch.setattr(agent_memory_writer, "recent_outcome_stats", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(twin, "_enrichment_summary", lambda _kid: {})

    result = twin.get_kol_twin(9, staff=actor)

    assert result["status"] == "ok"
    assert seen == [("card", actor), ("provenance", actor), ("roi", actor)]
