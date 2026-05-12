#!/usr/bin/env python3
"""P3.2 UI contract smoke.

This smoke is intentionally static: it catches the regression class the user
reported first, before browser QA spends time on a broken build.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(path: str, needle: str, message: str) -> None:
    text = read(path)
    if needle not in text:
        raise AssertionError(f"{message}: {path} missing {needle!r}")


def assert_not_contains(path: str, needle: str, message: str) -> None:
    text = read(path)
    if needle in text:
        raise AssertionError(f"{message}: {path} still contains {needle!r}")


def main() -> None:
    detail_section = "frontend/src/components/vkpi/shared/DetailSection.tsx"
    card_header = "frontend/src/components/vkpi/shared/CardHeader.tsx"
    discover_page = "frontend/src/components/vkpi/pages/DiscoverPage.tsx"
    data_utils = "frontend/src/components/vkpi/shared/vkpiDataUtils.ts"
    detail_panel = "frontend/src/components/vkpi/panels/KolDetailPanel.tsx"
    avatar = "frontend/src/components/vkpi/shared/Avatar.tsx"
    types = "frontend/src/components/vkpi/vkpiTypes.ts"

    fake_buttons: list[str] = []
    vkpi_root = ROOT / "frontend/src/components/vkpi"
    for path in sorted(vkpi_root.rglob("*.tsx")):
      for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if '<button' in stripped and 'type="button"' in stripped and "onClick" not in stripped and "disabled" not in stripped:
          fake_buttons.append(f"{path.relative_to(ROOT)}:{line_no}:{stripped}")
    if fake_buttons:
      raise AssertionError("static fake button candidates remain:\n" + "\n".join(fake_buttons[:20]))

    assert_contains(detail_section, "onAction?: () => void", "detail action must have a handler")
    assert_contains(detail_section, "action && onAction", "detail action must not render fake buttons")
    assert_contains(card_header, "onAction?: () => void", "card action must have a handler")
    assert_contains(card_header, "action && onAction", "card action must not render fake buttons")

    assert_not_contains(discover_page, "maxPosts: 1", "lookup must not cap refreshed content to one post")
    assert_contains(discover_page, "maxPosts: 24", "lookup should request enough posts for detail QA")

    assert_contains(types, "url?: string", "content assets need platform URL")
    assert_contains(types, "videoUrl?: string", "content assets need video URL")
    assert_contains(data_utils, "posts.slice(0, 24)", "lookup detail should keep more than top 6 posts")
    assert_contains(data_utils, "rawPosts.slice(0, 24)", "profile detail should keep more than top 6 posts")
    assert_contains(data_utils, "comments.slice(0, 20)", "lookup detail should keep more comments")
    assert_contains(data_utils, "profile.messages || []).slice(0, 20)", "profile detail should keep more messages")
    assert_contains(data_utils, "videoUrlNoWaterMark", "video URL mapping should include common Apify fields")
    assert_contains(data_utils, "shortCodeUrl", "platform URL mapping should include Instagram short code URLs")

    assert_contains(avatar, "proxiedImageUrl", "avatar should route platform CDN images through media proxy")
    assert_contains(detail_panel, "showAllContent", "content section should have real show-all state")
    assert_contains(detail_panel, "showAllMessages", "message section should have real show-all state")
    assert_contains(detail_panel, "ContentThumbnail src={content.imageUrl} videoUrl={content.videoUrl}", "content cards should render mapped video URLs")
    assert_contains(detail_panel, "打开平台内容", "content cards should link back to platform content")
    assert_not_contains(detail_panel, "aria-label=\"上一个红人\"", "dead previous button should be removed")
    assert_not_contains(detail_panel, "aria-label=\"下一个红人\"", "dead next button should be removed")
    assert_not_contains(detail_panel, ">编辑</button>", "dead edit button should be removed")

    print("VKPI_P3_2_FULL_QA_AUDIT_SMOKE_OK")


if __name__ == "__main__":
    main()
