#!/usr/bin/env python3
"""Static guard for P3.6G KOL Pool decision view.

Locks the candidate-pool UI so it remains a decision surface, not only a raw
import table. Runtime API behavior is covered by KOL Pool service smokes.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "frontend/src/components/vkpi/panels/KolPoolPanel.tsx"
CSS = ROOT / "frontend/src/components/vkpi/VkpiDashboard.css"


def require_contains(path: Path, needle: str) -> None:
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise AssertionError(f"{path.name} missing expected marker: {needle}")


def main() -> None:
    require_contains(PANEL, "P3.6G: 候选池决策视图")
    require_contains(PANEL, "<th>决策</th>")
    require_contains(PANEL, "DecisionCell")
    require_contains(PANEL, "decisionProfile(item)")
    require_contains(PANEL, "metricReadiness(item)")
    require_contains(PANEL, "四维判断")
    require_contains(PANEL, "决策优先级")
    require_contains(PANEL, "自动入主表")
    require_contains(PANEL, "点击查看")

    require_contains(CSS, ".vkpi-kol-pool-decision-cell")
    require_contains(CSS, ".vkpi-kol-pool-readiness-card")
    require_contains(CSS, ".vkpi-kol-pool-readiness-grid")

    print("VKPI_KOL_POOL_DECISION_VIEW_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
