#!/usr/bin/env python3
"""P4 Step32: Data Quality action UI governance contract."""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend/src/components/vkpi/pages/DataQualityPage.tsx"
CSS = ROOT / "frontend/src/components/vkpi/VkpiDashboard.css"

page = PAGE.read_text(encoding="utf-8")
css = CSS.read_text(encoding="utf-8")

required_page_terms = [
    "处理动作",
    "处理",
    "分派",
    "补救",
    "确认将该数据质量问题标记为已处理",
    "确认忽略该数据质量问题",
    "指派当前管理层",
    "记录重检",
    "记录补证据",
    "重新打开",
    "当前只记录重检请求",
    "当前只记录补证据动作",
    "actOnIssue(issue.id, 'reopen')",
]
missing = [term for term in required_page_terms if term not in page]
if missing:
    raise SystemExit(f"missing Data Quality action UI terms: {missing}")

if "window.confirm" not in page:
    raise SystemExit("Data Quality resolve/ignore actions must keep confirmation guard")

if "actOnIssue(issue.id, 'ignore')" not in page:
    raise SystemExit("Data Quality ignore action should remain explicit and audited")

required_css_terms = [
    ".vkpi-dq-action-menu",
    ".vkpi-dq-action-group",
]
missing_css = [term for term in required_css_terms if term not in css]
if missing_css:
    raise SystemExit(f"missing Data Quality action CSS contracts: {missing_css}")

stdout_out("VKPI_P4_32_DATA_QUALITY_ACTION_UI_CONTRACT_OK")
