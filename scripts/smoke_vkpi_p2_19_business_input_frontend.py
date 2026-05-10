#!/usr/bin/env python3
"""Static smoke for P2.19 project input and evidence upload UX."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    types = read("frontend/src/components/vkpi/vkpiTypes.ts")
    dashboard = read("frontend/src/components/vkpi/VkpiDashboard.tsx")
    ui_api = read("frontend/src/services/vkpi.ui-api.ts")
    projects = read("frontend/src/components/vkpi/pages/ProjectsPage.tsx")
    drawer = read("frontend/src/components/vkpi/drawers/ProjectDetailDrawer.tsx")
    forms = read("frontend/src/components/vkpi/drawers/ProjectEvidenceForms.tsx")

    assert "VkpiProductLaunchOption" in types and "productLaunches:" in types, "product launch option type missing"
    assert "productLaunches: []" in dashboard, "dashboard empty state missing productLaunches"
    assert "onUploadEvidenceFile={onUploadEvidenceFile}" in dashboard, "dashboard does not pass upload handler to drawer"

    assert "buildProductLaunchOptions" in ui_api, "product launch mapper missing"
    assert "/api/admin/vkpi/product-analysis/launches?limit=200" in ui_api, "product launch API fetch missing"
    assert "productLaunchesResult" in ui_api and "productLaunches," in ui_api, "productLaunches not returned in dashboard data"

    assert "useMemo" in projects and "data.productLaunches" in projects, "ProjectsPage product launch selector source missing"
    assert "sourceLabel: '成本目录'" in projects and "sourceLabel: launch.status" in projects, "product selector source labels missing"
    assert "selectedProductSkus" in projects and "selectPrimaryProduct" in projects, "product multi-select flow missing"

    assert "onUploadEvidenceFile" in drawer and "ProjectEvidenceForms" in drawer, "drawer upload prop not wired"
    assert "uploadEvidence" in forms, "evidence upload helper missing"
    for token in [
        "messageEvidenceFile",
        "contentAssetFile",
        "termsEvidenceFile",
        "shipmentEvidenceFile",
    ]:
        assert token in forms, f"{token} state missing"
    for purpose in [
        "message_evidence",
        "content_asset",
        "terms_evidence",
        "shipment_evidence",
    ]:
        assert purpose in forms, f"{purpose} upload purpose missing"
    assert forms.count('type="file"') >= 4, "project detail evidence file inputs missing"
    assert "entityType: 'project'" in forms and "entityId: projectId" in forms, "upload metadata missing project scope"

    print("VKPI_P2_19_BUSINESS_INPUT_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
