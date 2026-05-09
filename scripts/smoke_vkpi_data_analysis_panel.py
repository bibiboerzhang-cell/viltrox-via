#!/usr/bin/env python3
"""Smoke test data-analysis panel: layout rename, route compat, real empty state.

兼容 R58A 单文件 + R58B 拆分两种结构: 检查整个 data-analysis 目录而非单文件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")
os.environ.setdefault("DB_RUNTIME_BACKEND", "sqlite")
os.environ.setdefault("DATABASE_URL", "")

FRONTEND_ROOT = ROOT / "frontend" / "src" / "components" / "vkpi"
DA_ROOT = FRONTEND_ROOT / "pages" / "data-analysis"


def assert_file_contains(path: Path, needles: list[str], *, label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: 文件不存在 {path}")
    text = path.read_text(encoding="utf-8")
    missing = [n for n in needles if n not in text]
    if missing:
        raise AssertionError(f"{label}: {path.name} 缺少 {missing}")


def assert_file_excludes(path: Path, banned: list[str], *, label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: 文件不存在 {path}")
    text = path.read_text(encoding="utf-8")
    found = [b for b in banned if b in text]
    if found:
        raise AssertionError(f"{label}: {path.name} 不应包含品牌冲突词 {found}")


def collect_da_text() -> str:
    """聚合 data-analysis 目录下所有 .ts/.tsx 内容,用于跨文件 grep."""
    parts = []
    if not DA_ROOT.exists():
        return ""
    for p in sorted(DA_ROOT.rglob("*")):
        if p.suffix in {".ts", ".tsx"}:
            parts.append(f"\n// === FILE: {p.relative_to(DA_ROOT)} ===\n")
            parts.append(p.read_text(encoding="utf-8"))
    return "".join(parts)


def assert_text_contains(text: str, needles: list[str], *, label: str) -> None:
    missing = [n for n in needles if n not in text]
    if missing:
        raise AssertionError(f"{label}: data-analysis 目录聚合文本缺少 {missing}")


def assert_text_excludes(text: str, banned: list[str], *, label: str) -> None:
    found = [b for b in banned if b in text]
    if found:
        raise AssertionError(f"{label}: data-analysis 目录聚合文本含品牌冲突词 {found}")


def main() -> None:
    # 1. 路由命名检查
    layout_path = FRONTEND_ROOT / "layout" / "vkpiLayoutConstants.ts"
    assert_file_contains(
        layout_path,
        ["dataAnalysis", "数据分析", "icon: 'analytics'"],
        label="layout 命名",
    )

    # 2. 主页面薄壳
    page_path = FRONTEND_ROOT / "pages" / "DataAnalysisPage.tsx"
    assert_file_contains(
        page_path,
        ["CrossPlatformPanel", "title=\"数据分析\"", "Viltrox"],
        label="DataAnalysisPage 薄壳",
    )

    # 3. 兼容 re-export
    legacy_page = FRONTEND_ROOT / "pages" / "IndustryDataPage.tsx"
    assert_file_contains(
        legacy_page,
        ["DataAnalysisPage as IndustryDataPage"],
        label="IndustryDataPage re-export",
    )
    legacy_panel = FRONTEND_ROOT / "pages" / "analytics" / "IndustryMatrixPanel.tsx"
    assert_file_contains(
        legacy_panel,
        ["CrossPlatformPanel as IndustryMatrixPanel"],
        label="IndustryMatrixPanel re-export",
    )

    # 4. WorkspacePage 双兼容路由
    workspace = FRONTEND_ROOT / "pages" / "WorkspacePage.tsx"
    assert_file_contains(
        workspace,
        ["DataAnalysisPage", "dataAnalysis"],
        label="WorkspacePage 路由",
    )

    # 5. CSS 独立文件存在 + Viltrox 蓝主调
    css_path = DA_ROOT / "styles" / "data-analysis.css"
    assert_file_contains(
        css_path,
        [
            "--da-primary: #155dfc",
            "--da-accent: #F8A93C",
            "Data Analysis design tokens",
            ".da-post-card",
            ".da-filter-drawer",
            ".da-account-drawer",
        ],
        label="data-analysis.css 设计 token",
    )
    assert_file_excludes(
        css_path,
        ["Socialinsider", "SI Intelligence"],
        label="data-analysis.css 品牌",
    )

    # 6. data-analysis 目录聚合检查 (兼容 R58A 单文件 + R58B 拆分)
    da_text = collect_da_text()

    # 22 KPI 必须全部存在
    assert_text_contains(
        da_text,
        [
            "'followers'", "'followers_today'", "'followers_growth'", "'followers_growth_percent'",
            "'engagement'", "'posts'", "'views'", "'organic_value'",
            "'avg_eng_rate_followers'", "'avg_eng_rate_views'",
            "'avg_engagement'", "'avg_engagement_per_day'", "'avg_posts_per_day'",
            "'comments'", "'likes'", "'reels_views'",
            "'avg_eng_rate_impressions'", "'avg_eng_rate_reach'",
            "'reach'", "'posts_impressions'", "'shares'", "'saves'",
            # FilterDrawer Benchmark Average 选项
            "Benchmark Average",
            # 9 Tab AccountDrawer
            "ACCOUNT_TABS",
            # Viltrox 文案
            "Viltrox Marketing",
            "数据分析",
            # 不使用 Math.random 做 key fallback (用 tmp: 前缀)
            "tmp:",
        ],
        label="data-analysis 22 KPI + 关键字段",
    )
    assert_text_excludes(
        da_text,
        [
            "SI Intelligence",
            "Socialinsider-inspired",
            "Social Intelligence Controls",
            "Account Intelligence",
            "Industry Intelligence",
            "Book a demo",
            "Math.random()",
        ],
        label="data-analysis 品牌词清理",
    )

    # 7. Icon.tsx 加了 analytics
    icon_path = FRONTEND_ROOT / "shared" / "Icon.tsx"
    assert_file_contains(
        icon_path,
        ["analytics"],
        label="Icon.tsx analytics 图标",
    )

    # 8. vkpiTypes.ts 包含 dataAnalysis 类型
    types_path = FRONTEND_ROOT / "vkpiTypes.ts"
    assert_file_contains(
        types_path,
        ["dataAnalysis"],
        label="vkpiTypes dataAnalysis",
    )

    # 9. 后端真实 API 引用 (不引入新后端)
    required_apis = [
        "addIndustryAccount", "createIndustryProject",
        "getIndustryAccount", "getIndustryCrossPlatform",
        "importIndustryApifyHistory", "listIndustryAccounts",
        "listIndustryPosts", "listIndustryProjects",
        "refreshIndustryAccount",
    ]
    missing_apis = [a for a in required_apis if a not in da_text]
    if missing_apis:
        raise AssertionError(f"data-analysis 缺少 API 接入: {missing_apis}")

    print("VKPI_DATA_ANALYSIS_PANEL_SMOKE_OK")


if __name__ == "__main__":
    main()
