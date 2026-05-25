from app.domains.dashboard import performance as dashboard_performance


def test_build_revenue_trend_applies_scope(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(dashboard_performance.scope, "effective_staff_id", lambda staff, staff_id: 22)

    def revenue_trend(*, window_days, staff_id):
        calls["window_days"] = window_days
        calls["staff_id"] = staff_id
        return {"trend": []}

    monkeypatch.setattr(dashboard_performance.decision_engine, "revenue_trend", revenue_trend)

    payload = dashboard_performance.build_revenue_trend(window_days=9, staff_id=12, staff={})

    assert payload == {"trend": []}
    assert calls == {"window_days": 9, "staff_id": 22}


def test_build_product_performance_applies_scope_and_limit(monkeypatch):
    calls: dict[str, object] = {}

    monkeypatch.setattr(dashboard_performance.scope, "effective_staff_id", lambda staff, staff_id: None)

    def product_performance(*, window_days, staff_id, limit):
        calls["window_days"] = window_days
        calls["staff_id"] = staff_id
        calls["limit"] = limit
        return {"items": []}

    monkeypatch.setattr(dashboard_performance.decision_engine, "product_performance", product_performance)

    payload = dashboard_performance.build_product_performance(window_days=30, staff_id=None, limit=8, staff={})

    assert payload == {"items": []}
    assert calls == {"window_days": 30, "staff_id": None, "limit": 8}
