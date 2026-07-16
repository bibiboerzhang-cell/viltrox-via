#!/usr/bin/env python3
"""Static smoke for P2.11 alert drilldown UI."""
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
    drawer = read("frontend/src/components/vkpi/drawers/AlertDetailDrawer.tsx")
    css = read("frontend/src/components/vkpi/VkpiDashboard.css")

    assert "export interface VkpiAlertDetail" in types, "alert detail type missing"
    assert "getMarketingAlertDetail" in ui_api and "/api/marketing/alerts/" in ui_api, "alert detail API missing"
    assert "AlertDetailDrawer" in dashboard and "handleOpenAlert" in dashboard, "dashboard alert detail bridge missing"
    assert "onOpenAlert" in command and "onOpenAlert={onOpenAlert}" in command, "command center open bridge missing"
    assert "onOpenAlert(alert.id)" in panel and ">详情<" in panel, "alert detail button missing"
    assert "Flagged Comments" in drawer and "Source Post" in drawer and "Raw Metadata" in drawer, "drawer evidence sections missing"
    assert ".vkpi-alert-source-list" in css, "alert source CSS missing"

    stdout_out("VKPI_ALERT_DRILLDOWN_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
