from __future__ import annotations

from typing import Any

from app.domains.staff import decision_staff


def test_staff_kpi_refactor_preserves_query_order_financial_truth_and_ledger_override(monkeypatch):
    conn = object()
    queries: list[tuple[str, tuple[Any, ...]]] = []
    ensured: list[bool] = []

    monkeypatch.setattr(decision_staff, "ensure_vkpi_schema", lambda: ensured.append(True))
    monkeypatch.setattr(decision_staff, "get_conn", lambda: conn)
    monkeypatch.setattr(decision_staff, "_window_start", lambda window: f"START:{window}")
    monkeypatch.setattr(decision_staff, "_day_bucket", lambda *_columns: "DAY_BUCKET")
    monkeypatch.setattr(decision_staff, "_active_project_filter", lambda alias: f"ACTIVE:{alias}")
    monkeypatch.setattr(
        decision_staff,
        "staff_directory",
        lambda: {
            "staff": [
                {
                    "staff_id": 7,
                    "staff_name": "Seven",
                    "email": "seven@example.test",
                    "employee_code": "E7",
                    "avatar_url": "avatar-7",
                    "role": "ops",
                    "active": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(
        decision_staff.business_truth,
        "verified_shopify_attribution_sql",
        lambda alias: f"VERIFIED:{alias}",
    )
    monkeypatch.setattr(
        decision_staff.business_truth,
        "approved_actual_cost_sql",
        lambda alias: f"APPROVED:{alias}",
    )
    monkeypatch.setattr(
        decision_staff.business_truth,
        "current_kpi_ledger_sql",
        lambda: "CURRENT",
    )

    def fake_rows(actual_conn, sql: str, params: tuple[Any, ...]):
        assert actual_conn is conn
        queries.append((sql, params))
        if "FROM vkpi_kol_claims" in sql:
            return [{"staff_id": 7, "value": 2}]
        if "FROM vkpi_projects WHERE created_at" in sql:
            return [{"staff_id": 7, "value": 1}]
        if "FROM vkpi_projects WHERE stage_status='active'" in sql:
            return [{"staff_id": 7, "value": 1}]
        if "FROM vkpi_project_stage_events" in sql:
            return [
                {"staff_id": 7, "to_stage": "replied", "value": 3},
                {"staff_id": 7, "to_stage": "ignored", "value": 99},
            ]
        if "FROM vkpi_links WHERE created_at" in sql:
            return [{"staff_id": 7, "value": 4}]
        if "FROM vkpi_link_clicks" in sql:
            return [{"staff_id": 7, "valid_clicks": 12, "bot_clicks": 2}]
        if "INNER JOIN kol_posts" in sql:
            return [{"staff_id": 7, "content_views": 800, "content_likes": 40}]
        if "FROM vkpi_sales_attributions" in sql:
            return [{"staff_id": 7, "value": 5000, "source_count": 1}]
        if "FROM vkpi_cost_ledger" in sql:
            return [{"staff_id": 7, "value": 2000, "source_count": 2}]
        if "FROM vkpi_kpi_ledger" in sql:
            return [
                {
                    "staff_id": 7,
                    "ledger_workload_score": 9.5,
                    "kpi_credit": 3,
                    "recommendation_source_rows": 4,
                    "recommendation_projects": 1,
                    "recommendation_published": 1,
                    "recommendation_orders": 2,
                    "recommendation_clicks": 12,
                    "recommendation_gmv_cents": 5000,
                    "recommendation_cost_cents": 2000,
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(decision_staff, "_safe_rows", fake_rows)

    result = decision_staff.staff_kpi("month", staff_id=7)
    row = result["rows"][0]

    assert ensured == [True]
    assert len(queries) == 10
    assert [params for _, params in queries] == [("START:month", 7)] * 10
    assert result["window"] == "month"
    assert result["start"] == "START:month"
    assert result["staff_id"] == 7
    assert row["kol_claims"] == 2
    assert row["projects"] == 1
    assert row["active_projects"] == 1
    assert row["replied"] == 3
    assert row["links_created"] == 4
    assert row["valid_clicks"] == 12
    assert row["bot_clicks"] == 2
    assert row["content_views"] == 800
    assert row["content_likes"] == 40
    assert row["gmv_cents"] == 5000
    assert row["cost_cents"] == 2000
    assert row["net_contribution_cents"] == 3000
    assert row["roi"] == 2.5
    assert row["net_roi"] == 1.5
    assert row["financial_data_status"] == "real"
    assert row["metric_statuses"] == {
        "gmv": "real",
        "cost": "real",
        "net_contribution": "real",
        "roi": "real",
    }
    assert row["legacy_workload_score"] == 10
    assert row["workload_score"] == 9.5
    assert row["recommendation_source_rows"] == 4
