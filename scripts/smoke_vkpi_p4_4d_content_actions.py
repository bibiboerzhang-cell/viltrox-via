#!/usr/bin/env python3
"""P4.4D static gate for data-analysis content actions.

This smoke prevents regressions where post actions silently jump to the first
account, Home only exposes a Top subset, or account metrics use aggregate rows
as account-specific data.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, path: str) -> None:
    assert needle in text, f"missing {needle!r} in {path}"


def assert_not_contains(text: str, needle: str, path: str) -> None:
    assert needle not in text, f"unexpected {needle!r} in {path}"


def main() -> None:
    row_accessors_path = "frontend/src/components/vkpi/pages/data-analysis/utils/rowAccessors.ts"
    metric_helpers_path = "frontend/src/components/vkpi/pages/data-analysis/utils/metricHelpers.ts"
    post_card_path = "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx"
    home_tab_path = "frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx"
    posts_tab_path = "frontend/src/components/vkpi/pages/data-analysis/tabs/PostsTab.tsx"
    panel_path = "frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx"
    css_path = "frontend/src/components/vkpi/pages/data-analysis/styles/data-analysis.css"

    row_accessors = read(row_accessors_path)
    metric_helpers = read(metric_helpers_path)
    post_card = read(post_card_path)
    home_tab = read(home_tab_path)
    posts_tab = read(posts_tab_path)
    panel = read(panel_path)
    css = read(css_path)

    for needle in [
        "export function accountHandle",
        "ownerUsername",
        "authorUsername",
        "platformMatches",
        "const idMatches",
        "const handleMatches",
    ]:
        assert_contains(row_accessors, needle, row_accessors_path)

    assert_not_contains(row_accessors, "['handle', 'account_handle', 'username', 'display_name', 'name', 'profile_name']", row_accessors_path)
    assert_not_contains(metric_helpers, "rowString(row, ['account_id', 'id', 'profile_id'])", metric_helpers_path)
    assert_not_contains(metric_helpers, "rowString(row, ['handle', 'display_name', 'name'])", metric_helpers_path)

    for needle in [
        "const canOpenAccount = Boolean(matchedAccount && onViewAnalytics);",
        "disabled={!canOpenAccount}",
        "该帖子未匹配到账号，不能跳转账号分析",
        "disabled={!onOpenPost}",
    ]:
        assert_contains(post_card, needle, post_card_path)

    for path, text in [(home_tab_path, home_tab), (posts_tab_path, posts_tab)]:
        assert_not_contains(text, "accounts[0] || null", path)
        assert_not_contains(text, "selectedAccount || accounts[0]", path)
        assert_contains(text, "onViewAnalytics={matchedAccount ? () => onSetSelectedAccount(matchedAccount) : undefined}", path)

    for needle in [
        "onOpenPostsTab: () => void;",
        "打开完整帖子库",
        "className=\"da-inline-actions\"",
    ]:
        assert_contains(home_tab, needle, home_tab_path)

    assert_contains(panel, "onOpenPostsTab={() => setActiveSecondaryTab('Posts')}", panel_path)
    assert_contains(css, ".da-post-card__view-analytics:disabled", css_path)
    assert_contains(css, ".da-inline-actions", css_path)

    print(json.dumps({"ok": True, "marker": "VKPI_P4_4D_CONTENT_ACTIONS_SMOKE_OK"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
