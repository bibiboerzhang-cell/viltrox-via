"""预测读路径边界：页面预览不能制造训练或准确率样本。"""
from __future__ import annotations

from typing import Any


def test_forecast_get_and_launch_preview_force_dry_run(monkeypatch):
    from app.api.routers import vkpi_forecast
    from app.domains.kol import performance_forecast
    from app.domains.projects import launch_assembly

    calls: list[dict[str, Any]] = []

    def _fake_forecast(kol_pool_id: int, sku: str | None = None, **kw: Any) -> dict[str, Any]:
        calls.append({"kol_pool_id": kol_pool_id, "sku": sku, **kw})
        return {
            "status": "ready",
            "kol_pool_id": kol_pool_id,
            "expected_views_p50": 100,
        }

    monkeypatch.setattr(performance_forecast, "forecast_for_kol", _fake_forecast)

    # B 线 legacy scope 闸:只放行显式解析到 organization 1 的系统管理员。
    route_result = vkpi_forecast.get_kol_forecast(
        11,
        "AF-85",
        staff={"organization_id": 1, "organization_scope_status": "resolved"},
    )
    launch_result = launch_assembly._forecast_block(
        [{"kol_pool_id": 12, "handle": "creator"}],
        "AF-85",
    )

    assert route_result["status"] == "ready"
    assert launch_result["status"] == "ready"
    assert [call["kol_pool_id"] for call in calls] == [11, 12]
    assert all(call.get("dry_run") is True for call in calls)
