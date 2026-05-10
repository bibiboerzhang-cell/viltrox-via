#!/usr/bin/env python3
"""P2.24 smoke for budget/crawl gate alignment.

This smoke is intentionally offline. It verifies that Data Analysis refreshes
use the same global budget gates exposed in Settings, without triggering any
YouTube or Apify network calls.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ACCOUNT_DRAWER = ROOT / "frontend" / "src" / "components" / "vkpi" / "pages" / "data-analysis" / "drawers" / "AccountDrawer.tsx"
CROSS_PLATFORM_PANEL = ROOT / "frontend" / "src" / "components" / "vkpi" / "pages" / "data-analysis" / "CrossPlatformPanel.tsx"
DATA_ANALYSIS_CSS = ROOT / "frontend" / "src" / "components" / "vkpi" / "pages" / "data-analysis" / "styles" / "data-analysis.css"


def _assert(condition: bool, message: str, payload: Any = None) -> None:
    if not condition:
        suffix = "" if payload is None else f": {payload}"
        raise AssertionError(message + suffix)


def _budget_row(key: str, *, enabled: bool, monthly: float, spent: float = 0) -> dict[str, Any]:
    return {
        "budget_key": key,
        "enabled": enabled,
        "monthly_limit_usd": monthly,
        "current_month_spent": spent,
    }


def _assert_crawl_budget_gate() -> None:
    from app.services.vkpi import platform_crawl_settings as settings

    original_budget_settings = settings.budget_settings
    try:
        settings.budget_settings = lambda: {
            "budgets": [
                _budget_row("crawl_total", enabled=False, monthly=0),
                _budget_row("apify", enabled=True, monthly=10),
            ]
        }
        blocked = settings.crawl_budget_gate("instagram")
        _assert(blocked.get("allowed") is False, "crawl_total disabled should block live crawl", blocked)
        _assert(blocked.get("reason") == "crawl_total_budget_disabled", "wrong crawl_total block reason", blocked)
        _assert(blocked.get("budget_key") == "crawl_total", "wrong crawl_total budget key", blocked)

        settings.budget_settings = lambda: {
            "budgets": [
                _budget_row("crawl_total", enabled=True, monthly=10),
                _budget_row("apify", enabled=False, monthly=0),
            ]
        }
        blocked = settings.crawl_budget_gate("instagram")
        _assert(blocked.get("allowed") is False, "apify disabled should block Apify-backed crawl", blocked)
        _assert(blocked.get("reason") == "apify_budget_disabled", "wrong apify block reason", blocked)
        _assert(blocked.get("budget_key") == "apify", "wrong apify budget key", blocked)

        settings.budget_settings = lambda: {
            "budgets": [
                _budget_row("crawl_total", enabled=True, monthly=10, spent=2),
                _budget_row("apify", enabled=True, monthly=10, spent=2),
            ]
        }
        allowed = settings.crawl_budget_gate("instagram")
        _assert(allowed.get("allowed") is True, "enabled crawl_total + apify budget should pass", allowed)

        allowed = settings.crawl_budget_gate("youtube")
        _assert(allowed.get("allowed") is True, "youtube should not require apify budget", allowed)
    finally:
        settings.budget_settings = original_budget_settings


def _assert_provider_gate_uses_global_budget() -> None:
    from app.services.vkpi import industry_snapshot_collector as collector

    original_platform_config = collector._platform_config
    original_crawl_budget_gate = collector.platform_crawl_settings.crawl_budget_gate
    try:
        collector._platform_config = lambda platform: {
            "crawl_enabled": True,
            "monthly_budget_usd": 50,
        }
        collector.platform_crawl_settings.crawl_budget_gate = lambda platform: {
            "allowed": False,
            "reason": "apify_budget_disabled",
            "message": "该平台走 Apify 链路，apify 预算未启用或余额为 0，未执行外部抓取。",
            "budget_key": "apify",
        }
        result = collector.provider_gate({"platform": "instagram", "crawl_enabled": True}, force=False)
        _assert(result.get("allowed") is False, "provider gate should block when global budget gate blocks", result)
        _assert(result.get("provider_status") == "budget_disabled", "provider gate should surface budget_disabled", result)
        _assert(result.get("reason") == "apify_budget_disabled", "provider gate should preserve budget reason", result)
        _assert(result.get("budget_key") == "apify", "provider gate should preserve budget key", result)

        result = collector.provider_gate({"platform": "instagram", "crawl_enabled": True}, force=True)
        _assert(result.get("allowed") is not False or result.get("reason") != "apify_budget_disabled", "force=True should bypass budget gate", result)
    finally:
        collector._platform_config = original_platform_config
        collector.platform_crawl_settings.crawl_budget_gate = original_crawl_budget_gate


def _assert_frontend_gate_wiring() -> None:
    drawer = ACCOUNT_DRAWER.read_text(encoding="utf-8")
    panel = CROSS_PLATFORM_PANEL.read_text(encoding="utf-8")
    css = DATA_ANALYSIS_CSS.read_text(encoding="utf-8")

    for expected in [
        "platformCrawlSettings?: Row[]",
        "budgetSettings?: Row[]",
        "APIFY_CRAWL_PLATFORMS",
        "budgetReady",
        "全局 crawl_total",
        "Apify 预算",
        "抓取闸门",
        "当前阻塞",
        "crawlGateItems",
    ]:
        _assert(expected in drawer, f"AccountDrawer missing {expected}")

    for expected in [
        "listPlatformCrawlSettings",
        "listBudgetSettings",
        "platformCrawlSettings={platformCrawlSettings}",
        "budgetSettings={budgetSettings}",
    ]:
        _assert(expected in panel, f"CrossPlatformPanel missing {expected}")

    for expected in [
        ".da-crawl-gate-panel",
        ".da-crawl-gate-grid",
        ".da-crawl-gate-item.is-blocked",
    ]:
        _assert(expected in css, f"Data Analysis CSS missing {expected}")


def main() -> None:
    _assert_crawl_budget_gate()
    _assert_provider_gate_uses_global_budget()
    _assert_frontend_gate_wiring()
    print("VKPI_P2_24_BUDGET_CRAWL_LOOP_SMOKE_OK")


if __name__ == "__main__":
    main()
