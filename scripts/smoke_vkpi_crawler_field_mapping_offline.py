#!/usr/bin/env python3
"""Offline smoke for multi-platform crawler KPI field mapping.

This smoke does not call external providers. It verifies that raw payload shapes
from the R-Phase2-B adapters map into industry snapshot KPI fields without
fabricating unavailable metrics.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")

from app.services.vkpi.industry_snapshot_collector import calculate_kpis  # noqa: E402


def _assert_equal(actual: object, expected: object, label: str, payload: dict) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected={expected!r} actual={actual!r} payload={payload!r}")


def _x_official_payload() -> dict:
    return {
        "source": "x_official_fixture",
        "profile": {
            "items": [
                {
                    "id": "x-user-1",
                    "username": "viltrox",
                    "public_metrics": {
                        "followers_count": 1234,
                        "following_count": 20,
                        "tweet_count": 55,
                        "listed_count": 2,
                    },
                }
            ]
        },
        "videos": [
            {
                "id": "tweet-1",
                "text": "Viltrox lens test #viltrox",
                "public_metrics": {
                    "impression_count": 1000,
                    "like_count": 20,
                    "reply_count": 3,
                    "retweet_count": 4,
                    "bookmark_count": 5,
                },
            }
        ],
    }


def _twitch_payload() -> dict:
    return {
        "source": "twitch_fixture",
        "profile": {
            "items": [
                {
                    "id": "100",
                    "login": "viltrox_live",
                    "display_name": "Viltrox Live",
                    "view_count": 9999,
                }
            ]
        },
        "videos": [
            {
                "id": "vod-1",
                "title": "Lens launch livestream",
                "view_count": 500,
            }
        ],
    }


def _bilibili_payload() -> dict:
    return {
        "source": "bilibili_fixture",
        "profile": {
            "items": [
                {
                    "id": "space-1",
                    "name": "Viltrox CN",
                    "follower": 888,
                    "archiveCount": 12,
                    "view": 4000,
                }
            ]
        },
        "videos": [
            {
                "id": "BV1",
                "title": "35mm F1.2 sample",
                "play": 3000,
                "likes": 120,
                "comments": 9,
                "shares": 6,
            }
        ],
    }


def _xiaohongshu_payload() -> dict:
    return {
        "source": "xiaohongshu_fixture",
        "profile": {
            "items": [
                {
                    "id": "xhs-user-1",
                    "nickname": "Viltrox Notes",
                    "fans": 777,
                    "noteCount": 33,
                }
            ]
        },
        "videos": [
            {
                "id": "note-1",
                "title": "人像镜头样张",
                "views": 400,
                "likedCount": 100,
                "comment_count": 8,
                "share_count": 3,
                "collectCount": 9,
            }
        ],
    }


def _xiaohongshu_profile_only_payload() -> dict:
    return {
        "source": "xiaohongshu_zhorex_profile_fixture",
        "profile": {
            "items": [
                {
                    "mode": "profile",
                    "userId": "60346fc0000000000101c9be",
                    "profileUrl": "https://www.xiaohongshu.com/user/profile/60346fc0000000000101c9be",
                    "nickname": "Viltrox唯卓仕",
                    "followers": 0,
                    "notesCount": 0,
                    "totalLikes": 0,
                }
            ]
        },
        "videos": [],
    }


def _instagram_apify_payload() -> dict:
    return {
        "source": "instagram_apify_fixture",
        "profile": {
            "items": [
                {
                    "id": "ig-user-1",
                    "username": "viltrox.cine",
                    "followersCount": 34550,
                    "postsCount": 72,
                }
            ]
        },
        "videos": [
            {
                "id": "ig-post-1",
                "caption": "New lens sample #viltrox",
                "likesCount": 1200,
                "commentsCount": 34,
                "timestamp": "2026-05-09T09:00:00.000Z",
                "displayUrl": "https://example.invalid/ig.jpg",
            }
        ],
    }


def _tiktok_apify_payload() -> dict:
    return {
        "source": "tiktok_apify_fixture",
        "profile": {
            "items": [
                {
                    "input": "viltrox",
                    "authorMeta": {
                        "id": "tt-user-1",
                        "name": "viltrox",
                        "nickName": "Viltrox",
                        "fans": 22000,
                        "video": 41,
                        "heart": 150000,
                        "digg": 50,
                    },
                }
            ]
        },
        "videos": [
            {
                "id": "tt-post-1",
                "authorMeta": {
                    "fans": 22000,
                    "video": 41,
                },
            }
        ],
    }


def _youtube_regression_payload() -> dict:
    return {
        "source": "youtube_regression_fixture",
        "profile": {
            "items": [
                {
                    "id": "UC123",
                    "statistics": {
                        "subscriberCount": "12345",
                        "videoCount": "88",
                        "viewCount": "987654",
                    },
                }
            ]
        },
        "videos": [
            {
                "id": "yt-1",
                "snippet": {"publishedAt": "2026-05-07T10:00:00Z", "title": "Viltrox AF test #lens"},
                "statistics": {"viewCount": "1500", "likeCount": "180", "commentCount": "24"},
            }
        ],
    }


def main() -> None:
    x = calculate_kpis(_x_official_payload())
    _assert_equal(x["followers"], 1234, "x followers_count", x)
    _assert_equal(x["posts"], 55, "x tweet_count", x)
    _assert_equal(x["views_30d"], 1000, "x impression_count", x)
    _assert_equal(x["likes"], 20, "x like_count", x)
    _assert_equal(x["comments"], 3, "x reply_count", x)
    _assert_equal(x["shares"], 4, "x retweet_count", x)
    _assert_equal(x["saves"], 5, "x bookmark_count", x)

    twitch = calculate_kpis(_twitch_payload())
    _assert_equal(twitch["followers"], None, "twitch followers must stay unknown", twitch)
    _assert_equal(twitch["views"], 9999, "twitch profile view_count", twitch)
    _assert_equal(twitch["views_30d"], 500, "twitch video view_count", twitch)

    bilibili = calculate_kpis(_bilibili_payload())
    _assert_equal(bilibili["followers"], 888, "bilibili follower", bilibili)
    _assert_equal(bilibili["posts"], 12, "bilibili archiveCount", bilibili)
    _assert_equal(bilibili["views"], 4000, "bilibili view", bilibili)
    _assert_equal(bilibili["views_30d"], 3000, "bilibili play", bilibili)
    _assert_equal(bilibili["likes"], 120, "bilibili likes", bilibili)

    xhs = calculate_kpis(_xiaohongshu_payload())
    _assert_equal(xhs["followers"], 777, "xiaohongshu fans", xhs)
    _assert_equal(xhs["posts"], 33, "xiaohongshu noteCount", xhs)
    _assert_equal(xhs["views_30d"], 400, "xiaohongshu views", xhs)
    _assert_equal(xhs["likes"], 100, "xiaohongshu likedCount", xhs)
    _assert_equal(xhs["comments"], 8, "xiaohongshu comment_count", xhs)
    _assert_equal(xhs["shares"], 3, "xiaohongshu share_count", xhs)
    _assert_equal(xhs["saves"], 9, "xiaohongshu collectCount", xhs)

    xhs_profile = calculate_kpis(_xiaohongshu_profile_only_payload())
    _assert_equal(xhs_profile["followers"], 0, "xiaohongshu profile followers zero is known", xhs_profile)
    _assert_equal(xhs_profile["posts"], 0, "xiaohongshu profile notesCount zero is known", xhs_profile)
    _assert_equal(xhs_profile["likes"], 0, "xiaohongshu profile totalLikes zero is known", xhs_profile)

    instagram = calculate_kpis(_instagram_apify_payload())
    _assert_equal(instagram["followers"], 34550, "instagram followersCount", instagram)
    _assert_equal(instagram["posts"], 72, "instagram postsCount", instagram)
    _assert_equal(instagram["likes"], 1200, "instagram likesCount", instagram)
    _assert_equal(instagram["comments"], 34, "instagram commentsCount", instagram)
    _assert_equal(instagram["views_30d"], None, "instagram missing views must stay unknown", instagram)

    tiktok = calculate_kpis(_tiktok_apify_payload())
    _assert_equal(tiktok["followers"], 22000, "tiktok authorMeta.fans", tiktok)
    _assert_equal(tiktok["posts"], 41, "tiktok authorMeta.video", tiktok)
    _assert_equal(tiktok["views_30d"], None, "tiktok missing views must stay unknown", tiktok)

    youtube = calculate_kpis(_youtube_regression_payload())
    _assert_equal(youtube["followers"], 12345, "youtube subscriberCount regression", youtube)
    _assert_equal(youtube["posts"], 88, "youtube videoCount regression", youtube)
    _assert_equal(youtube["views"], 987654, "youtube viewCount regression", youtube)
    _assert_equal(youtube["views_30d"], 1500, "youtube video viewCount regression", youtube)

    print("VKPI_CRAWLER_FIELD_MAPPING_OFFLINE_SMOKE_OK")


if __name__ == "__main__":
    main()
