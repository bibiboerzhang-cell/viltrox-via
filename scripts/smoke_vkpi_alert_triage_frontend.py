#!/usr/bin/env python3
"""Static smoke for P2.9 alert triage UI.

Catches regressions where comment intelligence alerts are generated but no
longer visible or actionable from the command-center right rail.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    types = read("frontend/src/components/vkpi/vkpiTypes.ts")
    ui_api = read("frontend/src/services/vkpi.ui-api.ts")
    dashboard = read("frontend/src/components/vkpi/VkpiDashboard.tsx")
    command = read("frontend/src/components/vkpi/dashboard/CommandCenter.tsx")
    panel = read("frontend/src/components/vkpi/charts/AlertsPanel.tsx")
    css = read("frontend/src/components/vkpi/VkpiDashboard.css")

    assert "triageGroup?: 'comment_intelligence'" in types, "alert triage type missing"
    assert "negativeCount?: number" in types and "hostileCount?: number" in types, "comment risk count types missing"
    assert "parseJsonObject(row.metadata_json)" in ui_api, "alert metadata parser missing"
    assert "comment_intelligence" in ui_api and "resolveMarketingAlert" in ui_api, "alert mapping/resolve API missing"
    assert "resolvedAlertIds" in dashboard and "handleResolveAlert" in dashboard, "dashboard resolve state missing"
    assert "onResolveAlert" in command and "AlertsPanel alerts={alerts || data.alerts}" in command, "command center alert action bridge missing"
    assert "评论风险" in panel and "formatAlertMeta" in panel and "onResolveAlert(alert.id)" in panel, "alert triage UI missing"
    assert ".vkpi-alert-toolbar" in css and ".vkpi-alert-actions" in css, "alert triage CSS missing"

    stdout_out("VKPI_ALERT_TRIAGE_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
