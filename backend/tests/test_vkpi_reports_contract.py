from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.domains.reports import contracts
from app.domains.reports import reports


def _stub_weekly_sources(monkeypatch: pytest.MonkeyPatch, *, summary: dict) -> None:
    monkeypatch.setattr(reports, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(reports.decision_dashboard, "dashboard", lambda **_kwargs: {"summary": summary})
    monkeypatch.setattr(reports.decision_staff, "staff_kpi", lambda **_kwargs: {"rows": []})
    monkeypatch.setattr(reports.workflow, "list_projects", lambda **_kwargs: {"projects": []})
    monkeypatch.setattr(reports.attribution, "list_attributions", lambda **_kwargs: {"attributions": []})
    monkeypatch.setattr(reports.costs, "list_costs", lambda **_kwargs: {"costs": []})
    monkeypatch.setattr(reports.alerts, "list_alerts", lambda **_kwargs: {"alerts": []})
    monkeypatch.setattr(reports, "_kpi_source_appendix", lambda *_args, **_kwargs: {})


def _metric(context: dict, key: str) -> dict:
    return next(item for item in context["kpis"] if item["key"] == key)


def test_report_metric_contract_keeps_real_zero_distinct_from_unknown() -> None:
    metric = contracts.WEEKLY_REPORT_SPEC.metric("views")

    zero = contracts.ReportMetricValue(metric, 0, contracts.DataStatus.REAL, source_count=0)
    unknown = contracts.ReportMetricValue(metric, None, contracts.DataStatus.AWAITING_SOURCE)

    assert zero.as_dict()["value"] == 0
    assert zero.as_dict()["data_status"] == "real"
    assert unknown.as_dict()["value"] is None
    assert unknown.as_dict()["data_status"] == "awaiting_source"
    with pytest.raises(ValueError, match="must be None"):
        contracts.ReportMetricValue(metric, 0, contracts.DataStatus.AWAITING_SOURCE)
    with pytest.raises(ValueError, match="must not be None"):
        contracts.ReportMetricValue(metric, None, contracts.DataStatus.REAL)


def test_weekly_context_marks_missing_views_unknown_but_empty_queried_totals_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_weekly_sources(monkeypatch, summary={})

    context = reports.build_weekly_context(staff={"id": 1, "role": "manager"})

    views = _metric(context, "views")
    sales = _metric(context, "sales_cents")
    assert views["raw_value"] is None
    assert views["data_status"] == "awaiting_source"
    assert views["value"] == "未知"
    assert sales["raw_value"] == 0
    assert sales["data_status"] == "real"
    assert context["totals"]["views"] is None
    assert context["data_status"] == "partial"
    assert context["report_spec"]["schema_version"] == contracts.REPORT_SCHEMA_VERSION


def test_weekly_context_preserves_explicit_zero_views(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_weekly_sources(monkeypatch, summary={"total_views": 0})

    context = reports.build_weekly_context(staff={"id": 1, "role": "manager"})

    views = _metric(context, "views")
    assert views["raw_value"] == 0
    assert views["value"] == "0"
    assert views["data_status"] == "real"
    assert context["totals"]["views"] == 0
    assert context["data_status"] == "real"


def test_report_request_normalizes_legacy_aliases_and_derives_inclusive_range() -> None:
    result = contracts.sanitize_report_filters(
        {
            "staffId": "9",
            "startDate": "2026-07-01",
            "period_days": 7,
        }
    )

    assert result == {
        "report_type": "weekly",
        "period": "weekly",
        "period_days": 7,
        "date": "2026-07-07",
        "date_from": "2026-07-01",
        "date_to": "2026-07-07",
        "language": "zh",
        "sections": list(contracts.REPORT_SECTION_KEYS),
        "format": "visual",
        "staff_id": 9,
    }


def test_report_request_round_trips_frontend_contract() -> None:
    payload = {
        "report_type": "monthly",
        "period": "monthly",
        "period_days": 30,
        "date": "2026-07-13",
        "date_from": "2026-06-14",
        "date_to": "2026-07-13",
        "language": "en",
        "sections": ["kpiOverview", "projects", "summary"],
        "format": "markdown",
        "scope": "self",
    }

    assert contracts.sanitize_report_filters(payload) == payload
    spec = contracts.report_spec_for("monthly").as_dict(language="en")
    assert spec["report_type"] == "monthly"
    assert spec["title"] == "Viltrox Marketing Monthly Report"
    assert spec["request_contract"]["sections"] == list(contracts.REPORT_SECTION_KEYS)


@pytest.mark.parametrize(
    ("payload", "code", "field"),
    [
        ({"language": "fr"}, "report_request_unsupported_value", "language"),
        ({"format": "html"}, "report_request_unsupported_value", "format"),
        ({"scope": "team"}, "report_request_unsupported_value", "scope"),
        ({"sections": []}, "report_request_invalid_sections", "sections"),
        ({"sections": ["summary", "rawSql"]}, "report_request_unsupported_value", "sections"),
        ({"period_days": 367}, "report_request_invalid_integer", "period_days"),
        ({"access_token": "secret"}, "report_request_unsupported_field", "access_token"),
        (
            {"date_from": "2026-07-13", "date_to": "2026-07-01"},
            "report_request_invalid_range",
            "date_from",
        ),
        (
            {"period_days": 7, "date_from": "2026-07-01", "date_to": "2026-07-08"},
            "report_request_period_mismatch",
            "period_days",
        ),
    ],
)
def test_report_request_rejects_unsupported_values_with_stable_errors(
    payload: dict[str, Any],
    code: str,
    field: str,
) -> None:
    with pytest.raises(contracts.ReportContractError) as raised:
        contracts.sanitize_report_filters(payload)

    assert raised.value.as_detail()["code"] == code
    assert raised.value.as_detail()["field"] == field


def _frontend_payload(*, period: str = "weekly", report_format: str = "visual") -> dict[str, Any]:
    period_days = 30 if period == "monthly" else 7
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=period_days - 1)
    return {
        "report_type": period,
        "period": period,
        "period_days": period_days,
        "date": end.isoformat(),
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "language": "en",
        "sections": ["kpiOverview", "summary"],
        "format": report_format,
        "scope": "self",
    }


def test_weekly_context_honors_window_language_sections_and_self_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _frontend_payload()
    start = datetime.fromisoformat(payload["date_from"]).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(payload["date_to"]).replace(tzinfo=timezone.utc)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(reports, "ensure_vkpi_schema", lambda: None)
    monkeypatch.setattr(
        reports.decision_dashboard,
        "dashboard",
        lambda **_kwargs: pytest.fail("scope=self must not use the all-staff dashboard"),
    )

    def _staff_kpi(**kwargs: Any) -> dict[str, Any]:
        captured["staff_kpi"] = kwargs
        return {
            "rows": [
                {
                    "staff_id": 7,
                    "staff_name": "A",
                    "content_views": 0,
                    "kol_claims": 1,
                    "published": 2,
                }
            ]
        }

    def _projects(**kwargs: Any) -> dict[str, Any]:
        captured["projects"] = kwargs
        return {"projects": [{"id": 11, "project_name": "P", "stage": "active"}]}

    def _attributions(**kwargs: Any) -> dict[str, Any]:
        captured["attributions"] = kwargs
        return {
            "attributions": [
                {
                    "project_id": 11,
                    "revenue_cents": 2500,
                    "occurred_at": start.isoformat(),
                    "is_verified_business_truth": 1,
                },
                {
                    "project_id": 11,
                    "revenue_cents": 9999,
                    "occurred_at": (start - timedelta(days=1)).isoformat(),
                    "is_verified_business_truth": 1,
                },
                {
                    "project_id": 11,
                    "revenue_cents": 125000,
                    "occurred_at": start.isoformat(),
                    "is_verified_business_truth": 0,
                    "business_truth_status": "reference_only",
                },
            ]
        }

    def _costs(**kwargs: Any) -> dict[str, Any]:
        captured["costs"] = kwargs
        return {
            "costs": [
                {
                    "project_id": 11,
                    "amount_cents": 500,
                    "status": "approved",
                    "incurred_at": end.replace(hour=12).isoformat(),
                    "is_approved_actual": 1,
                },
                {
                    "project_id": 11,
                    "amount_cents": 900,
                    "status": "approved",
                    "incurred_at": (end + timedelta(days=1)).isoformat(),
                    "is_approved_actual": 1,
                },
                {
                    "project_id": 11,
                    "amount_cents": 65000,
                    "status": "estimated",
                    "incurred_at": end.replace(hour=12).isoformat(),
                    "is_approved_actual": 0,
                    "business_truth_status": "reference_only",
                },
            ]
        }

    monkeypatch.setattr(reports.decision_staff, "staff_kpi", _staff_kpi)
    monkeypatch.setattr(reports.workflow, "list_projects", _projects)
    monkeypatch.setattr(reports.attribution, "list_attributions", _attributions)
    monkeypatch.setattr(reports.costs, "list_costs", _costs)
    monkeypatch.setattr(
        reports.alerts,
        "list_alerts",
        lambda **_kwargs: {"alerts": [{"title": "hidden risk"}]},
    )

    context = reports.build_weekly_context(
        staff={"id": 7, "role": "manager"},
        filters=payload,
        report_uid="weekly-contract",
    )

    assert context["request"] == payload
    assert context["period_start"] == f"{payload['date_from']}T00:00:00Z"
    assert context["period_end"] == f"{payload['date_to']}T23:59:59Z"
    assert context["scope"] == "staff"
    assert context["scope_id"] == 7
    assert context["title"] == "Viltrox Marketing Weekly Report"
    assert context["totals"]["sales_cents"] == 2500
    assert context["totals"]["cost_cents"] == 500
    assert _metric(context, "views")["raw_value"] == 0
    assert _metric(context, "views")["label"] == "Views"
    assert context["projects"] == []
    assert context["alerts"] == []
    assert context["kpi_appendix"] == {}
    assert captured["staff_kpi"] == {"window": "7d", "staff_id": 7}
    assert captured["projects"]["staff_id_filter"] == 7
    assert captured["attributions"]["staff_id"] == 7
    assert captured["costs"]["staff_id"] == 7


def test_historical_window_does_not_relabel_current_rolling_totals_as_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_weekly_sources(monkeypatch, summary={"total_views": 999})
    end = datetime.now(timezone.utc).date() - timedelta(days=2)
    start = end - timedelta(days=6)
    payload = {
        "period_days": 7,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "sections": ["kpiOverview"],
    }
    monkeypatch.setattr(
        reports.decision_dashboard,
        "dashboard",
        lambda **_kwargs: pytest.fail("historical reports must not use a current dashboard rollup"),
    )
    monkeypatch.setattr(
        reports.decision_staff,
        "staff_kpi",
        lambda **_kwargs: pytest.fail("historical reports must not use a current staff rollup"),
    )

    context = reports.build_weekly_context(
        staff={"id": 7, "role": "manager"},
        filters=payload,
    )

    assert _metric(context, "views")["raw_value"] is None
    assert _metric(context, "views")["data_status"] == "awaiting_source"
    assert _metric(context, "new_kol")["raw_value"] is None
    assert _metric(context, "published_content")["raw_value"] is None
    assert _metric(context, "sales_cents")["raw_value"] == 0
    assert context["data_status"] == "partial"


class _Result:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.row = row
        self.rowcount = 1

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


class _GenerateConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.commits = 0

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Result:
        compact = " ".join(sql.split())
        self.calls.append((compact, tuple(params)))
        if compact.startswith("SELECT id FROM vkpi_report_runs"):
            return _Result({"id": 88})
        return _Result()

    def commit(self) -> None:
        self.commits += 1


def test_generate_persists_and_returns_normalized_request_and_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _frontend_payload(period="monthly", report_format="markdown")
    conn = _GenerateConn()
    stored: dict[str, Any] = {}
    stored_calls: list[dict[str, Any]] = []
    _stub_weekly_sources(monkeypatch, summary={})
    monkeypatch.setattr(reports, "ensure_vkpi_reports_schema", lambda: None)
    monkeypatch.setattr(reports, "get_conn", lambda: conn)
    monkeypatch.setattr(reports, "_generate_ai_summary", lambda *_args, **_kwargs: "")

    def _store(content: bytes, *, filename: str) -> dict[str, Any]:
        stored.update({"content": content, "filename": filename})
        item = {
            "file_path": f"/safe/{filename}",
            "file_size_bytes": len(content),
            "sha256_hex": hashlib.sha256(content).hexdigest(),
        }
        stored_calls.append({**item, "content": content, "filename": filename})
        return item

    monkeypatch.setattr(reports.pdf_renderer, "store_bytes", _store)
    monkeypatch.setattr(
        reports.pdf_renderer,
        "render_and_store_pdf",
        lambda _context, *, filename: {
            "file_path": f"/safe/{filename}",
            "file_size_bytes": 10,
            "sha256_hex": hashlib.sha256(b"0123456789").hexdigest(),
            "html": "<html></html>",
        },
    )

    result = reports.generate_weekly_report(
        staff={"id": 7, "role": "manager", "organization_id": 1},
        filters=payload,
        render_pdf=True,
    )

    run_params = next(
        params for sql, params in conn.calls if sql.startswith("INSERT INTO vkpi_report_runs")
    )
    file_params = next(
        params for sql, params in conn.calls if sql.startswith("INSERT INTO vkpi_report_files")
    )
    metadata = json.loads(str(run_params[11]))
    assert run_params[1] == "monthly"
    assert run_params[2] == f"{payload['date_from']}T00:00:00Z"
    assert run_params[3] == f"{payload['date_to']}T23:59:59Z"
    assert run_params[4:6] == ("staff", 7)
    assert {key: metadata[key] for key in payload} == payload
    assert metadata["_report_contract"]["request"] == payload
    assert metadata["_report_contract"]["effective_staff_id"] == 7
    assert file_params[1] == "markdown"
    assert [params[1] for sql, params in conn.calls if sql.startswith("INSERT INTO vkpi_report_files")] == [
        "markdown",
        "pdf",
    ]
    markdown_call = next(item for item in stored_calls if item["filename"].endswith(".md"))
    assert markdown_call["filename"].endswith(".md")
    markdown = markdown_call["content"].decode("utf-8")
    assert "# Viltrox Marketing Monthly Report" in markdown
    assert "## Core metrics" in markdown
    assert "## Projects" not in markdown
    assert result["request"] == payload
    assert result["context"]["request"] == payload
    assert result["report_type"] == "monthly"
    assert result["download_url"].endswith("?format=markdown")


def _route_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routers import vkpi_reports

    app = FastAPI()
    app.include_router(vkpi_reports.router)
    generate_route = next(
        route
        for route in app.routes
        if getattr(route, "path", "") == "/api/admin/vkpi/reports/weekly/generate"
    )
    manager_dependency = generate_route.dependant.dependencies[0].call
    app.dependency_overrides[manager_dependency] = lambda: {"id": 7, "role": "manager"}
    return TestClient(app, raise_server_exceptions=False), vkpi_reports


def test_generate_route_round_trips_contract_and_returns_stable_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, router_module = _route_client(monkeypatch)
    payload = _frontend_payload(report_format="markdown")

    def _generate(**kwargs: Any) -> dict[str, Any]:
        normalized = contracts.sanitize_report_filters(kwargs.get("filters"))
        reports._report_scope_id(normalized, kwargs.get("staff"))
        context = {
            "report_type": normalized["report_type"],
            "period_start": f"{normalized['date_from']}T00:00:00Z",
            "period_end": f"{normalized['date_to']}T23:59:59Z",
            "data_status": "real",
            "summary_text": "summary",
            "kpis": [],
            "source_appendix": [],
            "kpi_appendix": {},
            "request": normalized,
        }
        return {
            "report_run_id": 88,
            "report_uid": "weekly-88",
            "report_type": normalized["report_type"],
            "period_start": context["period_start"],
            "period_end": context["period_end"],
            "data_status": "real",
            "request": normalized,
            "status": "ready",
            "download_url": "/download?format=markdown",
            "summary_text": "summary",
            "context": context,
        }

    monkeypatch.setattr(router_module.reports, "generate_weekly_report", _generate)

    response = client.post("/api/admin/vkpi/reports/weekly/generate", json=payload)
    assert response.status_code == 200
    assert response.json()["request"] == payload
    assert response.json()["context"]["request"] == payload
    assert response.json()["report_type"] == "weekly"
    assert response.json()["report_run_id"] == 88

    invalid = client.post(
        "/api/admin/vkpi/reports/weekly/generate",
        json={**payload, "language": "fr"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == {
        "code": "report_request_unsupported_value",
        "field": "language",
        "message": "unsupported language: fr",
    }

    unsupported_field = client.post(
        "/api/admin/vkpi/reports/weekly/generate",
        json={**payload, "access_token": "must-not-persist"},
    )
    assert unsupported_field.status_code == 422
    assert unsupported_field.json()["detail"]["code"] == "report_request_unsupported_field"

    scope_conflict = client.post(
        "/api/admin/vkpi/reports/weekly/generate",
        json={**payload, "staff_id": 9},
    )
    assert scope_conflict.status_code == 422
    assert scope_conflict.json()["detail"]["code"] == "report_request_scope_conflict"
