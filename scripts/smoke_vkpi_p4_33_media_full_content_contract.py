#!/usr/bin/env python3
"""P4 Step33 static contract: media views must expose all-loaded content, not top-only.

This smoke is intentionally offline/static. It verifies the frontend/backend contract
that the data-analysis page loads the maximum supported post window and keeps the
Top/All toggles plus post detail entries wired.
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
    service_rel = "frontend/src/services/vkpi.ui-api.ts"
    panel_rel = "frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx"
    home_rel = "frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx"
    posts_rel = "frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx"
    drawer_tabs_rel = "frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx"
    router_rel = "backend/app/api/routers/vkpi_industry_automation.py"
    industry_rel = "backend/app/services/vkpi/industry_data.py"

    service = read(service_rel)
    for needle in [
        "export async function getIndustryAccount(token: string, accountId: string, limit = 500)",
        "accounts/${encodeURIComponent(accountId)}?limit=",
        "export async function listIndustryPosts(token: string, projectId: string, limit = 500)",
    ]:
        require(service, needle, service_rel)

    panel = read(panel_rel)
    require(panel, "listIndustryPosts(apiToken, resolvedProjectId, 500)", panel_rel)
    require(panel, "posts={visiblePosts}", panel_rel)
    require(panel, "onOpenPost={openPost}", panel_rel)

    home = read(home_rel)
    for needle in [
        "const [showAllPosts, setShowAllPosts] = useState(false);",
        "showAllPosts ? posts : posts.slice(0, 3)",
        "显示全部 ${posts.length} 条",
        "打开完整帖子库",
        "onOpenPost={onOpenPost}",
    ]:
        require(home, needle, home_rel)

    posts = read(posts_rel)
    for needle in [
        "const [showAll, setShowAll] = useState(false);",
        "showAll ? sorted : sorted.slice(0, 30)",
        "显示全部 ${sorted.length} 条",
        "共 {sorted.length} 条 / 全部 {posts.length}",
        "onOpenPost={onOpenPost}",
    ]:
        require(posts, needle, posts_rel)

    drawer_tabs = read(drawer_tabs_rel)
    for needle in [
        "const [showAll, setShowAll] = useState(false);",
        "showAll ? sortedPosts : sortedPosts.slice(0, 24)",
        "显示全部 ${sortedPosts.length} 条",
        "单帖详情 / 分析",
        "onOpenPost(post)",
    ]:
        require(drawer_tabs, needle, drawer_tabs_rel)

    router = read(router_rel)
    for needle in [
        "limit: int = Query(default=500, ge=1, le=500)",
        "industry_data.get_account(account_id, post_limit=limit)",
        "limit: int = Query(default=100, ge=1, le=500)",
    ]:
        require(router, needle, router_rel)

    industry = read(industry_rel)
    for needle in [
        "def get_account(account_id: int, *, post_limit: int = 500)",
        "safe_post_limit = max(1, min(500, int(post_limit or 500)))",
        "WHERE account_id=? ORDER BY published_at DESC, id DESC LIMIT ?",
        "max(1, min(500, int(limit or 100)))",
    ]:
        require(industry, needle, industry_rel)

    print(json.dumps({"ok": True, "marker": "VKPI_P4_33_MEDIA_FULL_CONTENT_CONTRACT_OK"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
