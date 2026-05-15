#!/usr/bin/env python3
"""P4 Step34 static contract: media lists show loaded-window truth.

The UI must not imply infinite historical coverage. It should clearly say how
many posts were loaded and that 500 is the current backend window cap.
"""
from __future__ import annotations

import json
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
    home_rel = "frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx"
    posts_rel = "frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx"
    drawer_tabs_rel = "frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx"
    css_rel = "frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css"
    service_rel = "frontend/src/services/vkpi.ui-api.ts"
    router_rel = "backend/app/api/routers/vkpi_industry_automation.py"

    home = read(home_rel)
    for needle in [
        "const loadWindowNote = posts.length >= 500",
        "已加载 500 条上限；更多历史需要分页",
        "已加载 ${posts.length} 条内容",
        "<span className=\"da-load-window-note\">{loadWindowNote}</span>",
        "显示全部 ${posts.length} 条",
        "打开完整帖子库",
    ]:
        require(home, needle, home_rel)

    posts = read(posts_rel)
    for needle in [
        "const loadWindowNote = posts.length >= 500",
        "已加载 500 条上限；更多历史需要分页",
        "共 {sorted.length} 条 / 全部 {posts.length} · {loadWindowNote}",
        "显示全部 ${sorted.length} 条",
        "查看全部已加载内容",
    ]:
        require(posts, needle, posts_rel)

    drawer_tabs = read(drawer_tabs_rel)
    for needle in [
        "const loadWindowNote = sortedPosts.length >= 500",
        "已加载 500 条上限；更多历史需要分页",
        "显示 {visiblePosts.length} / {sortedPosts.length} 条内容 · {loadWindowNote}",
        "显示全部 ${sortedPosts.length} 条",
    ]:
        require(drawer_tabs, needle, drawer_tabs_rel)

    css = read(css_rel)
    for needle in [
        ".da-load-window-note",
        ".da-load-window-hint",
        "font-weight: 700;",
    ]:
        require(css, needle, css_rel)

    service = read(service_rel)
    require(service, "export async function listIndustryPosts(token: string, projectId: string, limit = 500)", service_rel)
    require(service, "export async function getIndustryAccount(token: string, accountId: string, limit = 500)", service_rel)

    router = read(router_rel)
    require(router, "limit: int = Query(default=500, ge=1, le=500)", router_rel)

    print(json.dumps({"ok": True, "marker": "VKPI_P4_34_MEDIA_LOADED_COUNT_CONTRACT_OK"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
