import pytest
from fastapi import Response

from app.domains.dashboard import summary as dashboard_summary
from app.domains.dashboard import summary_cache


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
        "build_dashboard_active_roster_counts",
        lambda **kwargs: {"all": 12, "kol": 8, "media": 2, "company": 2},
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
        "build_dashboard_active_roster_counts",
        lambda **kwargs: {"all": 4, "kol": 4, "media": 0, "company": 0},
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


def test_full_dashboard_cache_is_scope_isolated_and_returns_defensive_copies(monkeypatch):
    stored = {}
    builds = []

    def fake_cache_get_or_build(key, builder, ttl, cache_if):
        if key not in stored:
            value = builder()
            assert cache_if(value) is True
            stored[key] = value
        return stored[key]

    monkeypatch.setattr(dashboard_summary, "cache_get_or_build", fake_cache_get_or_build)
    monkeypatch.setattr(
        dashboard_summary.scope,
        "effective_staff_id",
        lambda staff, _requested: None if staff.get("role") == "owner" else int(staff["id"]),
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_build_dashboard_summary_uncached",
        lambda **kwargs: builds.append(kwargs) or {"summary": {"value": len(builds)}},
    )

    owner = {"id": 1, "role": "owner", "organization_id": 9}
    first = dashboard_summary.build_dashboard_summary(window_days=30, metric_scope="all", staff=owner)
    first["summary"]["value"] = 999
    second = dashboard_summary.build_dashboard_summary(window_days=30, metric_scope="all", staff=owner)
    employee = dashboard_summary.build_dashboard_summary(
        window_days=30,
        metric_scope="all",
        staff={"id": 42, "role": "employee", "organization_id": 9},
    )
    other_tenant = dashboard_summary.build_dashboard_summary(
        window_days=30,
        metric_scope="all",
        staff={"id": 1, "role": "owner", "organization_id": 10},
    )

    assert len(builds) == 3
    assert second["summary"]["value"] == 1
    assert employee["summary"]["value"] == 2
    assert other_tenant["summary"]["value"] == 3
    assert any("tenant=9:scope=global" in key for key in stored)
    assert any("tenant=9:scope=42" in key for key in stored)
    assert any("tenant=10:scope=global" in key for key in stored)


def test_full_summary_cache_ttl_outlives_the_dashboard_poll_interval():
    """TTL 必须大于门面轮询间隔,否则每一拍都过期 = 缓存等于没有。

    门面轮询是 90s(frontend useCockpitRuntime.ts DASHBOARD_REFRESH_MS)。
    2026-08-25 线上取证:TTL=30s 时近 6h 60 次读缓存只命中 6 次(10%),
    其余全走 1.5-2.7s 的 builder。这条断言就是钉住那次回归的护栏。
    """

    assert dashboard_summary._FULL_SUMMARY_CACHE_TTL > 90


def test_manual_force_refresh_reaches_cache_layer_and_polling_does_not(monkeypatch):
    """手动刷新必须能穿透读缓存;默认(轮询)路径的调用形状保持不变。"""

    seen_kwargs: list[dict] = []

    def fake_cache_get_or_build(key, builder, ttl, cache_if, **kwargs):
        del key, ttl, cache_if
        seen_kwargs.append(dict(kwargs))
        return builder()

    monkeypatch.setattr(dashboard_summary, "cache_get_or_build", fake_cache_get_or_build)
    monkeypatch.setattr(dashboard_summary.scope, "effective_staff_id", lambda staff, _requested: None)
    monkeypatch.setattr(
        dashboard_summary,
        "_build_dashboard_summary_uncached",
        lambda **_kwargs: {"summary": {}},
    )

    owner = {"id": 1, "role": "owner", "organization_id": 9}
    dashboard_summary.build_dashboard_summary(window_days=30, metric_scope="all", staff=owner)
    dashboard_summary.build_dashboard_summary(
        window_days=30,
        metric_scope="all",
        staff=owner,
        force_refresh=True,
    )

    assert seen_kwargs == [{}, {"force_refresh": True}]


def test_dashboard_cache_observer_exposes_exact_builder_and_hit_headers():
    miss_response = Response()
    summary_cache.dashboard_cache_observer(response=miss_response)(
        {"outcome": "miss_builder", "elapsed_ms": 12.3456, "builder_ms": 10.25}
    )

    assert miss_response.headers["X-VKPI-Cache"] == "miss_builder"
    assert miss_response.headers["X-VKPI-Cache-Builder"] == "1"
    assert miss_response.headers["X-VKPI-Cache-Key-Version"] == "v1"
    assert miss_response.headers["Server-Timing"] == (
        'dashboard-cache;desc="miss_builder";dur=12.346, dashboard-builder;dur=10.250'
    )

    hit_response = Response()
    summary_cache.dashboard_cache_observer(response=hit_response)(
        {"outcome": "hit", "elapsed_ms": 0.5}
    )

    assert hit_response.headers["X-VKPI-Cache"] == "hit"
    assert hit_response.headers["X-VKPI-Cache-Builder"] == "0"
    assert hit_response.headers["X-VKPI-Cache-Key-Version"] == "v1"
    assert hit_response.headers["Server-Timing"] == 'dashboard-cache;desc="hit";dur=0.500'


def test_dashboard_authz_bypass_is_observable_without_shared_cache():
    response = Response()

    result = summary_cache.cached_full_summary(
        cache_get_or_build_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unprovable auth scope must bypass shared cache")
        ),
        builder=lambda: {"summary": {"value": 7}},
        window_days=30,
        metric_scope="all",
        effective_staff_id=7,
        staff={"id": 7},
        ttl=30,
        observe=summary_cache.dashboard_cache_observer(response=response),
    )

    assert result == {"summary": {"value": 7}}
    assert response.headers["X-VKPI-Cache"] == "authz_bypass"
    assert response.headers["X-VKPI-Cache-Builder"] == "1"
    assert "dashboard-builder" in response.headers["Server-Timing"]


def test_dashboard_authz_bypass_ignores_observer_failure():
    result = summary_cache.cached_full_summary(
        cache_get_or_build_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unprovable auth scope must bypass shared cache")
        ),
        builder=lambda: {"summary": {"healthy": True}},
        window_days=30,
        metric_scope="all",
        effective_staff_id=7,
        staff={"id": 7},
        ttl=30,
        observe=lambda _payload: (_ for _ in ()).throw(RuntimeError("telemetry down")),
    )

    assert result == {"summary": {"healthy": True}}


def test_dashboard_window_is_normalized_once_for_builder_and_cache(monkeypatch):
    stored = {}
    builds = []

    def fake_cache_get_or_build(key, builder, ttl, cache_if):
        if key not in stored:
            value = builder()
            assert cache_if(value) is True
            stored[key] = value
        return stored[key]

    monkeypatch.setattr(dashboard_summary, "cache_get_or_build", fake_cache_get_or_build)
    monkeypatch.setattr(dashboard_summary.scope, "effective_staff_id", lambda _staff, _requested: None)
    monkeypatch.setattr(
        dashboard_summary,
        "_build_dashboard_summary_uncached",
        lambda **kwargs: builds.append(kwargs["window_days"])
        or {"window_days": kwargs["window_days"]},
    )
    staff = {"id": 1, "role": "owner", "organization_id": 9}

    overflow = dashboard_summary.build_dashboard_summary(window_days=1000, staff=staff)
    boundary = dashboard_summary.build_dashboard_summary(window_days=180, staff=staff)

    assert overflow == boundary == {"window_days": 180}
    assert builds == [180]
    assert len(stored) == 1
    assert "window=180" in next(iter(stored))


def test_nested_dashboard_cache_is_tenant_partitioned_and_unknown_bypasses(monkeypatch):
    stored = {}
    builds = []

    monkeypatch.setattr(summary_cache, "cache_get", lambda key: stored.get(key))
    monkeypatch.setattr(summary_cache, "cache_set", lambda key, value, _ttl: stored.__setitem__(key, value))

    def build():
        builds.append(len(builds) + 1)
        return {"value": builds[-1]}

    first = summary_cache.cached_summary_block(
        "funnel", None, build, tenant_partition="9", window=30
    )
    same = summary_cache.cached_summary_block(
        "funnel", None, build, tenant_partition="9", window=30
    )
    other = summary_cache.cached_summary_block(
        "funnel", None, build, tenant_partition="10", window=30
    )
    unknown_a = summary_cache.cached_summary_block("funnel", None, build)
    unknown_b = summary_cache.cached_summary_block("funnel", None, build)

    assert (first, same, other) == ({"value": 1}, {"value": 1}, {"value": 2})
    assert (unknown_a, unknown_b) == ({"value": 3}, {"value": 4})
    assert len(stored) == 2


def test_uncached_dashboard_assembles_staged_reads_with_contract_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(dashboard_summary.scope, "effective_staff_id", lambda _staff, _requested: None)
    monkeypatch.setattr(dashboard_summary, "resolve_staff_id", lambda _staff: 7)
    monkeypatch.setattr(
        dashboard_summary,
        "build_dashboard_active_roster_counts",
        lambda **_kwargs: calls.append("active_roster")
        or {"all": 3, "kol": 2, "media": 0, "company": 1},
    )
    monkeypatch.setattr(
        dashboard_summary.decision_engine,
        "dashboard",
        lambda **_kwargs: calls.append("decision")
        or {"summary": {"metric_series_by_scope": {}}},
    )
    monkeypatch.setattr(
        dashboard_summary.metric_lineage,
        "dashboard_metrics",
        lambda **_kwargs: calls.append("lineage")
        or {"run": {}, "metrics": []},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "dashboard_metric_maturity_contract",
        lambda: calls.append("maturity") or {
            "scopes": {
                "all": {
                    "scope_label": "all",
                    "snapshot_days": 0,
                    "required_days": 30,
                    "maturity_label": "pending",
                }
            }
        },
    )
    monkeypatch.setattr(
        dashboard_summary,
        "dashboard_window_metrics_contract",
        lambda _contract: calls.append("window_metrics") or {
            "exposure_30d_by_scope": {},
            "engagement_rate_by_scope": {},
            "active_30d_by_scope": {},
        },
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_dashboard_official_matrix_summary",
        lambda **_kwargs: calls.append("official_summary") or {},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_build_evidence_metrics_summary",
        lambda **_kwargs: calls.append("evidence_metrics")
        or {"coverage": {"last_refreshed_at": "2026-08-24T00:00:00Z"}},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_build_active_campaigns_summary",
        lambda **_kwargs: calls.append("active_campaigns") or {"active_count": 1},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_build_funnel_summary",
        lambda **_kwargs: calls.append("funnel") or {"favorites_total": 3},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_build_company_window_metrics",
        lambda: calls.append("company_window") or {},
    )
    monkeypatch.setattr(
        dashboard_summary,
        "_build_company_metric_series",
        lambda **_kwargs: calls.append("company_series") or {"status": "real"},
    )

    result = dashboard_summary._build_dashboard_summary_uncached(
        window_days=30,
        metric_scope="all",
        staff={"id": 7, "role": "owner", "organization_id": 1},
    )

    assert calls == [
        "decision",
        "lineage",
        "official_summary",
        "active_roster",
        "maturity",
        "window_metrics",
        "evidence_metrics",
        "active_campaigns",
        "funnel",
        "company_window",
        "company_series",
    ]
    assert result["summary"]["active_roster"] == 3
    assert result["summary"]["evidence_metrics"]["coverage"]["last_refreshed_at"] == "2026-08-24T00:00:00Z"
    assert result["summary"]["active_campaigns"] == {"active_count": 1}
    assert result["summary"]["funnel"] == {"favorites_total": 3}
    assert result["summary"]["metric_series_by_scope"]["company"] == {"status": "real"}
