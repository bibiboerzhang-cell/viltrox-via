#!/usr/bin/env python3
"""P3.13C contract smoke: single-post detail drawer and analysis entry stay wired.

This smoke is intentionally static. The live browser click needs a logged-in JWT;
this contract prevents the UI from regressing into fake buttons or disconnected
analysis controls.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    if not path.exists():
        raise AssertionError(f"missing file: {rel}")
    return path.read_text(encoding="utf-8")


def require(text: str, needle: str, rel: str) -> None:
    if needle not in text:
        raise AssertionError(f"{rel} missing required contract: {needle}")


def main() -> None:
    drawer_rel = "frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx"
    panel_rel = "frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx"
    service_rel = "frontend/src/services/vkpi.ui-api.ts"
    card_rel = "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx"
    drawer_tabs_rel = "frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx"
    css_rel = "frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css"

    drawer = read(drawer_rel)
    for needle in [
        "export function PostDetailDrawer",
        "运行单帖分析",
        "打开原帖",
        "playbackVideoCandidates",
        "postVideoUrls(post)",
        "videoCandidateIndex < videoCandidates.length - 1",
        "video链接失效".replace("video", "视频"),
        "onAnalyze(post)",
        "Views",
        "Engagement",
    ]:
        require(drawer, needle, drawer_rel)

    panel = read(panel_rel)
    for needle in [
        "analyzeDataAnalysisPostUrl",
        "PostDetailDrawer",
        "const openPost = (post: Row)",
        "const analyzePost = async (post: Row)",
        "onOpenPost={openPost}",
        "post={selectedPost}",
        "onAnalyze={(post) => void analyzePost(post)}",
    ]:
        require(panel, needle, panel_rel)

    service = read(service_rel)
    for needle in [
        "export async function analyzeDataAnalysisPostUrl",
        "/api/admin/kol/tools/analyze-url",
        "creator_handle",
        "timeoutMs: 180000",
    ]:
        require(service, needle, service_rel)

    card = read(card_rel)
    for needle in ["onOpenPost?:", "单帖详情", "onOpenPost?.(post)"]:
        require(card, needle, card_rel)

    drawer_tabs = read(drawer_tabs_rel)
    for needle in ["onOpenPost?:", "单帖详情 / 分析", "onOpenPost(post)"]:
        require(drawer_tabs, needle, drawer_tabs_rel)

    css = read(css_rel)
    for needle in [".da-post-detail", ".da-post-detail__media", ".da-post-detail__metrics", ".da-post-card__actions"]:
        require(css, needle, css_rel)

    stdout_out("VKPI_P3_13C_POST_DETAIL_CONTRACT_OK")


if __name__ == "__main__":
    main()
