from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.domains.reports import contracts, reports
from scripts.vkpi_engineering_health_collect import collect_complexity


# Complete canonical return values frozen from the pre-split implementation at
# HEAD 7c2c5837af71092a29b989b66d7b3d34dc3e4740.  The reviewed reports.py
# source had SHA-256 f01bbd49a2f635b0845e09a5811f72638318aebc5f67a5117d9ca1360dc440fe.
LEGACY_CONTEXT_SHA256 = {
    "all_current": "8fd0129eb65a17aca2cde33ce353e09e0183b2f66aafa8f4ef85325916b2f29e",
    "historical": "de62cabc322a124ec0773a8e7163ba6a82203b899f9cd847c63436dfa56a8c12",
    "staff_project": "ad1916fff347a84c4388cf2a2466e03fcb163659eac153201dd58cc4925602d8",
}
LEGACY_CONTEXT_KEY_ORDER = [
    "title",
    "report_type",
    "report_spec",
    "data_status",
    "metric_statuses",
    "report_uid",
    "period_label",
    "period_days",
    "period_start",
    "period_end",
    "generated_at",
    "watermark_user",
    "language",
    "format",
    "sections",
    "scope",
    "scope_id",
    "summary_text",
    "kpis",
    "funnel",
    "staff_rows",
    "projects",
    "alerts",
    "kpi_appendix",
    "metric_run_id",
    "filters",
    "request",
    "totals",
]


_ORIGINALS = {
    "metric_payload": reports._metric_payload,
    "parse_moment": reports._parse_moment,
    "period": reports._period,
    "report_spec_for": reports.report_spec_for,
    "sanitize_report_filters": reports.sanitize_report_filters,
}


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _record(events: list[str], name: str, value: Any) -> Any:
    events.append(name)
    return value


def _install_case(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> tuple[dict[str, Any], list[str]]:
    events: list[str] = []
    scoped_staff_id = 7 if kind == "staff_project" else None
    current_window = kind != "historical"

    monkeypatch.setattr(
        reports,
        "ensure_vkpi_schema",
        lambda: _record(events, "ensure", None),
    )
    monkeypatch.setattr(
        reports,
        "sanitize_report_filters",
        lambda raw: _record(
            events,
            "sanitize",
            _ORIGINALS["sanitize_report_filters"](raw),
        ),
    )
    monkeypatch.setattr(
        reports,
        "report_spec_for",
        lambda report_type: _record(
            events,
            f"spec:{report_type}",
            _ORIGINALS["report_spec_for"](report_type),
        ),
    )
    monkeypatch.setattr(
        reports,
        "_period",
        lambda days, **kwargs: _record(
            events,
            "period",
            _ORIGINALS["period"](days, **kwargs),
        ),
    )
    monkeypatch.setattr(
        reports,
        "_parse_moment",
        lambda value: _record(
            events,
            f"parse:{value}",
            _ORIGINALS["parse_moment"](value),
        ),
    )
    monkeypatch.setattr(
        reports,
        "_report_scope_id",
        lambda _filters, _staff: _record(events, "scope", scoped_staff_id),
    )
    monkeypatch.setattr(
        reports,
        "_is_current_report_date",
        lambda _value: _record(events, "current", current_window),
    )
    monkeypatch.setattr(
        reports,
        "_metric_payload",
        lambda metric, **kwargs: _record(
            events,
            f"metric:{metric.spec.key}",
            _ORIGINALS["metric_payload"](metric, **kwargs),
        ),
    )
    monkeypatch.setattr(
        reports,
        "_utcnow",
        lambda: _record(events, "utcnow", "2026-08-29T12:34:56Z"),
    )
    monkeypatch.setattr(
        reports,
        "_staff_name",
        lambda _staff: _record(events, "staff_name", "Actor Name"),
    )
    monkeypatch.setattr(
        reports,
        "_kpi_source_appendix",
        lambda *args, **kwargs: _record(
            events,
            "appendix",
            {
                "args": list(args),
                "staff": kwargs.get("scoped_staff_id"),
            },
        ),
    )
    monkeypatch.setattr(
        reports.scope,
        "assert_project_access",
        lambda project_id, _staff: _record(
            events, f"project_access:{project_id}", None
        ),
    )
    monkeypatch.setattr(
        reports.decision_dashboard,
        "dashboard",
        lambda **_kwargs: _record(
            events,
            "dashboard",
            {"summary": {"total_views": 1234}},
        ),
    )
    staff_rows = [
        {
            "staff_id": 7,
            "staff_name": "A",
            "content_views": 100,
            "kol_claims": 2,
            "published": 3,
            "gmv_cents": 2000,
            "cost_cents": 300,
            "active_projects": 1,
        },
        {
            "staff_id": 8,
            "name": "B",
            "content_views": None,
            "kol_claims": 1,
            "published": 4,
            "revenue_cents": 4000,
            "cost_cents": None,
            "project_count": 2,
        },
    ]
    monkeypatch.setattr(
        reports.decision_staff,
        "staff_kpi",
        lambda **_kwargs: _record(events, "staff_kpi", {"rows": staff_rows}),
    )
    projects = [
        {
            "id": 11,
            "project_name": "P1",
            "kol_name": "K1",
            "stage": "active",
            "staff_name": "A",
            "updated_at": "2026-08-28",
        },
        {
            "id": 12,
            "project_uid": "P2",
            "kol_id": 22,
            "stage": "closed",
            "assigned_staff_id": 8,
            "updated_at": "",
        },
        {"id": 13, "project_name": "P3", "stage": "released"},
    ]
    monkeypatch.setattr(
        reports.workflow,
        "list_projects",
        lambda **_kwargs: _record(events, "projects", {"projects": projects}),
    )
    attributions = [
        {
            "project_id": 11,
            "revenue_cents": 2500,
            "occurred_at": "2026-08-23T00:00:00Z",
            "is_verified_business_truth": 1,
        },
        {
            "project_id": 12,
            "revenue_cents": 1000,
            "occurred_at": "2026-08-29T23:59:59Z",
            "is_verified_business_truth": 1,
        },
        {
            "project_id": 11,
            "revenue_cents": 9999,
            "occurred_at": "2026-08-22T23:59:59Z",
            "is_verified_business_truth": 1,
        },
        {
            "project_id": 11,
            "revenue_cents": 7777,
            "occurred_at": "2026-08-24T00:00:00Z",
            "is_verified_business_truth": 0,
        },
    ]
    monkeypatch.setattr(
        reports.attribution,
        "list_attributions",
        lambda **_kwargs: _record(
            events, "attributions", {"attributions": attributions}
        ),
    )
    cost_rows = [
        {
            "project_id": 11,
            "amount_cents": 500,
            "incurred_at": "2026-08-24T00:00:00Z",
            "is_approved_actual": 1,
        },
        {
            "project_id": 12,
            "amount_cents": 700,
            "incurred_at": "2026-08-29T23:59:59Z",
            "is_approved_actual": 1,
        },
        {
            "project_id": 11,
            "amount_cents": 888,
            "incurred_at": "2026-08-30T00:00:00Z",
            "is_approved_actual": 1,
        },
        {
            "project_id": 11,
            "amount_cents": 999,
            "incurred_at": "2026-08-25T00:00:00Z",
            "is_approved_actual": 0,
        },
    ]
    monkeypatch.setattr(
        reports.costs,
        "list_costs",
        lambda **_kwargs: _record(events, "costs", {"costs": cost_rows}),
    )
    monkeypatch.setattr(
        reports.alerts,
        "list_alerts",
        lambda **_kwargs: _record(
            events,
            "alerts",
            {
                "alerts": [
                    {"title": "Risk A", "description": "Desc A"},
                    {"alert_type": "fallback", "message": "Desc B"},
                ]
            },
        ),
    )
    filters: dict[str, Any] = {
        "report_type": "weekly",
        "period": "weekly",
        "period_days": 7,
        "date": "2026-08-29",
        "date_from": "2026-08-23",
        "date_to": "2026-08-29",
        "language": "en",
        "sections": list(contracts.REPORT_SECTION_KEYS),
        "format": "visual",
        "scope": "all",
    }
    if kind == "staff_project":
        filters.update(
            {
                "language": "zh",
                "scope": "self",
                "staff_id": 7,
                "project_id": 11,
            }
        )
    if kind == "historical":
        filters.update(
            {
                "date": "2026-08-20",
                "date_from": "2026-08-14",
                "date_to": "2026-08-20",
                "sections": ["kpiOverview", "summary"],
            }
        )
    return filters, events


def _expected_calls(kind: str) -> list[str]:
    start = "2026-08-14" if kind == "historical" else "2026-08-23"
    end = "2026-08-20" if kind == "historical" else "2026-08-29"
    calls = [
        "ensure",
        "sanitize",
        "spec:weekly",
        "period",
        f"parse:{start}T00:00:00Z",
        f"parse:{end}T23:59:59Z",
        "scope",
        "current",
    ]
    if kind == "all_current":
        calls.append("dashboard")
    if kind != "historical":
        calls.append("staff_kpi")
    calls.extend(
        [
            "projects",
            "attributions",
            "parse:2026-08-23T00:00:00Z",
            "parse:2026-08-29T23:59:59Z",
            "parse:2026-08-22T23:59:59Z",
            "parse:2026-08-24T00:00:00Z",
            "costs",
            "parse:2026-08-24T00:00:00Z",
            "parse:2026-08-29T23:59:59Z",
            "parse:2026-08-30T00:00:00Z",
            "parse:2026-08-25T00:00:00Z",
            "alerts",
        ]
    )
    if kind == "staff_project":
        calls.append("project_access:11")
    calls.extend(
        [
            "metric:views",
            "metric:sales_cents",
            "metric:cost_cents",
            "metric:new_kol",
            "metric:published_content",
            "metric:active_projects",
            "utcnow",
            "staff_name",
        ]
    )
    if kind != "historical":
        calls.append("appendix")
    return calls


@pytest.mark.parametrize("kind", ["all_current", "staff_project", "historical"])
def test_complete_weekly_context_and_call_order_match_legacy(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    filters, events = _install_case(monkeypatch, kind)

    result = reports.build_weekly_context(
        period_days=7,
        staff={"id": 7, "name": "Actor", "role": "manager"},
        filters=filters,
        report_uid="weekly-fixed",
    )

    assert _digest(result) == LEGACY_CONTEXT_SHA256[kind]
    assert list(result) == LEGACY_CONTEXT_KEY_ORDER
    assert list(result["metric_statuses"]) == [
        "views",
        "sales_cents",
        "cost_cents",
        "new_kol",
        "published_content",
        "active_projects",
    ]
    assert list(result["totals"]) == [
        "sales_cents",
        "cost_cents",
        "views",
        "new_kol",
        "published",
        "active_projects",
    ]
    if result["kpis"]:
        assert list(result["kpis"][0]) == [
            "key",
            "label",
            "value_type",
            "unit",
            "data_status",
            "source_count",
            "note",
            "value",
            "raw_value",
        ]
    if result["projects"]:
        assert list(result["projects"][0]) == [
            "project_name",
            "kol_name",
            "stage",
            "staff_name",
            "sales",
            "cost",
            "updated_at",
        ]
    assert events == _expected_calls(kind)


def test_source_shape_failures_remain_visible_after_schema_and_request_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters, events = _install_case(monkeypatch, "all_current")
    monkeypatch.setattr(
        reports.decision_dashboard,
        "dashboard",
        lambda **_kwargs: _record(events, "dashboard", None),
    )

    with pytest.raises(AttributeError):
        reports.build_weekly_context(
            period_days=7,
            staff={"id": 7, "role": "manager"},
            filters=filters,
        )

    assert events == [
        "ensure",
        "sanitize",
        "spec:weekly",
        "period",
        "parse:2026-08-23T00:00:00Z",
        "parse:2026-08-29T23:59:59Z",
        "scope",
        "current",
        "dashboard",
    ]


def test_weekly_context_family_complexity_size_and_dependency_are_bounded() -> None:
    paths = [
        Path("backend/app/domains/reports/reports.py"),
        Path("backend/app/domains/reports/weekly_context.py"),
    ]
    trees = {str(path): ast.parse(path.read_text(encoding="utf-8")) for path in paths}
    rows = collect_complexity(trees)
    targets = {
        row.qualified_name: row
        for row in rows
        if row.qualified_name in {"build_weekly_context", "build_weekly_context_impl"}
    }

    assert set(targets) == {"build_weekly_context", "build_weekly_context_impl"}
    assert targets["build_weekly_context"].cc <= 10
    assert targets["build_weekly_context_impl"].cc <= 10
    assert max(row.cc for row in rows) < 50
    assert all(len(path.read_text(encoding="utf-8").splitlines()) < 800 for path in paths)

    leaf_imports = {
        node.module or ""
        for node in ast.walk(trees[str(paths[1])])
        if isinstance(node, ast.ImportFrom)
    }
    assert "app.domains.reports.reports" not in leaf_imports
