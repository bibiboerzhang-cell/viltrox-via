import pytest

from app.domains.dashboard import summary as dashboard_summary


@pytest.fixture(autouse=True)
def _isolate_database_backed_summary_blocks(monkeypatch):
    """Keep these orchestration tests independent of production dashboard tables."""
    monkeypatch.setattr(
        dashboard_summary,
        "_cached_summary_block",
        lambda _name, _scope, builder, **_parts: builder(),
    )
    monkeypatch.setattr(dashboard_summary, "_build_evidence_metrics_summary", lambda **_kwargs: {})
    monkeypatch.setattr(dashboard_summary, "_build_active_campaigns_summary", lambda **_kwargs: {})
    monkeypatch.setattr(dashboard_summary, "_build_funnel_summary", lambda **_kwargs: {})
    monkeypatch.setattr(dashboard_summary, "_build_company_window_metrics", lambda: {})
    monkeypatch.setattr(dashboard_summary, "_build_company_metric_series", lambda **_kwargs: {})


def test_build_dashboard_summary_adds_lineage_and_official_summary(monkeypatch):
    monkeypatch.setattr(dashboard_summary.scope, "effective_staff_id", lambda staff, staff_id: None)
    monkeypatch.setattr(dashboard_summary, "resolve_staff_id", lambda staff: 7)
    monkeypatch.setattr(
        dashboard_summary,
        "build_dashboard_kpi",
        lambda **kwargs: {"active_roster": 12, "scope": kwargs.get("staff_scope_id")},
    )
    monkeypatch.setattr(
        dashboard_summary.decision_engine,
        "dashboard",
        lambda window_days: {"summary": {"existing": True}, "window_days": window_days},
    )
    monkeypatch.setattr(
        dashboard_summary.metric_lineage,
        "dashboard_metrics",
        lambda **kwargs: {"run": {"id": 10, "staff": kwargs["generated_by_staff_id"]}, "metrics": [{"key": "views"}]},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_dashboard_official_matrix_summary",
        lambda limit: {"account_count": 18, "post_count": 120, "total_views": 5000, "platform_count": 4},
    )

    payload = dashboard_summary.build_dashboard_summary(window_days=14, staff_id=None, staff={"id": 7})

    assert payload["window_days"] == 14
    assert payload["metric_run"] == {"id": 10, "staff": 7}
    assert payload["metrics"] == [{"key": "views"}]
    assert payload["official_matrix_summary"]["account_count"] == 18
    assert payload["summary"]["existing"] is True
    assert payload["summary"]["official_account_count"] == 18
    assert payload["summary"]["official_post_count"] == 120
    assert payload["summary"]["official_total_views"] == 5000


def test_build_dashboard_summary_uses_staff_dashboard_when_scoped(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(dashboard_summary.scope, "effective_staff_id", lambda staff, staff_id: 42)
    monkeypatch.setattr(dashboard_summary, "resolve_staff_id", lambda staff: 9)
    monkeypatch.setattr(
        dashboard_summary,
        "build_dashboard_kpi",
        lambda **kwargs: {"active_roster": 4, "scope": kwargs.get("staff_scope_id")},
    )

    def dashboard_view(view, *, window_days, staff_id):
        calls["view"] = view
        calls["window_days"] = window_days
        calls["staff_id"] = staff_id
        return {"summary": {}}

    monkeypatch.setattr(dashboard_summary.decision_engine, "dashboard_view", dashboard_view)
    monkeypatch.setattr(
        dashboard_summary.metric_lineage,
        "dashboard_metrics",
        lambda **kwargs: {"run": {"staff_id": kwargs["staff_id"]}, "metrics": []},
    )
    monkeypatch.setattr(dashboard_summary, "_dashboard_official_matrix_summary", lambda limit: {})

    payload = dashboard_summary.build_dashboard_summary(window_days=30, staff_id=42, staff={"id": 9})

    assert calls == {"view": "staff", "window_days": 30, "staff_id": 42}
    assert payload["metric_run"] == {"staff_id": 42}
    assert payload["metrics"] == []
