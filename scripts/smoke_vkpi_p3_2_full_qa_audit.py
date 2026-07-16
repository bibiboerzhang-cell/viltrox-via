#!/usr/bin/env python3
"""P3.2 UI contract smoke.

This smoke is intentionally static: it catches the regression class the user
reported first, before browser QA spends time on a broken build.
"""

from __future__ import annotations
from stdout_utils import out as stdout_out

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
    data_quality_page = "frontend/src/components/vkpi/pages/DataQualityPage.tsx"
    reports_page = "frontend/src/components/vkpi/pages/ReportsPage.tsx"
    data_analysis_root = ROOT / "frontend/src/components/vkpi/pages/data-analysis"
    da_profile_dashboard = "frontend/src/components/vkpi/pages/data-analysis/profile/ProfileDashboard.tsx"
    da_tab_index = "frontend/src/components/vkpi/pages/data-analysis/drawers/tabs/index.tsx"
    da_post_detail = "frontend/src/components/vkpi/pages/data-analysis/drawers/PostDetailDrawer.tsx"
    da_post_card = "frontend/src/components/vkpi/pages/data-analysis/shared/PostCard.tsx"
    da_panel = "frontend/src/components/vkpi/pages/data-analysis/CrossPlatformPanel.tsx"
    media_proxy = "frontend/src/components/vkpi/pages/data-analysis/utils/mediaProxy.ts"
    media_fields = "frontend/src/components/vkpi/pages/data-analysis/utils/mediaFields.ts"
    ui_api = "frontend/src/services/vkpi.ui-api.ts"

    fake_buttons: list[str] = []
    vkpi_root = ROOT / "frontend/src/components/vkpi"
    for path in sorted(vkpi_root.rglob("*.tsx")):
      for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if '<button' in stripped and 'type="button"' in stripped and "onClick" not in stripped and "disabled" not in stripped:
          fake_buttons.append(f"{path.relative_to(ROOT)}:{line_no}:{stripped}")
    if fake_buttons:
      raise AssertionError("static fake button candidates remain:\n" + "\n".join(fake_buttons[:20]))
    window_open_calls: list[str] = []
    for path in sorted(data_analysis_root.rglob("*.tsx")):
      for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "window.open(" in line:
          window_open_calls.append(f"{path.relative_to(ROOT)}:{line_no}:{line.strip()}")
    if window_open_calls:
      raise AssertionError("data-analysis external navigation must use real anchors:\n" + "\n".join(window_open_calls[:20]))

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

    assert_contains(data_quality_page, "vkpi-data-quality-actions", "data quality row actions should use compact grouped controls")
    assert_contains(data_quality_page, "<summary>更多</summary>", "secondary data-quality actions should collapse under more menu")
    assert_contains(data_quality_page, "actOnIssue(issue.id, 'resolve')", "data quality primary action must remain wired to the real endpoint")
    assert_not_contains(reports_page, "onSelectProject={() => undefined}", "reports project detail action must not be a no-op")
    assert_contains(reports_page, "onSelectProject={onSelectProject}", "reports project detail action must open the real project drawer")

    assert_contains(da_profile_dashboard, "onOpenPost: (post: Row) => void", "profile dashboard must expose single-post open handler")
    assert_contains(da_profile_dashboard, "const props = { account, snapshots, posts, accounts, onOpenPost }", "profile tabs must receive the real single-post handler")
    assert_contains(da_profile_dashboard, "onToggleCrawl(accountKey, !crawlEnabled)", "account crawl toggle must call the real account toggle action")
    assert_contains(da_tab_index, "const [showAll, setShowAll] = useState(false)", "content/posts tabs need real show-all state")
    assert_contains(da_tab_index, "showAll ? sortedPosts : sortedPosts.slice(0, 24)", "content tab must allow all posts, not only top cards")
    assert_contains(da_tab_index, "showAll ? sortedPosts : sortedPosts.slice(0, 50)", "posts table must allow all rows, not only top rows")
    assert_contains(da_tab_index, "显示全部 ${sortedPosts.length} 条", "content/posts tabs need a visible show-all action")
    assert_contains(da_tab_index, "proxiedVideoUrl(rawVideoUrl)", "post media should use the video proxy")
    assert_contains(da_tab_index, "redirectedVideoUrl(rawVideoUrl)", "post media should use the redirect fallback")
    assert_contains(da_tab_index, "视频链接失效，打开原帖", "video failure must offer the real platform fallback")
    assert_contains(da_tab_index, 'rel="noopener noreferrer"', "content tab platform links must use real external anchors")
    assert_contains(da_post_card, 'rel="noopener noreferrer"', "post cards must use real external links for original platform URLs")
    assert_contains(da_post_detail, 'rel="noopener noreferrer"', "post detail drawer must use real external links for original platform URLs")
    assert_contains(da_tab_index, "onOpenPost(post)", "post cards must open the real single-post detail drawer")
    assert_contains(da_post_detail, "onAnalyze: (post: Row) => void", "post detail drawer must expose real analysis callback")
    assert_contains(da_post_detail, "disabled={!originalUrl || analysisBusy}", "post analysis button must be disabled when real URL is missing")
    assert_contains(da_post_detail, "onClick={() => onAnalyze(post)}", "post analysis button must call real analysis")
    assert_contains(da_post_detail, "视频链接失效，请打开原帖查看。", "post detail drawer must explain video fallback")
    assert_contains(da_post_detail, "不会展示假分析", "post detail drawer must not show fake analysis copy")
    assert_contains(da_panel, "const openPost = (post: Row) =>", "data-analysis panel must define the single-post open action")
    assert_contains(da_panel, "setSelectedPost(post)", "single-post open action must set selected post state")
    assert_contains(da_panel, "const analyzePost = async (post: Row) =>", "data-analysis panel must define real post analysis action")
    assert_contains(da_panel, "analyzeDataAnalysisPostUrl(apiToken", "single-post analysis must call the backend URL analysis endpoint")
    assert_contains(ui_api, "timeoutMs: 300000", "single-post analysis timeout must allow slow real provider chains")
    assert_contains(da_panel, "<PostDetailDrawer", "data-analysis panel must render the post detail drawer")
    assert_contains(da_panel, "onAnalyze={(post) => void analyzePost(post)}", "post detail drawer must be wired to the real analysis action")
    assert_contains(media_proxy, "/api/admin/vkpi/media/image-proxy?url=", "image proxy must route platform CDN images through backend")
    assert_contains(media_proxy, "/api/admin/vkpi/media/video-proxy?url=", "video proxy must route platform CDN videos through backend")
    assert_contains(media_proxy, "/api/admin/vkpi/media/video-redirect?url=", "video redirect fallback must be available")
    assert_contains(media_fields, "url_to_video", "media field mapping must include common Apify video URL")
    assert_contains(media_fields, "source_video_url", "media field mapping must include downloaded source video URL")
    assert_contains(media_fields, "shortCodeUrl", "media field mapping must include Instagram original URLs")

    stdout_out("VKPI_P3_2_FULL_QA_AUDIT_SMOKE_OK")


if __name__ == "__main__":
    main()
