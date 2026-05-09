#!/usr/bin/env python3
"""Static smoke for Phase 0B V-KPI frontend entry split.

This catches accidental removal of the two top-level entry points without
requiring a browser or fake data.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    nav = read("frontend/src/components/vkpi/layout/vkpiLayoutConstants.ts")
    types = read("frontend/src/components/vkpi/vkpiTypes.ts")
    workspace = read("frontend/src/components/vkpi/pages/WorkspacePage.tsx")
    product = read("frontend/src/components/vkpi/pages/ProductBattlePage.tsx")
    recommendation_panel = read("frontend/src/components/vkpi/pages/analytics/ProductRecommendationPanel.tsx")
    recommendation_setup = read("frontend/src/components/vkpi/pages/analytics/RecommendationSetupForms.tsx")
    recommendation_hook = read("frontend/src/components/vkpi/pages/analytics/useProductRecommendationPanel.ts")
    recommendation_actions = read("frontend/src/components/vkpi/pages/analytics/useProductRecommendationActions.ts")
    recommendation_evidence_hook = read("frontend/src/components/vkpi/pages/analytics/useRecommendationEvidence.ts")
    outreach_tables = read("frontend/src/components/vkpi/pages/analytics/OutreachTables.tsx")
    project_drawer = read("frontend/src/components/vkpi/drawers/ProjectDetailDrawer.tsx")
    link_drawer = read("frontend/src/components/vkpi/drawers/LinkDetailDrawer.tsx")
    costs_page = read("frontend/src/components/vkpi/pages/CostsPage.tsx")
    attribution_page = read("frontend/src/components/vkpi/pages/AttributionPage.tsx")
    monitor_panel = read("frontend/src/components/vkpi/pages/analytics/AnalyticsMonitorPanel.tsx")
    recommendation_drawer = read("frontend/src/components/vkpi/pages/analytics/RecommendationDetailDrawer.tsx")
    recommendation_outcome = read("frontend/src/components/vkpi/pages/analytics/RecommendationOutcomeTable.tsx")
    recommendation_candidate = read("frontend/src/components/vkpi/pages/analytics/RecommendationCandidateTable.tsx")
    industry = read("frontend/src/components/vkpi/pages/IndustryDataPage.tsx")
    industry_panel = read("frontend/src/components/vkpi/pages/analytics/IndustryMatrixPanel.tsx")
    ui_api = read("frontend/src/services/vkpi.ui-api.ts")
    settings = read("frontend/src/components/vkpi/pages/SettingsPage.tsx")
    settings_admin_cards = read("frontend/src/components/vkpi/pages/settings/SettingsAdminCards.tsx")
    settings_control_panels = read("frontend/src/components/vkpi/pages/settings/SettingsControlPanels.tsx")
    settings_preference_panels = read("frontend/src/components/vkpi/pages/settings/SettingsPreferencePanels.tsx")
    settings_modules = "\n".join([settings, settings_admin_cards, settings_control_panels, settings_preference_panels])

    assert "产品作战" in nav and "productBattle" in nav, "productBattle nav missing"
    # R58A: 主导航改名 industryData → dataAnalysis,但 vkpiTypes 保留 industryData 兼容
    assert "数据分析" in nav and "dataAnalysis" in nav, "dataAnalysis nav missing"
    assert "'productBattle'" in types and "'dataAnalysis'" in types, "page keys missing"
    # WorkspacePage 路由: 优先用 DataAnalysisPage,旧 industryData 入口也走 DataAnalysisPage
    assert "ProductBattlePage" in workspace and "DataAnalysisPage" in workspace, "workspace routing missing"
    assert "industryData" in workspace, "workspace 应保留 industryData 兼容路由"
    assert "不展示假视频或假红人" in product, "product page no-fake copy missing"
    # R58A: IndustryDataPage 改为 1 行 re-export
    assert "DataAnalysisPage as IndustryDataPage" in industry, "IndustryDataPage 应 re-export"
    assert "getIndustryAccount" in ui_api and "listIndustryPosts" in ui_api, "industry detail/post API methods missing"
    # R58A: IndustryMatrixPanel 改为 1 行 re-export
    assert "CrossPlatformPanel as IndustryMatrixPanel" in industry_panel, "IndustryMatrixPanel 应 re-export"
    assert "getDailyOutreachDigestStatus" in ui_api and "getDailyOutreachDigestStatus" in product, "daily digest status API missing"
    assert "digestStatus" in product and "每日 Top100 候选" in monitor_panel and "08:00" in monitor_panel, "Top100 digest status UI missing"
    assert "createProjectFromOutreachSuggestion" in ui_api and "create-project" in ui_api, "suggestion project bridge API missing"
    assert "建项目+短链" in outreach_tables and "create_project" in outreach_tables, "suggestion project bridge UI missing"
    assert "RecommendationDetailDrawer" in recommendation_panel and "闭环回流" in recommendation_drawer, "recommendation evidence drawer split missing"
    assert "RecommendationOutcomeTable" in recommendation_panel and "推荐 Outcome 转化" in recommendation_outcome, "recommendation outcome table split missing"
    assert "RecommendationCandidateTable" in recommendation_panel and "产品推荐候选" in recommendation_candidate, "recommendation candidate table split missing"
    assert "RecommendationSetupForms" in recommendation_panel and "产品发布 / 推荐项目" in recommendation_setup and "历史数据 JSON / Apify 导入" in recommendation_setup, "recommendation setup forms split missing"
    assert "useProductRecommendationPanel" in recommendation_panel and "useProductRecommendationActions" in recommendation_hook and "useRecommendationEvidence" in recommendation_hook, "recommendation hook composition missing"
    assert "productRecommendationAction" in recommendation_actions and "runProductRecommendations" in recommendation_actions, "recommendation action hook split missing"
    assert "getProductRecommendationEvidence" in recommendation_evidence_hook and "recommendationEvidenceLoading" in recommendation_evidence_hook, "recommendation evidence hook split missing"
    assert "审计记录" in project_drawer and "audit_events" in project_drawer, "project detail audit UI missing"
    assert "审计记录" in link_drawer and "audit_events" in link_drawer, "link detail audit UI missing"
    assert "成本详情 / 审计" in costs_page and "getMarketingCostDetail" in costs_page and "audit_events" in costs_page, "cost detail audit UI missing"
    assert "Shopify API 状态" in attribution_page and "runShopifySync" in attribution_page and "未配置真实 Shopify Admin API" in attribution_page, "shopify sync status UI missing"
    assert "getShopifyOrderEvidence" in ui_api and "runShopifyBackfill" in ui_api, "shopify order/sync API methods missing"
    assert "API 是否工作" in settings_modules and "授权账户" in settings_modules and "SKU 录入" in settings_modules and "员工授权列表" in settings, "settings modules incomplete"
    assert "平台抓取开关" in settings_modules and "预算控制" in settings_modules and "保存限制" in settings_modules, "crawl/budget settings missing"

    print("VKPI_PHASE0B_FRONTEND_ENTRIES_SMOKE_OK")


if __name__ == "__main__":
    main()
