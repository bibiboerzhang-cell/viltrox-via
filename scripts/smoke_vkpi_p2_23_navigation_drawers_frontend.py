#!/usr/bin/env python3
"""Static smoke for P2.23 cross-page drawer cleanup.

The browser regression this guards: navigating from Data Analysis / KOL Profile
back to another main page left global right-side drawers mounted on top of the
new page. This smoke keeps the navigation handler wired to close those drawers.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "frontend" / "src" / "components" / "vkpi" / "VkpiDashboard.tsx"
STATUS_DOC = ROOT / "docs" / "VKPI_P2_RELEASE_STATUS.md"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _between(source: str, start: str, end: str) -> str:
    start_index = source.index(start)
    end_index = source.index(end, start_index)
    return source[start_index:end_index]


def main() -> None:
    source = DASHBOARD.read_text(encoding="utf-8")
    status = STATUS_DOC.read_text(encoding="utf-8")

    _require("const closeWorkspaceDrawers = () =>" in source, "missing closeWorkspaceDrawers helper")
    close_block = _between(source, "const closeWorkspaceDrawers = () =>", "const handleSelectPage =")
    for expected in [
        "setEvidenceMetric(null)",
        "setEvidenceMetricValueId(null)",
        "projectDetailDrawer.closeProjectDetail()",
        "closeKolProfileDrawer()",
        "closeStaffProfileDrawer()",
        "closeAlertDetailDrawer()",
    ]:
        _require(expected in close_block, f"closeWorkspaceDrawers does not call {expected}")

    _require("const handleSelectPage = (page: VkpiPageKey) =>" in source, "missing handleSelectPage")
    nav_block = _between(source, "const handleSelectPage = (page: VkpiPageKey) =>", "const handleSelectProject =")
    _require("setActivePage(page)" in nav_block, "handleSelectPage does not switch page")
    _require("closeWorkspaceDrawers()" in nav_block, "handleSelectPage does not close drawers")

    _require("useEffect(() => {\n    closeWorkspaceDrawers();\n  }, [activePage]);" in source, "activePage change does not close drawers")

    _require("onSelectPage={handleSelectPage}" in source, "sidebar still bypasses drawer cleanup")
    _require("onSelectPage={setActivePage}" not in source, "sidebar still uses raw setActivePage")
    _require("onClose={closeKolProfileDrawer}" in source, "KOL drawer close helper regressed")
    _require("onClose={closeStaffProfileDrawer}" in source, "Staff drawer close helper regressed")
    _require("closeAlertDetailDrawer();" in source, "Alert drawer close helper is not reused")

    _require("P2.23" in status, "P2 release status does not mention P2.23")
    print("VKPI_P2_23_NAVIGATION_DRAWERS_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
