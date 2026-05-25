from app.domains.dashboard import views as dashboard_views


def test_staff_id_from_context_uses_employee_views(monkeypatch):
    monkeypatch.setattr(dashboard_views, "resolve_staff_id", lambda staff: 12)

    assert dashboard_views.staff_id_from_context("staff", {"id": 12}) == 12
    assert dashboard_views.staff_id_from_context("employee", {"id": 12}) == 12
    assert dashboard_views.staff_id_from_context("manager", {"id": 12}) is None


def test_build_dashboard_view_payload_adds_lineage(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(dashboard_views, "resolve_staff_id", lambda staff: 8)
    monkeypatch.setattr(dashboard_views.scope, "effective_staff_id", lambda staff, staff_id: staff_id)

    def dashboard_view(view, *, window_days, staff_id):
        calls["view"] = view
        calls["window_days"] = window_days
        calls["staff_id"] = staff_id
        return {"view": view}

    monkeypatch.setattr(dashboard_views.decision_engine, "dashboard_view", dashboard_view)
    monkeypatch.setattr(
        dashboard_views.metric_lineage,
        "dashboard_metrics",
        lambda **kwargs: {"run": {"staff_id": kwargs["staff_id"]}, "metrics": [{"key": "gmv"}]},
    )

    payload = dashboard_views.build_dashboard_view_payload(view="staff", window_days=7, staff_id=None, staff={"id": 8})

    assert calls == {"view": "staff", "window_days": 7, "staff_id": 8}
    assert payload["metric_run"] == {"staff_id": 8}
    assert payload["metrics"] == [{"key": "gmv"}]


def test_build_dashboard_view_payload_keeps_empty_lineage_on_error(monkeypatch):
    monkeypatch.setattr(dashboard_views.scope, "effective_staff_id", lambda staff, staff_id: None)
    monkeypatch.setattr(
        dashboard_views.decision_engine,
        "dashboard_view",
        lambda view, *, window_days, staff_id: {"view": view, "window_days": window_days, "staff_id": staff_id},
    )

    def raise_lineage(**_kwargs):
        raise RuntimeError("lineage unavailable")

    monkeypatch.setattr(dashboard_views.metric_lineage, "dashboard_metrics", raise_lineage)

    payload = dashboard_views.build_dashboard_view_payload(view="manager", window_days=14, staff_id=None, staff={})

    assert payload["view"] == "manager"
    assert payload["metric_run"] == {}
    assert payload["metrics"] == []
