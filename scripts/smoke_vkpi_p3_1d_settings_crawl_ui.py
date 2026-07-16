#!/usr/bin/env python3
"""Static smoke for P3.1D platform crawl settings UI.

The goal is to prevent the settings page from regressing back to the dense
13-card editor and to protect hidden advanced checkbox values from being
silently cleared when only numeric limits are saved.

It also locks the sidebar reachability fix. On shorter screens the old sidebar
could push "系统设置" below the clickable viewport.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PANELS = ROOT / "frontend/src/components/vkpi/pages/settings/SettingsControlPanels.tsx"
SETTINGS_PAGE = ROOT / "frontend/src/components/vkpi/pages/SettingsPage.tsx"
CSS = ROOT / "frontend/src/components/vkpi/VkpiDashboard.css"


def require_contains(path: Path, needle: str) -> None:
    text = path.read_text()
    if needle not in text:
        raise AssertionError(f"{path.name} missing expected marker: {needle}")


def require_not_contains(path: Path, needle: str) -> None:
    text = path.read_text()
    if needle in text:
        raise AssertionError(f"{path.name} still contains legacy marker: {needle}")


def main() -> None:
    require_contains(CONTROL_PANELS, "vkpi-platform-crawl-console")
    require_contains(CONTROL_PANELS, "vkpi-platform-crawl-list")
    require_contains(CONTROL_PANELS, "vkpi-platform-crawl-detail")
    require_contains(CONTROL_PANELS, "advancedOpen")
    require_contains(CONTROL_PANELS, "开启当前平台")
    require_contains(CONTROL_PANELS, "保存当前平台限制")
    require_not_contains(CONTROL_PANELS, "vkpi-settings-card-grid--platforms")

    require_contains(SETTINGS_PAGE, "const formBool =")
    require_contains(SETTINGS_PAGE, "formBool(form, 'crawl_comments', rowEnabled(row, 'crawl_comments'))")
    require_contains(SETTINGS_PAGE, "formBool(form, 'include_candidate_kols', rowEnabled(row, 'include_candidate_kols'))")

    require_contains(CSS, ".vkpi-platform-crawl-console")
    require_contains(CSS, ".vkpi-platform-crawl-row")
    require_contains(CSS, ".vkpi-platform-crawl-detail")
    require_contains(CSS, ".vkpi-crawl-primary-toggle")
    require_contains(CSS, ".vkpi-sidebar")
    require_contains(CSS, "overflow: hidden")
    require_contains(CSS, ".vkpi-nav")
    require_contains(CSS, "overflow-y: auto")
    require_contains(CSS, "scrollbar-width: thin")
    require_contains(CSS, "@media (max-width: 1180px)")
    require_contains(CSS, "@media (max-width: 760px)")

    stdout_out("VKPI_P3_1D_SETTINGS_CRAWL_UI_SMOKE_OK")


if __name__ == "__main__":
    main()
