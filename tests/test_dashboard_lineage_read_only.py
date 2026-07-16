from __future__ import annotations

from app.domains.lineage import service, store


def test_dashboard_metrics_never_generates_a_run_when_snapshot_is_missing(monkeypatch):
    monkeypatch.setattr(service.scope_service, "effective_staff_id", lambda staff, staff_id: None)
    monkeypatch.setattr(service, "recent_dashboard_run_id", lambda **kwargs: None)

    def forbidden_generate(**kwargs):
        raise AssertionError(f"GET-compatible dashboard_metrics attempted a write: {kwargs}")

    monkeypatch.setattr(service, "generate_run", forbidden_generate)

    result = service.dashboard_metrics(period_days=30, staff={"id": 1})

    assert result == {
        "run": {},
        "metrics": [],
        "status": "lineage_snapshot_pending",
        "refresh_path": "/api/admin/vkpi/lineage/runs",
    }


def test_dashboard_metrics_reads_exact_window_snapshot_without_refresh(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(service.scope_service, "effective_staff_id", lambda staff, staff_id: 40)

    def fake_run_id(**kwargs):
        captured.update(kwargs)
        return 8793

    monkeypatch.setattr(service, "recent_dashboard_run_id", fake_run_id)
    monkeypatch.setattr(
        service,
        "get_run",
        lambda run_id, staff=None: {
            "run": {"id": run_id, "scope_type": "staff", "scope_id": 40},
            "values": [
                {
                    "id": 84927,
                    "metric_key": "active_projects",
                    "value_numeric": 39,
                    "currency": "",
                    "unit": "count",
                    "source_count": 39,
                    "calculation_json": '{"formula":"COUNT"}',
                }
            ],
        },
    )

    result = service.dashboard_metrics(period_days=30, staff={"id": 40})

    assert captured == {
        "scope_type": "staff",
        "scope_id": 40,
        "period_days": 30,
        "max_age_seconds": None,
    }
    assert result["run"]["id"] == 8793
    assert result["metrics"][0]["metricValueId"] == 84927
    assert result["metrics"][0]["drilldown_url"].endswith("/84927/drilldown")


def test_window_snapshot_lookup_without_age_filter_is_read_only(monkeypatch):
    captured: dict[str, object] = {}

    class _Rows:
        @staticmethod
        def fetchall():
            return [
                {
                    "id": 8793,
                    "period_start": "2026-06-13T19:21:21Z",
                    "period_end": "2026-07-13T19:21:21Z",
                }
            ]

    class _Conn:
        @staticmethod
        def execute(sql, params):
            captured.update(sql=sql, params=params)
            return _Rows()

    monkeypatch.setattr(store, "ensure_vkpi_lineage_schema", lambda: None)
    monkeypatch.setattr(store, "get_conn", lambda: _Conn())

    run_id = store.recent_dashboard_run_id(
        scope_type="staff",
        scope_id=40,
        period_days=30,
        max_age_seconds=None,
    )

    assert run_id == 8793
    assert "generated_at >=" not in str(captured["sql"])
    assert captured["params"] == ("staff", 40)
