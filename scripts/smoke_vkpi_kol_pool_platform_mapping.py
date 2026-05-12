#!/usr/bin/env python3
"""Offline guard for Apify-style platform field mapping in KOL Pool."""
from __future__ import annotations

from app.services.vkpi.industry_snapshot_kpis import calculate_kpis
from app.services.vkpi import kol_pool


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    tiktok_item = {
        "id": "7325787847372328197",
        "text": "macro lens test #photography",
        "createTime": 1705667902,
        "createTimeISO": "2024-01-19T12:38:22.000Z",
        "playCount": 193000,
        "diggCount": 27900,
        "commentCount": 151,
        "shareCount": 631,
        "collectCount": 1351,
        "webVideoUrl": "https://www.tiktok.com/@teleginivan/video/7325787847372328197",
        "videoMeta": {
            "duration": 29,
            "coverUrl": "https://example.com/video-cover.jpg",
        },
        "authorMeta": {
            "name": "teleginivan",
            "nickName": "Ivan Telegin",
            "signature": "camera and macro videos",
            "profileUrl": "https://www.tiktok.com/@teleginivan",
            "avatar": "https://example.com/avatar.jpg",
            "fans": 1000000,
            "following": 221,
            "video": 879,
            "heart": 50100000,
        },
    }
    raw = {
        "source": "tiktok_crawler",
        "profile": {"provider": "tiktok", "provider_status": "ok", "sync_status": "synced", "items": [tiktok_item]},
        "videos": [],
    }

    kpis = calculate_kpis(raw)
    assert_true(kpis["followers"] == 1000000, f"followers mismatch: {kpis['followers']}")
    assert_true(kpis["posts"] == 879, f"posts mismatch: {kpis['posts']}")
    assert_true(kpis["avg_views"] == 193000, f"avg_views mismatch: {kpis['avg_views']}")
    assert_true(kpis["likes"] == 27900, f"likes mismatch: {kpis['likes']}")
    assert_true(kpis["comments"] == 151, f"comments mismatch: {kpis['comments']}")
    assert_true(kpis["shares"] == 631, f"shares mismatch: {kpis['shares']}")
    assert_true(kpis["saves"] == 1351, f"saves mismatch: {kpis['saves']}")
    assert_true(kpis["engagement_rate"] and kpis["engagement_rate"] > 0, "engagement_rate should be positive")
    assert_true(kpis["avg_video_duration_seconds"] == 29, f"duration mismatch: {kpis['avg_video_duration_seconds']}")

    profile = kol_pool._profile_item(raw)
    videos = kol_pool._content_items_from_payload(raw["profile"])
    assert_true(len(videos) == 1, "profile.items should be treated as content list")
    assert_true(kol_pool._thumb_url(profile) == "https://example.com/avatar.jpg", "authorMeta avatar should map")
    assert_true(kol_pool._display_name(profile, "fallback") == "Ivan Telegin", "authorMeta display name should map")
    assert_true(kol_pool._bio(profile) == "camera and macro videos", "authorMeta signature should map")
    assert_true(kol_pool._profile_url("tiktok", profile, "teleginivan") == "https://www.tiktok.com/@teleginivan", "profileUrl should map")

    print("VKPI_KOL_POOL_PLATFORM_MAPPING_SMOKE_OK")


if __name__ == "__main__":
    main()
