#!/usr/bin/env python3
"""Static media UX contract for P3.13A.

This catches the regression class reported in browser QA: avatars, thumbnails,
video URLs, platform-open links, and show-all controls diverging across the
data-analysis pages.
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(path: str, needle: str, message: str) -> None:
    text = read(path)
    if needle not in text:
        raise AssertionError(f"{message}: {path} missing {needle!r}")


def main() -> None:
    media_fields = "frontend/src/components/vkpi/pages/data-analysis/utils/mediaFields.ts"
    post_card = "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx"
    home_tab = "frontend/src/components/vkpi/pages/data-analysis/tabs/HomeTab.tsx"
    profile_dashboard = "frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx"
    drawer = "frontend/src/components/vkpi/pages/data-analysis/drawers/AccountDrawer.tsx"
    drawer_tabs = "frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx"
    detail_panel = "frontend/src/components/vkpi/panels/KolDetailPanel.tsx"
    data_utils = "frontend/src/components/vkpi/shared/vkpiDataUtils.ts"

    for needle in [
        "POST_THUMBNAIL_KEYS",
        "display_url",
        "videoUrlNoWaterMark",
        "video_url_no_watermark",
        "downloadUrl",
        "ACCOUNT_AVATAR_KEYS",
        "ACCOUNT_PROFILE_URL_KEYS",
    ]:
        require(media_fields, needle, "media helper must cover common platform fields")

    require(post_card, "postThumbnailUrl(post)", "post cards must use unified thumbnail mapping")
    require(post_card, "postVideoUrl(post)", "post cards must use unified video mapping")
    require(post_card, "accountAvatarUrl(matchedAccount)", "post cards must use unified avatar mapping")
    require(post_card, "redirectedVideoUrl(rawVideoUrl)", "post cards must have redirect fallback")
    require(post_card, "打开原帖", "post cards must expose original post link")

    require(home_tab, "accountAvatarUrl(account)", "home account cards must use unified avatar mapping")
    require(profile_dashboard, "accountAvatarUrl(account)", "profile dashboard must use unified avatar mapping")
    require(profile_dashboard, "accountProfileUrl(account)", "profile dashboard must use unified profile URL mapping")
    require(drawer, "accountAvatarUrl(account)", "account drawer must use unified avatar mapping")
    require(drawer, "accountProfileUrl(account)", "account drawer must use unified profile URL mapping")

    require(drawer_tabs, "postThumbnailUrl(post)", "drawer content tab must use unified thumbnail mapping")
    require(drawer_tabs, "postVideoUrl(post)", "drawer content tab must use unified video mapping")
    require(drawer_tabs, "redirectedVideoUrl(rawVideoUrl)", "drawer content tab must have redirect fallback")
    require(drawer_tabs, "显示全部", "drawer content/posts tabs must not be hard-capped to top-only")
    require(drawer_tabs, "postPlatformUrl(post)", "drawer posts table must use unified platform URL mapping")

    require(detail_panel, "showAllContent", "KOL detail panel must expose real show-all content state")
    require(detail_panel, "打开平台内容", "KOL detail content cards must open platform content")
    require(data_utils, "post.videoUrlNoWaterMark", "KOL detail mapping must accept top-level no-watermark video URL")
    require(data_utils, "post.downloadUrl", "KOL detail mapping must accept download URL")
    require(data_utils, "post.previewUrl", "KOL detail mapping must accept preview thumbnail URL")

    print("VKPI_P3_13A_MEDIA_CONTRACT_SMOKE_OK")


if __name__ == "__main__":
    main()
