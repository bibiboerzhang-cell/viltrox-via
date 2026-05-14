#!/usr/bin/env python3
"""P4.4C smoke: post analysis result stays structured and honest."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def assert_contains(path: Path, needle: str, message: str) -> None:
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise AssertionError(f"{message}: missing {needle!r} in {path}")


def main() -> None:
    drawer = ROOT / "frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx"
    css = ROOT / "frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css"

    assert_contains(drawer, "interface AnalysisView", "post analysis should use a typed view model")
    assert_contains(drawer, "analysisView(analysis", "post analysis should normalize provider response")
    assert_contains(drawer, "真实 URL 分析处理中", "busy state should explain real provider latency")
    assert_contains(drawer, "Status: {structuredAnalysis.status}", "status should be rendered as metadata")
    assert_contains(drawer, "Score: {structuredAnalysis.qualityScore}", "quality score should be rendered as metadata")
    assert_contains(drawer, "Providers: {structuredAnalysis.providers.join(' + ')}", "providers should be visible")
    assert_contains(drawer, "<summary>查看原始返回</summary>", "raw response should be available but collapsed")
    assert_contains(drawer, "不会展示假分析", "empty state must remain honest")

    assert_contains(css, ".da-post-analysis__meta", "structured analysis metadata should be styled")
    assert_contains(css, ".da-post-analysis__card", "structured analysis cards should be styled")
    assert_contains(css, ".da-post-analysis__raw", "raw response details should be styled")

    print("VKPI_P4_4C_POST_ANALYSIS_DISPLAY_SMOKE_OK")


if __name__ == "__main__":
    main()
