#!/usr/bin/env python3
"""Smoke test R58B: data-analysis 拆分为 14 个独立组件 + 6 Tab 实装.

验证:
1. utils/shared/drawers/tabs 子目录结构正确
2. 14 个新组件文件全部存在
3. 主 CrossPlatformPanel.tsx 行数大幅减少 (1199 → < 500)
4. 6 个 Tab 文件存在
5. CrossPlatformPanel 引用所有 6 个 Tab 组件
6. 各组件 import 路径正确
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")

DA_ROOT = ROOT / "frontend" / "src" / "components" / "vkpi" / "pages" / "data-analysis"


def assert_file_exists(path: Path, *, label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: 文件不存在 {path}")


def assert_file_contains(path: Path, needles: list[str], *, label: str) -> None:
    if not path.exists():
        raise AssertionError(f"{label}: 文件不存在 {path}")
    text = path.read_text(encoding="utf-8")
    missing = [n for n in needles if n not in text]
    if missing:
        raise AssertionError(f"{label}: {path.name} 缺少 {missing}")


def main() -> None:
    # 1. 子目录结构
    for subdir in ["utils", "shared", "drawers", "tabs", "styles"]:
        d = DA_ROOT / subdir
        if not d.is_dir():
            raise AssertionError(f"R58B 子目录不存在: {d}")

    # 2. utils 5 个文件
    utils_files = ["types.ts", "kpiOptions.ts", "rowAccessors.ts", "platformHelpers.ts", "metricHelpers.ts"]
    for f in utils_files:
        assert_file_exists(DA_ROOT / "utils" / f, label=f"utils/{f}")

    # 3. shared 8 个组件
    shared_files = [
        "BigNumberCard.tsx", "DaCard.tsx", "EmptyState.tsx",
        "TimeSeriesChart.tsx", "BarChart.tsx", "DonutChart.tsx",
        "PostingTimesHeatmap.tsx", "PostCard.tsx",
    ]
    for f in shared_files:
        assert_file_exists(DA_ROOT / "shared" / f, label=f"shared/{f}")

    # 4. drawers 2 个
    for f in ["FilterDrawer.tsx", "AccountDrawer.tsx"]:
        assert_file_exists(DA_ROOT / "drawers" / f, label=f"drawers/{f}")

    # 5. tabs 6 个
    tab_files = [
        "HomeTab.tsx", "BenchmarksTab.tsx", "PostsTab.tsx",
        "PillarsTab.tsx", "SentimentTab.tsx", "TopicTrackingTab.tsx",
    ]
    for f in tab_files:
        assert_file_exists(DA_ROOT / "tabs" / f, label=f"tabs/{f}")

    # 6. 主面板瘦身 (1199 → 必须 < 500)
    panel_path = DA_ROOT / "CrossPlatformPanel.tsx"
    assert_file_exists(panel_path, label="主面板")
    line_count = sum(1 for _ in panel_path.read_text(encoding="utf-8").splitlines())
    if line_count > 500:
        raise AssertionError(
            f"R58B 拆分失败: CrossPlatformPanel.tsx 仍有 {line_count} 行 (要求 < 500)"
        )

    # 7. 主面板必须 import 所有 6 个 Tab
    assert_file_contains(
        panel_path,
        [
            "import { HomeTab }",
            "import { BenchmarksTab }",
            "import { PostsTab }",
            "import { PillarsTab }",
            "import { SentimentTab }",
            "import { TopicTrackingTab }",
            # 必须有 Tab 切换路由
            "activeSecondaryTab",
            "renderTab",
        ],
        label="主面板 Tab 实装",
    )

    # 8. SECONDARY_TABS 6 个
    types_path = DA_ROOT / "utils" / "types.ts"
    assert_file_contains(
        types_path,
        [
            "'Home'", "'Benchmarks'", "'Posts'",
            "'Pillars'", "'Sentiment'", "'Topic Tracking'",
        ],
        label="SECONDARY_TABS 定义",
    )

    # 9. Sentiment / Topic Tracking 必须真实空态(等 Phase 2/3)
    sentiment_path = DA_ROOT / "tabs" / "SentimentTab.tsx"
    assert_file_contains(
        sentiment_path,
        ["Phase 3", "EmptyState"],
        label="SentimentTab 真实空态",
    )
    topic_path = DA_ROOT / "tabs" / "TopicTrackingTab.tsx"
    assert_file_contains(
        topic_path,
        ["Phase 2", "EmptyState"],
        label="TopicTrackingTab 真实空态",
    )

    # 10. 22 KPI 集中在 utils/kpiOptions.ts
    kpi_path = DA_ROOT / "utils" / "kpiOptions.ts"
    assert_file_contains(
        kpi_path,
        ["KPI_OPTIONS", "DEFAULT_KPIS", "'followers'", "'organic_value'", "'reels_views'"],
        label="kpiOptions 22 KPI 集中管理",
    )

    # 11. 稳定 ID 在 utils/rowAccessors (不用 Math.random)
    accessor_path = DA_ROOT / "utils" / "rowAccessors.ts"
    assert_file_contains(
        accessor_path,
        ["accountId", "postKey", "tmp:"],
        label="稳定 ID 生成",
    )
    text = accessor_path.read_text(encoding="utf-8")
    if "Math.random()" in text:
        raise AssertionError("rowAccessors 不应该使用 Math.random")

    # 12. 各 Tab 不写品牌冲突词
    banned = ["SI Intelligence", "Socialinsider-inspired", "Book a demo"]
    for f in tab_files:
        p = DA_ROOT / "tabs" / f
        text = p.read_text(encoding="utf-8")
        found = [b for b in banned if b in text]
        if found:
            raise AssertionError(f"tabs/{f} 品牌词残留: {found}")

    print("VKPI_DATA_ANALYSIS_R58B_SMOKE_OK")


if __name__ == "__main__":
    main()
