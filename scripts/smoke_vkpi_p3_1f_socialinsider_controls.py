#!/usr/bin/env python3
"""Static smoke for P3.1F reference-dashboard style data-analysis controls.

Locks the first usable dashboard control layer: date range, compare, group-by,
KPI/chart selector, and chart visibility wired to real component state.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx"
FILTER = ROOT / "frontend/src/components/vkpi/pages/data-analysis/drawers/FilterDrawer.tsx"
HOME = ROOT / "frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx"
TYPES = ROOT / "frontend/src/components/vkpi/pages/data-analysis/utils/types.ts"
KPI_OPTIONS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/utils/kpiOptions.ts"
CSS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css"


def require_contains(path: Path, needle: str) -> None:
    text = path.read_text()
    if needle not in text:
        raise AssertionError(f"{path.name} missing expected marker: {needle}")


def main() -> None:
    require_contains(TYPES, "export type ChartKey")
    require_contains(KPI_OPTIONS, "export const CHART_OPTIONS")
    require_contains(KPI_OPTIONS, "export const DEFAULT_CHARTS")

    require_contains(PANEL, "type DatePreset")
    require_contains(PANEL, "const DATE_PRESETS")
    require_contains(PANEL, "const [datePreset, setDatePreset]")
    require_contains(PANEL, "const [compareMode, setCompareMode]")
    require_contains(PANEL, "const [groupBy, setGroupBy]")
    require_contains(PANEL, "const [selectedCharts, setSelectedCharts]")
    require_contains(PANEL, "const visiblePosts = useMemo")
    require_contains(PANEL, "className=\"da-analysis-toolbar\"")
    require_contains(PANEL, "Select KPIs / Charts")
    require_contains(PANEL, "posts={visiblePosts}")
    require_contains(PANEL, "selectedCharts={selectedCharts}")

    require_contains(FILTER, "selectedCharts")
    require_contains(FILTER, "onChartToggle")
    require_contains(FILTER, "Select KPIs")
    require_contains(FILTER, "Select Charts")
    require_contains(FILTER, "da-filter-accordion")

    require_contains(HOME, "selectedCharts.includes('top_profiles')")
    require_contains(HOME, "selectedCharts.includes('top_posts')")
    require_contains(HOME, "selectedCharts.includes('posts_distribution')")
    require_contains(HOME, "selectedCharts.includes('posting_signals')")

    require_contains(CSS, ".da-analysis-toolbar")
    require_contains(CSS, ".da-analysis-toolbar__field")
    require_contains(CSS, ".da-filter-accordion")
    require_contains(CSS, "width: min(520px, 94vw)")

    print("VKPI_P3_1F_SOCIALINSIDER_CONTROLS_SMOKE_OK")


if __name__ == "__main__":
    main()
