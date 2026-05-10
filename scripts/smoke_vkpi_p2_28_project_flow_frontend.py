#!/usr/bin/env python3
"""Static smoke for P2.28 browser-verified project flow UX.

The browser pass is documented in docs/VKPI_P2_28_BROWSER_PROJECT_FLOW_QA.md.
This smoke protects the source-level affordances that make that browser flow
possible without requiring a browser runner in CI.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    projects = read("frontend/src/components/vkpi/pages/ProjectsPage.tsx")
    table = read("frontend/src/components/vkpi/tables/ProjectTable.tsx")
    drawer = read("frontend/src/components/vkpi/drawers/ProjectDetailDrawer.tsx")
    forms = read("frontend/src/components/vkpi/drawers/ProjectEvidenceForms.tsx")
    dashboard = read("frontend/src/components/vkpi/VkpiDashboard.tsx")
    release_status = read("docs/VKPI_P2_RELEASE_STATUS.md")
    qa_doc = read("docs/VKPI_P2_28_BROWSER_PROJECT_FLOW_QA.md")

    assert "data.kolOptions.length" in projects, "ProjectsPage must prefer existing KOL selector"
    assert "选择已有红人" in projects, "existing KOL selector placeholder missing"
    assert "暂无红人列表，可临时输入 KOL ID" in projects, "manual KOL fallback missing"

    assert "data.productCosts" in projects and "data.productLaunches" in projects, "product choices must merge cost catalog and launches"
    assert "selectedProductSkus" in projects, "multi-product selected state missing"
    assert "selectPrimaryProduct" in projects and "toggleProduct" in projects, "product single/multi handlers missing"
    assert "关联产品：" in projects, "multi-product note persistence missing"
    assert "sourceLabel: '成本目录'" in projects and "产品发布" in projects, "product source labels missing"

    assert "onClick={() => onSelectProject(project)}" in table, "project row should open detail drawer"
    assert "event.stopPropagation()" in table, "nested KOL/staff/delete actions must not hijack project row click"
    assert "title=\"点击查看员工、KOL、项目、消息、短链和归因详情\"" in table, "project row detail affordance missing"

    assert "ProjectEvidenceForms" in drawer, "ProjectDetailDrawer must render evidence forms"
    assert "onUploadEvidenceFile={onUploadEvidenceFile}" in drawer, "drawer must pass upload handler to evidence forms"
    assert "projectDetailDrawer.openProjectDetail(project)" in dashboard, "dashboard select handler must open project drawer"

    for label in [
        "消息附件 / PDF / 截图",
        "内容素材 / 截图 / PDF",
        "条款附件 / PDF / 报价单",
        "物流凭证 / PDF / 截图",
    ]:
        assert label in forms, f"{label} upload control missing"
    assert forms.count('type="file"') >= 4, "project evidence file inputs missing"
    assert "uploadEvidence" in forms and "onUploadEvidenceFile(file" in forms, "evidence upload helper wiring missing"
    for purpose in ["message_evidence", "content_asset", "terms_evidence", "shipment_evidence"]:
        assert purpose in forms, f"{purpose} upload purpose missing"

    assert "P2.28" in release_status and "smoke_vkpi_p2_28_project_flow_frontend.py" in release_status, "release status missing P2.28"
    assert "Browser QA" in qa_doc and "P2.28 browser QA message with attachment" in qa_doc, "P2.28 browser QA evidence doc incomplete"

    print("VKPI_P2_28_PROJECT_FLOW_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
