#!/usr/bin/env python3
"""Smoke for P3.1G data-analysis mapping and action cleanup.

Covers the bugs this round fixed:
- Account cards must not reuse platform-level aggregates as per-account data.
- Account list API must include latest account snapshot fields.
- Posts can switch from top subset to all loaded posts.
- Post analytics opens the matched account, not always the first account.
- Visible placeholder/TODO/fake tag controls are removed from the account tabs.
"""
from __future__ import annotations
from stdout_utils import out as stdout_out

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDUSTRY_DATA = ROOT / "backend/app/services/vkpi/industry_data.py"
METRICS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/utils/metricHelpers.ts"
ROW_ACCESSORS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/utils/rowAccessors.ts"
PANEL = ROOT / "frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx"
HOME = ROOT / "frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx"
POSTS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx"
POST_CARD = ROOT / "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx"
DRAWER_TABS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx"
CSS = ROOT / "frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css"


def text(path: Path) -> str:
    return path.read_text()


def require_contains(path: Path, needle: str) -> None:
    if needle not in text(path):
        raise AssertionError(f"{path.name} missing marker: {needle}")


def require_not_contains(path: Path, needle: str) -> None:
    if needle in text(path):
        raise AssertionError(f"{path.name} still contains forbidden marker: {needle}")


def main() -> None:
    require_contains(INDUSTRY_DATA, "s.snapshot_date AS latest_snapshot_date")
    require_contains(INDUSTRY_DATA, "FROM vkpi_industry_accounts a")
    require_contains(INDUSTRY_DATA, "LEFT JOIN vkpi_industry_account_snapshots s")
    require_contains(INDUSTRY_DATA, "s.views_30d AS views_30d")
    require_contains(INDUSTRY_DATA, "s.engagement_total_30d AS engagement_total_30d")

    require_contains(ROW_ACCESSORS, "export function findAccountForPost")
    require_contains(METRICS, "findAccountForPost(post, [account])")
    require_not_contains(METRICS, "rowPlatform === platform")
    require_contains(METRICS, "return views ?? null")
    require_contains(METRICS, "estimated_organic_value_cents")
    require_contains(METRICS, "return cents !== null ? cents / 100 : null")

    require_contains(PANEL, "const [accountPosts, setAccountPosts]")
    require_contains(PANEL, "setAccountPosts(result.posts || [])")
    require_contains(PANEL, "posts={accountPosts}")
    require_contains(PANEL, "selectAccountForAnalysis")
    require_contains(PANEL, "accounts={visibleAccounts}")

    require_contains(HOME, "const [showAllPosts, setShowAllPosts]")
    require_contains(HOME, "显示全部")
    require_contains(HOME, "findAccountForPost(post, accounts)")
    require_contains(HOME, "postsForAccount(posts, account).length")

    require_contains(POSTS, "const [showAll, setShowAll]")
    require_contains(POSTS, "visiblePosts.map")
    require_contains(POSTS, "findAccountForPost(post, accounts)")
    require_not_contains(POSTS, "显示前 30 条 · 累积更多真实数据后启用分页")

    require_not_contains(POST_CARD, "Tag post ▾")
    require_contains(POST_CARD, "findAccountForPost(post, accounts)")
    require_contains(POST_CARD, "accountAvatar")

    require_not_contains(DRAWER_TABS, "TODO")
    require_contains(DRAWER_TABS, "显示全部")
    require_contains(DRAWER_TABS, "MiniSnapshotTable")
    require_contains(DRAWER_TABS, "打开平台")

    require_contains(CSS, ".da-post-card__avatar img")

    stdout_out("VKPI_P3_1G_DATA_MAPPING_ACTIONS_SMOKE_OK")


if __name__ == "__main__":
    main()
