from __future__ import annotations

import sqlite3
from typing import Iterator

import pytest

from app.domains.attribution import link_center, reconciliation, revenue as attribution_revenue
from app.domains.reports import reports
from app.domains.reports import export_jobs, report_appendices, weekly_generator
from app.domains.projects import workflow_detail
from app.domains.staff import decision_staff
from app.domains.staff import kpi_ledger


@pytest.fixture()
def truth_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_kpi_ledger (
            id INTEGER PRIMARY KEY,
            ledger_date TEXT NOT NULL,
            staff_id INTEGER,
            kol_id INTEGER,
            project_id INTEGER,
            metric_key TEXT NOT NULL,
            metric_value REAL NOT NULL,
            source_type TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            confidence TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        INSERT INTO vkpi_kpi_ledger VALUES
          (1, '2026-07-14', 7, NULL, NULL, 'revenue_cents', 12000,
           'shopify', 'current', 'confirmed', '{}', '2026-07-14T00:00:00Z'),
          (2, '2026-07-14', 7, NULL, NULL, 'revenue_cents', 99000,
           'shopify', 'superseded', 'stale', '{}', '2026-07-13T00:00:00Z');

        CREATE TABLE vkpi_metric_values (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL,
            metric_key TEXT NOT NULL,
            value_numeric REAL,
            currency TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            source_count INTEGER NOT NULL DEFAULT 0,
            data_status TEXT,
            confidence REAL,
            is_partial INTEGER
        );
        INSERT INTO vkpi_metric_values VALUES
          (10, 5, 'gmv', NULL, 'USD', 'cents', 3, 'unavailable', 0, 1);

        CREATE TABLE vkpi_projects (
            id INTEGER PRIMARY KEY,
            project_name TEXT,
            stage_status TEXT
        );
        INSERT INTO vkpi_projects VALUES (21, 'Truth project', 'active');
        CREATE TABLE kols (id INTEGER PRIMARY KEY, channel_name TEXT);
        INSERT INTO kols VALUES (31, 'Truth KOL');
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO users VALUES (41, 'Truth staff');
        CREATE TABLE staff (id INTEGER PRIMARY KEY, user_id INTEGER);
        INSERT INTO staff VALUES (7, 41);
        CREATE TABLE vkpi_shopify_order_snapshots (
            id INTEGER PRIMARY KEY,
            shopify_order_id TEXT,
            order_name TEXT,
            order_number TEXT,
            processed_at TEXT,
            currency TEXT,
            subtotal_cents INTEGER,
            total_cents INTEGER,
            provider_auth_mode TEXT,
            provider_verified_at TEXT,
            raw_payload_hash TEXT,
            financial_status TEXT,
            cancelled_at TEXT,
            fulfillment_status TEXT,
            refund_status TEXT,
            landing_site TEXT
        );
        INSERT INTO vkpi_shopify_order_snapshots VALUES
          (51, 'order-51', '#51', '51', '2026-07-14T00:00:00Z', 'USD', 9000, 10000,
           'shopify-hmac', '2026-07-14T00:00:00Z', 'sha256:signed', 'paid', NULL, 'fulfilled', '', '/truth'),
          (52, 'order-52', '#52', '52', '2026-07-14T00:00:00Z', 'USD', 80000, 90000,
           'manual', NULL, '', 'paid', NULL, 'fulfilled', '', '/reference');
        CREATE TABLE vkpi_sales_attributions (
            id INTEGER PRIMARY KEY,
            source_platform TEXT,
            project_id INTEGER,
            link_id INTEGER,
            kol_id INTEGER,
            staff_id INTEGER,
            product_sku TEXT,
            revenue_cents INTEGER,
            commission_cents INTEGER,
            currency TEXT,
            attribution_model TEXT,
            confidence TEXT,
            occurred_at TEXT,
            imported_at TEXT,
            created_at TEXT,
            shopify_order_snapshot_id INTEGER
        );
        INSERT INTO vkpi_sales_attributions VALUES
          (61, 'shopify', 21, 81, 31, 7, 'SKU', 10000, 500, 'USD', 'last_touch',
           'confirmed', '2026-07-14T01:00:00Z', '2026-07-14T01:00:00Z', '2026-07-14T01:00:00Z', 51),
          (62, 'shopify', 21, 81, 31, 7, 'SKU', 90000, 4500, 'USD', 'last_touch',
           'confirmed', '2026-07-14T02:00:00Z', '2026-07-14T02:00:00Z', '2026-07-14T02:00:00Z', 52),
          (63, 'manual', 21, 81, 31, 7, 'SKU', 70000, 3500, 'USD', 'manual',
           'pending', '2026-07-14T03:00:00Z', '2026-07-14T03:00:00Z', '2026-07-14T03:00:00Z', NULL);
        CREATE TABLE vkpi_cost_ledger (
            id INTEGER PRIMARY KEY,
            project_id INTEGER,
            kol_id INTEGER,
            staff_id INTEGER,
            cost_type TEXT,
            amount_cents INTEGER,
            currency TEXT,
            status TEXT,
            incurred_at TEXT,
            created_by_staff_id INTEGER,
            created_at TEXT,
            approved_by_staff_id INTEGER,
            approved_at TEXT,
            voided_by_staff_id INTEGER,
            voided_at TEXT,
            updated_at TEXT
        );
        INSERT INTO vkpi_cost_ledger VALUES
          (71, 21, 31, 7, 'shipping', 1000, 'USD', 'actual', '2026-07-14T01:00:00Z',
           7, '2026-07-14T01:00:00Z', 7, '2026-07-14T01:05:00Z', NULL, NULL, '2026-07-14T01:05:00Z'),
          (72, 21, 31, 7, 'promotion', 8000, 'USD', 'estimated', '2026-07-14T02:00:00Z',
           7, '2026-07-14T02:00:00Z', NULL, NULL, NULL, NULL, '2026-07-14T02:00:00Z');
        """
    )
    try:
        yield conn
    finally:
        conn.close()


def test_ledger_source_reader_excludes_rows_marked_stale(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kpi_ledger, "get_conn", lambda: truth_conn)

    rows = kpi_ledger._ledger_source_query("2026-07-14")

    assert [row["id"] for row in rows] == [1]
    assert rows[0]["metric_value"] == 12000


def test_kpi_export_excludes_stale_materialization(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "get_conn", lambda: truth_conn)

    rows = export_jobs._rows("kpi_ledger", {}, staff=None)

    assert [row["id"] for row in rows] == [1]
    assert rows[0]["confidence"] == "confirmed"


def test_finance_export_only_contains_provider_verified_rows(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "get_conn", lambda: truth_conn)

    rows = export_jobs._rows("finance", {}, staff=None)

    assert [row["id"] for row in rows] == [61]
    assert rows[0]["business_truth_status"] == "provider_verified"
    assert rows[0]["revenue_cents"] == 10000


def test_attribution_export_keeps_reference_rows_but_masks_summable_money(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "get_conn", lambda: truth_conn)

    rows = export_jobs._rows("attribution", {}, staff=None)
    by_id = {row["id"]: row for row in rows}

    assert set(by_id) == {61, 62, 63}
    assert by_id[61]["business_truth_status"] == "provider_verified"
    assert by_id[62]["business_truth_status"] == "reference_only"
    assert by_id[62]["revenue_cents"] is None
    assert by_id[62]["reference_revenue_cents"] == 90000
    assert by_id[63]["revenue_cents"] is None


def test_cost_export_only_contains_approved_actual_rows(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(export_jobs, "get_conn", lambda: truth_conn)

    rows = export_jobs._rows("costs", {}, staff=None)

    assert [row["id"] for row in rows] == [71]
    assert rows[0]["business_truth_status"] == "approved_actual"


def test_link_order_summary_counts_only_provider_verified_money(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(link_center, "get_conn", lambda: truth_conn)
    monkeypatch.setattr(link_center, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(link_center.scope, "assert_link_access", lambda *_args, **_kwargs: None)

    payload = link_center.link_orders(81, staff={"id": 7, "role": "manager"})

    assert payload["summary"]["attribution_count"] == 3
    assert payload["summary"]["verified_attribution_count"] == 1
    assert payload["summary"]["reference_attribution_count"] == 2
    assert payload["summary"]["order_count"] == 1
    assert payload["summary"]["revenue_cents"] == 10000
    assert [row["business_truth_status"] for row in payload["sales_attributions"]] == [
        "reference_only",
        "reference_only",
        "provider_verified",
    ]


def test_amazon_summary_is_reference_only_and_not_summable_verified_gmv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows

        def fetchall(self) -> list[dict[str, object]]:
            return self.rows

        def fetchone(self) -> dict[str, object] | None:
            return self.rows[0] if self.rows else None

    class Conn:
        def execute(self, sql: str, _params: object = ()) -> Result:
            if "GROUP BY amazon_campaign_id" in sql:
                return Result([
                    {
                        "amazon_campaign_id": "campaign-1",
                        "product_sku": "SKU",
                        "currency": "USD",
                        "rows": 2,
                        "revenue_cents": 12000,
                        "commission_cents": 600,
                    }
                ])
            return Result([{"rows": 2, "revenue_cents": 12000, "commission_cents": 600}])

    conn = Conn()
    monkeypatch.setattr(attribution_revenue, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(attribution_revenue, "get_conn", lambda: conn)
    monkeypatch.setattr(
        attribution_revenue.scope,
        "row_staff_filter",
        lambda *_args, **_kwargs: ("", []),
    )

    payload = attribution_revenue.amazon_summary()

    assert payload["business_truth_status"] == "reference_only"
    assert payload["counts_toward_verified_gmv"] is False
    assert payload["totals"]["revenue_cents"] is None
    assert payload["totals"]["reference_revenue_cents"] == 12000
    assert payload["items"][0]["commission_cents"] is None
    assert payload["items"][0]["reference_commission_cents"] == 600


def test_reconciliation_money_is_labeled_reference_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def fetchone(self) -> dict[str, object]:
            return {"pending_count": 2, "pending_gmv_cents": 99000}

    class Conn:
        def execute(self, _sql: str, _params: object = ()) -> Result:
            return Result()

    monkeypatch.setattr(reconciliation, "ensure_vkpi_reconciliation_schema", lambda: None)
    monkeypatch.setattr(reconciliation, "get_conn", lambda: Conn())

    payload = reconciliation.stats()

    assert payload["pending_gmv_cents"] == 99000
    assert payload["business_truth_status"] == "reference_diagnostic"
    assert payload["counts_toward_verified_gmv"] is False


def test_staff_profile_retains_reference_costs_but_sums_only_approved_actual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_sql: list[str] = []

    def fake_rows(_conn: object, sql: str, _params: tuple[object, ...] = ()) -> list[dict[str, object]]:
        captured_sql.append(" ".join(sql.split()))
        if "FROM vkpi_cost_ledger c" in sql:
            return [
                {
                    "id": 1,
                    "amount_cents": 1000,
                    "status": "actual",
                    "approved_at": "2026-07-14T00:00:00Z",
                    "is_approved_actual": 1,
                },
                {
                    "id": 2,
                    "amount_cents": 9000,
                    "status": "estimated",
                    "approved_at": None,
                    "is_approved_actual": 0,
                },
            ]
        return []

    monkeypatch.setattr(decision_staff, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(decision_staff, "get_conn", lambda: object())
    monkeypatch.setattr(
        decision_staff,
        "staff_directory",
        lambda: {"staff": [{"staff_id": 7, "staff_name": "A"}]},
    )
    monkeypatch.setattr(decision_staff, "staff_kpi", lambda *_args, **_kwargs: {"rows": []})
    monkeypatch.setattr(decision_staff, "_safe_rows", fake_rows)
    monkeypatch.setattr(
        decision_staff,
        "_staff_kpi_breakdown",
        lambda *_args, **_kwargs: {"source_count": 0, "recommendation_source_rows": []},
    )
    monkeypatch.setattr(decision_staff.scope, "assert_staff_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(decision_staff.scope, "can_view_all", lambda *_args, **_kwargs: True)

    result = decision_staff.staff_profile(7, staff={"id": 7, "role": "manager"})

    assert [row["business_truth_status"] for row in result["costs"]] == [
        "approved_actual",
        "reference_only",
    ]
    assert result["summary"]["profile_cost_cents"] == 1000
    assert result["summary"]["approved_cost_count"] == 1
    assert any("c.status='actual' AND c.approved_at IS NOT NULL" in sql for sql in captured_sql)


def _stub_staff_kpi_base(monkeypatch: pytest.MonkeyPatch, fake_rows) -> None:
    monkeypatch.setattr(decision_staff, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(decision_staff, "get_conn", lambda: object())
    monkeypatch.setattr(
        decision_staff,
        "staff_directory",
        lambda: {
            "staff": [
                {
                    "staff_id": 7,
                    "staff_name": "A",
                    "email": "a@example.com",
                    "active": 1,
                }
            ]
        },
    )
    monkeypatch.setattr(decision_staff, "_safe_rows", fake_rows)


def test_staff_kpi_missing_financial_sources_stays_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_staff_kpi_base(monkeypatch, lambda *_args, **_kwargs: [])

    row = decision_staff.staff_kpi("month", staff_id=7)["rows"][0]

    assert row["gmv_cents"] is None
    assert row["cost_cents"] is None
    assert row["net_contribution_cents"] is None
    assert row["roi"] is None
    assert row["net_roi"] is None
    assert row["data_status"] == "awaiting_source"
    assert row["metric_statuses"] == {
        "gmv": "awaiting_source",
        "cost": "awaiting_source",
        "net_contribution": "awaiting_source",
        "roi": "awaiting_source",
    }


def test_staff_kpi_computes_financials_only_after_both_canonical_sources_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_rows(_conn: object, sql: str, _params: object = ()) -> list[dict[str, object]]:
        if "FROM vkpi_sales_attributions sa" in sql:
            return [{"staff_id": 7, "value": 10000, "source_count": 1}]
        if "FROM vkpi_cost_ledger c" in sql:
            return [{"staff_id": 7, "value": 2500, "source_count": 1}]
        return []

    _stub_staff_kpi_base(monkeypatch, fake_rows)

    row = decision_staff.staff_kpi("month", staff_id=7)["rows"][0]

    assert row["gmv_cents"] == 10000
    assert row["cost_cents"] == 2500
    assert row["net_contribution_cents"] == 7500
    assert row["roi"] == 4.0
    assert row["net_roi"] == 3.0
    assert row["data_status"] == "real"
    assert row["metric_statuses"] == {
        "gmv": "real",
        "cost": "real",
        "net_contribution": "real",
        "roi": "real",
    }


def test_project_detail_financial_headline_uses_only_canonical_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Result:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self.rows = rows

        def fetchone(self) -> dict[str, object] | None:
            return self.rows[0] if self.rows else None

        def fetchall(self) -> list[dict[str, object]]:
            return self.rows

    class Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql: str, _params: object = ()) -> Result:
            compact = " ".join(sql.split())
            self.sql.append(compact)
            if "FROM vkpi_projects p" in compact and "WHERE p.id=?" in compact:
                return Result([{"id": 21, "project_name": "P", "stage": "active"}])
            if "FROM vkpi_project_kol_assignments a" in compact:
                return Result([])
            if compact.startswith("SELECT * FROM vkpi_links"):
                return Result([{"id": 81, "click_count": 3, "valid_click_count": 2, "bot_click_count": 1}])
            if "FROM vkpi_link_clicks c" in compact:
                return Result([])
            if "sa.link_id IN" in compact:
                return Result([
                    {"attribution_id": 61, "source_ref": "verified", "revenue_cents": 10000, "is_verified_business_truth": 1},
                    {"attribution_id": 62, "source_ref": "reference", "revenue_cents": 90000, "is_verified_business_truth": 0},
                ])
            if "FROM vkpi_sales_attributions sa" in compact:
                return Result([
                    {"id": 61, "revenue_cents": 10000, "is_verified_business_truth": 1},
                    {"id": 62, "revenue_cents": 90000, "is_verified_business_truth": 0},
                ])
            if "FROM vkpi_cost_ledger c" in compact:
                return Result([
                    {"id": 71, "amount_cents": 1000, "cost_type": "shipping", "is_approved_actual": 1},
                    {"id": 72, "amount_cents": 8000, "cost_type": "promotion", "is_approved_actual": 0},
                ])
            return Result([])

    conn = Conn()
    monkeypatch.setattr(workflow_detail, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(workflow_detail, "ensure_vkpi_audit_schema", lambda: None)
    monkeypatch.setattr(workflow_detail, "get_conn", lambda: conn)
    monkeypatch.setattr(workflow_detail, "_enrich_project_card_fields", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_detail.scope, "assert_project_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workflow_detail.scope, "can_view_all", lambda *_args, **_kwargs: True)

    result = workflow_detail.project_detail(21, staff={"id": 7, "role": "manager"})

    assert result["roi"]["revenue_cents"] == 10000
    assert result["roi"]["cost_cents"] == 1000
    assert result["link_summary"]["revenue_cents"] == 10000
    assert result["link_summary"]["verified_attribution_count"] == 1
    assert result["sales_attributions"][1]["business_truth_status"] == "reference_only"
    assert result["costs"][1]["business_truth_status"] == "reference_only"
    assert any("provider_auth_mode='shopify-hmac'" in sql for sql in conn.sql)
    assert any("c.status='actual' AND c.approved_at IS NOT NULL" in sql for sql in conn.sql)


def test_unavailable_metric_appendix_hides_value_and_retained_sources(
    truth_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(report_appendices, "get_conn", lambda: truth_conn)
    monkeypatch.setattr(report_appendices, "ensure_vkpi_lineage_schema", lambda: None)

    appendix = report_appendices._source_appendix(5)

    assert appendix == [
        {
            "metric_key": "gmv",
            "metric_label": "本周销售额",
            "value": "未知",
            "raw_value": None,
            "data_status": "unavailable",
            "confidence": 0.0,
            "is_partial": True,
            "source_count": 0,
            "retained_source_count": 3,
            "rows": [],
        }
    ]


def test_invalidated_export_history_never_returns_download_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE vkpi_export_jobs (
            id INTEGER PRIMARY KEY,
            export_uid TEXT,
            requested_by_staff_id INTEGER,
            export_type TEXT,
            file_format TEXT,
            status TEXT,
            file_path TEXT,
            download_url TEXT,
            row_count INTEGER,
            triggered_at TEXT,
            completed_at TEXT,
            expires_at TEXT,
            error_message TEXT
        );
        INSERT INTO vkpi_export_jobs VALUES
          (9, 'export-9', 7, 'attribution', 'csv', 'invalidated',
           '/retained/audit/export-9.csv', '/api/admin/vkpi/exports/9/download',
           4, '2026-07-13T00:00:00Z', '2026-07-13T00:01:00Z', '2000-01-01T00:00:00Z',
           'truth_invalidated_by_migration_256');
        """
    )
    monkeypatch.setattr(export_jobs, "get_conn", lambda: conn)
    monkeypatch.setattr(export_jobs, "ensure_vkpi_reports_schema", lambda: None)
    removed: list[str] = []
    monkeypatch.setattr(
        export_jobs,
        "remove_stored_file",
        lambda path, **_kwargs: removed.append(str(path)),
    )
    monkeypatch.setattr(
        export_jobs.scope,
        "assert_legacy_default_organization",
        lambda *_args, **_kwargs: 1,
    )

    result = export_jobs.list_exports(staff={"id": 7, "role": "manager"})

    assert result["count"] == 1
    assert result["exports"][0]["truth_invalidated"] is True
    assert result["exports"][0]["download_url"] == ""
    assert result["exports"][0]["downloadUrl"] == ""
    assert "file_path" not in result["exports"][0]
    assert removed == []
    conn.close()


def test_legacy_weekly_truth_invalidation_withholds_markdown_body() -> None:
    public = weekly_generator._public_report(
        {
            "id": 2,
            "status": "invalidated",
            "body_md": "# Prior financial claim\nGMV $99,000",
            "truth_invalidated_at": "2026-07-14T00:00:00Z",
            "truth_invalidation_reason": "pre_native_shopify_financial_truth",
            "truth_invalidation_migration": 256,
            "truth_restorable": False,
        },
        include_body=True,
    )

    assert public["status"] == "invalidated"
    assert public["truth_invalidated"] is True
    assert public["data_status"] == "unavailable"
    assert "body_md" not in public


def test_post_256_weekly_report_keeps_body_but_not_fake_real_status() -> None:
    public = weekly_generator._public_report(
        {
            "id": 3,
            "status": "draft",
            "body_md": "# Fresh report",
            "truth_invalidated_at": None,
            "truth_invalidation_reason": "",
            "truth_invalidation_migration": None,
            "truth_restorable": True,
        },
        include_body=True,
    )

    assert public["truth_invalidated"] is False
    assert public["data_status"] == "awaiting_source"
    assert public["is_partial"] is True
    assert public["body_md"] == "# Fresh report"


def test_weekly_public_report_requires_explicit_real_source_evidence() -> None:
    public = weekly_generator._public_report(
        {
            "id": 4,
            "status": "draft",
            "body_md": "# Verified-source report",
            "truth_invalidated_at": None,
            "source_data_status": "real",
            "source_count": 3,
            "source_is_partial": False,
        },
        include_body=True,
    )

    assert public["data_status"] == "real"
    assert public["is_partial"] is False
    assert public["body_md"] == "# Verified-source report"
