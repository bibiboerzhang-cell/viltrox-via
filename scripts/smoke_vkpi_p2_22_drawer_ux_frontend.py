#!/usr/bin/env python3
"""Static smoke for P2.22 drawer UX.

Opening a project detail from a profile drawer must not leave both drawers
visible at once. This catches the P2.20 browser QA regression where KOL Profile
and Project Detail stacked on top of each other.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    dashboard = read("frontend/src/components/vkpi/VkpiDashboard.tsx")
    p2_20 = read("docs/VKPI_P2_20_BROWSER_QA.md")

    assert "closeKolProfileDrawer" in dashboard, "KOL profile drawer close helper missing"
    assert "closeStaffProfileDrawer" in dashboard, "staff profile drawer close helper missing"
    assert "const handleSelectProject" in dashboard, "project selection handler missing"

    handler_start = dashboard.index("const handleSelectProject")
    handler_end = dashboard.index("};", handler_start)
    handler = dashboard[handler_start:handler_end]
    assert "closeKolProfileDrawer();" in handler, "opening project detail should close KOL drawer"
    assert "closeStaffProfileDrawer();" in handler, "opening project detail should close staff drawer"
    assert handler.index("closeKolProfileDrawer();") < handler.index("projectDetailDrawer.openProjectDetail(project);"), "KOL drawer must close before project detail opens"
    assert handler.index("closeStaffProfileDrawer();") < handler.index("projectDetailDrawer.openProjectDetail(project);"), "staff drawer must close before project detail opens"

    assert "onClose={closeKolProfileDrawer}" in dashboard, "KOL drawer close button should reuse helper"
    assert "onClose={closeStaffProfileDrawer}" in dashboard, "staff drawer close button should reuse helper"
    assert "KOL drawer 与 Project detail drawer 叠层" in p2_20, "P2.20 regression note missing"

    print("VKPI_P2_22_DRAWER_UX_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
