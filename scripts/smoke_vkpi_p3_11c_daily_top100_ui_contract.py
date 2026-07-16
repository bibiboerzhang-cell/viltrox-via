#!/usr/bin/env python3
"""Static contract smoke for P3.11C Daily Top100 UI wording and product scope.

This does not replace browser QA. It protects the product-scope controls and
non-ambiguous staff-count labels from being removed in future UI edits.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx"
API = ROOT / "frontend/src/services/vkpi.ui-api.ts"
CSS = ROOT / "frontend/src/components/vkpi/VkpiDashboard.css"

panel = PANEL.read_text(encoding="utf-8")
api = API.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")

required_panel_terms = [
    "候选产品口径",
    "活跃员工",
    "符合分发",
    "已生成清单",
    "有候选员工",
    "无候选员工",
    "候选来源",
    "产品级候选",
    "重复分发",
    "查看分发细节",
    "按当前口径生成 Top100",
]
missing = [term for term in required_panel_terms if term not in panel]
if missing:
    raise SystemExit(f"missing Daily Top100 UI terms: {missing}")

if "const refreshedStatus = await getDailyOutreachDigestStatus(apiToken, productSku)" not in panel:
    raise SystemExit("Daily Top100 generate flow must refresh status after mutation")

if "有效员工" in panel:
    raise SystemExit("ambiguous label remains: 有效员工")

required_api_terms = [
    "getDailyOutreachDigestStatus(token: string, productSku = \"\")",
    "generateDailyOutreachDigest(token: string, productSku = \"\")",
    "params.set(\"product_sku\", productSku.trim())",
    "body.product_sku = productSku.trim()",
]
missing_api = [term for term in required_api_terms if term not in api]
if missing_api:
    raise SystemExit(f"missing product_sku API support: {missing_api}")

required_css_terms = [
    ".vkpi-digest-scope",
    ".vkpi-digest-status-grid",
    ".vkpi-digest-source-strip",
    ".vkpi-digest-details",
]
missing_css = [term for term in required_css_terms if term not in css]
if missing_css:
    raise SystemExit(f"missing digest CSS contracts: {missing_css}")

stdout_out("VKPI_P3_11C_DAILY_TOP100_UI_CONTRACT_OK")
