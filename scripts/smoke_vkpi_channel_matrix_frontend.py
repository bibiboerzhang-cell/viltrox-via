#!/usr/bin/env python3
"""Static smoke for the employee-platform official channel matrix."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def main() -> None:
    channels_page = read("frontend/src/components/vkpi/pages/ChannelsPage.tsx")
    router = read("backend/app/api/routers/vkpi_operations.py")
    media_router = read("backend/app/api/routers/media.py")
    channel_service = read("backend/app/services/vkpi/channels.py")
    refill_service = read("backend/app/services/vkpi/channel_refill.py")
    media_cache_service = read("backend/app/services/vkpi/media_cache.py")
    gaps_service = read("backend/app/services/vkpi/channel_gaps.py")
    youtube_crawler = read("backend/app/services/vkpi/industry_crawlers/youtube_crawler.py")
    instagram_crawler = read("backend/app/services/vkpi/industry_crawlers/instagram_crawler.py")
    tiktok_crawler = read("backend/app/services/vkpi/industry_crawlers/tiktok_crawler.py")
    matrix = read("frontend/src/components/vkpi/pages/channels/ChannelPlatformMatrix.tsx")
    staff = read("frontend/src/components/vkpi/pages/channels/ChannelStaffProgress.tsx")
    gaps = read("frontend/src/components/vkpi/pages/channels/ChannelGapPanel.tsx")
    accounts = read("frontend/src/components/vkpi/pages/channels/ChannelAccountList.tsx")
    content = read("frontend/src/components/vkpi/pages/channels/ChannelContentList.tsx")
    gap_hook = read("frontend/src/components/vkpi/pages/channels/useOfficialChannelGaps.ts")
    hook = read("frontend/src/components/vkpi/pages/channels/useOfficialChannelMatrix.ts")
    types = read("frontend/src/components/vkpi/pages/channels/channelTypes.ts")
    css = read("frontend/src/components/vkpi/pages/channels/channels.css")
    staff_css = read("frontend/src/components/vkpi/pages/channels/channelStaff.css")
    gaps_css = read("frontend/src/components/vkpi/pages/channels/channelGaps.css")
    account_css = read("frontend/src/components/vkpi/pages/channels/channelAccounts.css")
    content_css = read("frontend/src/components/vkpi/pages/channels/channelContent.css")
    media_proxy = read("frontend/src/components/vkpi/shared/mediaProxy.ts")
    api = read("frontend/src/services/vkpi.ui-api.ts")
    doc = read("docs/qa/official-channel-matrix-execution.md")

    assert "ChannelPlatformMatrix" in channels_page, "ChannelsPage must render platform matrix"
    assert "ChannelStaffProgress" in channels_page, "ChannelsPage must render staff progress"
    assert "compactMode" in channels_page and "vkpi-action-card--team" in channels_page, "staff progress must live in the team matrix card"
    assert "ChannelGapPanel" in channels_page and "useOfficialChannelGaps" in channels_page, "gap panel wiring missing"
    assert "ChannelAccountList" in channels_page, "ChannelsPage must render account cards"
    assert "ChannelContentList" in channels_page, "ChannelsPage must render content cards"
    assert "useOfficialChannelMatrix" in channels_page, "ChannelsPage must use official matrix hook"
    assert "selectedPlatform" in channels_page and "selectedPlatformData" in channels_page and "visiblePlatformData" in channels_page, "platform filter wiring missing"
    assert "selectedStaffId" in channels_page and "selectStaff" in channels_page, "staff filter wiring missing"
    assert "selectedAccountId" in channels_page and "selectAccount" in channels_page, "account selection wiring missing"
    assert "getOfficialChannelMatrix" in api and "official-matrix" in api, "official matrix API client missing"
    assert "官方账号矩阵" in matrix and "平台总览" in matrix, "matrix header missing"
    assert "summaryMetrics" in matrix and "篇均播放" in matrix and "已同步" in matrix, "matrix summary calculations missing"
    assert "followersDelta" in matrix and "deltaLabel" in matrix and "较上次" in matrix, "platform follower delta indicator missing"
    assert "基线保护" in matrix and "baselineProtected" in matrix and "is-protected" in matrix, "platform matrix must surface baseline protection"
    assert "vkpi-channel-platform-card" in matrix and "vkpi-channel-avatar-stack" in matrix, "platform card/avatar stack missing"
    assert "负责人层" in staff and "员工账号进度" in staff and "负责人进度" in staff, "staff progress header missing"
    assert "基线保护" in staff and "baselineProtected" in staff, "staff progress must surface baseline protection"
    assert "summaryMetrics" in staff and "篇均播放" in staff and "负责人" in staff, "staff summary calculations missing"
    assert "staffAvatarUrl" in staff and "topAccount" in staff and "platformCount" in staff, "staff progress metrics missing"
    assert "补数清单" in gaps and "素材与证据缺口" in gaps and "issueLabels" in gaps, "gap panel header missing"
    assert "providerReady" in gaps and "recommendedAction" in gaps, "gap panel provider state missing"
    assert "autoRefillSupported" in gaps and "抓取无结果" in gaps and "暂未接入自动补抓" in gaps, "gap panel must explain no-result/unsupported states"
    assert "账号层" in accounts and "vkpi-channel-account-card" in accounts, "account card UI missing"
    assert "基线保护" in accounts and "baselineProtected" in accounts and "hasProtectedField" in accounts, "account cards must replace protected +0 with baseline protection"
    assert "avatarUrl" in accounts and "totalViews" in accounts and "engagementRate" in accounts, "account card metrics missing"
    assert "lastSyncError" in accounts and "抓取无结果" in accounts, "account card sync state label missing"
    assert "内容层" in content and "vkpi-channel-content-card" in content, "content card UI missing"
    assert "mediaUrl" in content and "打开原帖" in content and "赞" in content, "content card fields missing"
    assert "MediaSlot" in content and "proxiedImageUrl" in content and "proxiedVideoUrl" in content, "content media proxy missing"
    assert "likelyVideoUrl" in content and "<video" in content and "待缓存" in content, "content video/fallback missing"
    assert "OfficialChannelPlatform" in types and "OfficialChannelAccount" in types and "ChannelContentPost" in types, "typed contract missing"
    assert "staffId" in types and "staffName" in types and "staffRole" in types, "staff typed contract missing"
    assert "ChannelGapAccount" in types and "ChannelGapIssue" in types and "autoRefillSupported" in types, "gap typed contract missing"
    assert "mapPlatform" in hook and "mapAccount" in hook and "mapPost" in hook, "matrix mapper missing"
    assert "getOfficialChannelGapReport" in api and "official-gap-report" in api, "gap API client missing"
    assert "mapAccount" in gap_hook and "mapIssue" in gap_hook, "gap mapper missing"
    assert "official-gap-report" in router and "channel_gaps.official_gap_report" in router, "gap route missing"
    assert "channel_refill.sync_channel_snapshot" in channel_service, "sync-now must call refill service"
    assert "attributionType" in channel_service and "accountName" in channel_service and "staffName" in channel_service and "mediaUrl" in channel_service, "official views evidence attribution fields missing"
    assert "def sync_channel_snapshot" in refill_service and "_sync_instagram" in refill_service and "_sync_tiktok" in refill_service and "_sync_youtube" in refill_service, "provider refill service incomplete"
    assert "_sync_facebook" in refill_service and "_sync_reddit" in refill_service and "_sync_x" in refill_service and "_quality_summary" in refill_service, "Facebook/Reddit/X refill path missing"
    assert "platform=\"facebook\"" in refill_service and "\"comment\"" in refill_service, "Facebook/Reddit post filtering must be platform-aware"
    assert "_audit_staff_id" in refill_service and "channel.get(\"staff_id\")" in refill_service, "refill audit must use bound staff id"
    assert "_instagram_profile_has_data" in refill_service and "_tiktok_items_have_data" in refill_service and "no_results" in refill_service, "provider refill quality gate missing"
    assert "def official_gap_report" in gaps_service and "missing_avatar" in gaps_service and "missing_media" in gaps_service, "gap service incomplete"
    assert "no_provider_results" in gaps_service and "AUTO_REFILL_PLATFORMS" in gaps_service and "facebook" in gaps_service and "reddit" in gaps_service and '"x"' in gaps_service, "gap service state separation missing"
    assert "tiktokcdn.com" in media_router and "apifyusercontent.com" in media_router, "media image proxy allowlist incomplete"
    assert "api/vkpi-media/image-cache" in media_router and "cached_image_file" in media_router, "local media cache route missing"
    assert "videoMeta" in channel_service and "coverUrl" in channel_service and "originalCoverUrl" in channel_service, "TikTok cover mapping missing"
    assert "cached_image_url" in channel_service and "_cached_media_url" in channel_service, "matrix must prefer local cached images"
    assert "prewarm_official_media_cache" in refill_service and "media_cache" in refill_service, "refill must prewarm media cache"
    assert "def prewarm_official_media_cache" in media_cache_service and "MAX_IMAGE_BYTES" in media_cache_service, "media cache service missing safety bounds"
    assert ".redd.it" in media_cache_service and "html.unescape" in media_cache_service, "Reddit media cache normalization missing"
    assert "nextPageToken" in youtube_crawler and "DEFAULT_MAX_CHANNEL_VIDEOS" in youtube_crawler, "YouTube full-baseline pagination missing"
    assert "DEFAULT_MAX_POST_RESULTS" in instagram_crawler and "instagram-scraper" in instagram_crawler, "Instagram baseline must use the posts scraper for deeper playback totals"
    assert "DEFAULT_MAX_PROFILE_RESULTS" in tiktok_crawler, "TikTok baseline limit must allow more than 50"
    assert ".vkpi-channel-platforms" in css and ".vkpi-channel-platform-card" in css, "matrix CSS missing"
    assert ".vkpi-channel-summary-metric" in css and "flex-wrap" in css, "summary metric anti-overlap CSS missing"
    assert ".vkpi-channel-staff-grid" in staff_css and ".vkpi-channel-staff-card" in staff_css, "staff CSS missing"
    assert ".vkpi-channel-staff--compact" in staff_css and ".vkpi-action-card--team" in staff_css, "compact staff CSS missing"
    assert ".vkpi-channel-gap-list" in gaps_css and ".vkpi-channel-gap-card" in gaps_css and ".vkpi-channel-gaps__bar" in gaps_css, "gap CSS missing"
    assert ".vkpi-channel-account-grid" in account_css and ".vkpi-channel-account-card" in account_css, "account CSS missing"
    assert ".vkpi-channel-content-card" in content_css and "-webkit-line-clamp" in content_css and "video" in content_css, "content CSS missing"
    assert "IMAGE_PROXY_HOSTS" in media_proxy and "VIDEO_PROXY_HOSTS" in media_proxy and "playbackVideoCandidates" in media_proxy, "shared media proxy missing"
    assert ".image" in media_proxy and "tiktok.com/@" in media_proxy, "TikTok image/video detection missing"
    assert "staffLabel" in read("frontend/src/components/vkpi/drawers/EvidenceDrawer.tsx") and "vkpi-traffic-post__media" in read("frontend/src/components/vkpi/drawers/EvidenceDrawer.tsx"), "views evidence attribution UI missing"
    assert ".vkpi-traffic-post__media" in read("frontend/src/components/vkpi/VkpiDashboard.css"), "views evidence media CSS missing"
    assert "Execution Rules" in doc and "Level 1 Platform Matrix" in doc, "execution contract doc missing"

    print("VKPI_CHANNEL_MATRIX_FRONTEND_SMOKE_OK")


if __name__ == "__main__":
    main()
