#!/usr/bin/env python3
"""P4.4A static gate for data-analysis media UX.

This smoke does not call external platforms. It verifies the UI contract that
real media URLs can be discovered from normalized columns and raw payloads, and
that account/post tabs can open the single-post drawer instead of leaving dead
buttons.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(text: str, needle: str, path: str) -> None:
    assert needle in text, f"missing {needle!r} in {path}"


def main() -> None:
    media_fields_path = "frontend/src/components/vkpi/pages/data-analysis/utils/mediaFields.ts"
    account_drawer_path = "frontend/src/components/vkpi/pages/data-analysis/drawers/AccountDrawer.tsx"
    profile_dashboard_path = "frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx"
    post_detail_path = "frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx"
    post_card_path = "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx"
    media_router_path = "backend/app/api/routers/media.py"

    media_fields = read(media_fields_path)
    account_drawer = read(account_drawer_path)
    profile_dashboard = read(profile_dashboard_path)
    post_detail = read(post_detail_path)
    post_card = read(post_card_path)
    media_router = read(media_router_path)

    for needle in [
        "RAW_JSON_KEYS",
        "raw_platform_data",
        "metadata_json",
        "findNestedString",
        "mediaString(post, POST_THUMBNAIL_KEYS)",
        "mediaString(post, POST_VIDEO_KEYS)",
        "mediaString(post, POST_PLATFORM_URL_KEYS)",
        "mediaString(account, ACCOUNT_AVATAR_KEYS)",
    ]:
        assert_contains(media_fields, needle, media_fields_path)

    for needle in [
        "onOpenPost?: (post: Row) => void;",
        "onOpenPost,",
        "<ContentTab account={account} posts={posts} onOpenPost={onOpenPost} />",
        "<PostsTab account={account} posts={posts} onOpenPost={onOpenPost} />",
    ]:
        assert_contains(account_drawer, needle, account_drawer_path)

    for needle in [
        "onOpenPost: (post: Row) => void;",
        "const props = { account, snapshots, posts, accounts, onOpenPost };",
        "return <ContentTab {...props} />;",
        "return <PostsTab {...props} />;",
    ]:
        assert_contains(profile_dashboard, needle, profile_dashboard_path)

    for needle in [
        "playbackVideoCandidates(postVideoUrls(post))",
        "videoCandidateIndex < videoCandidates.length - 1",
        "打开原帖",
        "运行单帖分析",
        "analysisError",
    ]:
        assert_contains(post_detail, needle, post_detail_path)

    for needle in [
        "playbackVideoCandidates(postVideoUrls(post))",
        "videoCandidateIndex < videoCandidates.length - 1",
        "onOpenPost?.(post)",
        "视频链接失效，打开原帖",
    ]:
        assert_contains(post_card, needle, post_card_path)

    for needle in [
        "/api/admin/vkpi/media/image-proxy",
        "/api/admin/vkpi/media/video-proxy",
        "/api/admin/vkpi/media/video-redirect",
        "VKPI_VIDEO_PROXY_MAX_BYTES",
        "_allowed_external_video_url",
    ]:
        assert_contains(media_router, needle, media_router_path)

    print(json.dumps({"ok": True, "marker": "VKPI_P4_4_MEDIA_UX_CONTRACT_SMOKE_OK"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
