from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import search_sessions, search_sessions_history
from app.domains.kol.search_sessions_items import canonicalize_session_creator_items
from app.domains.kol.search_sessions_previews import (
    hydrate_session_item_avatar_fallbacks,
)
from app.services.intelligence import account_scan_helpers
from app.services.intelligence import account_scan_service
from app.services.intelligence import account_search_discovery


def test_signed_avatar_policy_rejects_expired_urls_and_labels_live_ephemeral_urls() -> None:
    expired_tt = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=100&x-signature=old"
    live_tt = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=300&x-signature=current"
    expired_ig = "https://scontent.cdninstagram.com/avatar.jpg?oe=64&_nc_sid=test"

    assert account_scan_helpers._avatar_url_policy(expired_tt, now_epoch=200) == ("", "expired")
    assert account_scan_helpers._avatar_url_policy(live_tt, now_epoch=200) == (live_tt, "ephemeral")
    assert account_scan_helpers._avatar_url_policy(expired_ig, now_epoch=200) == ("", "expired")
    assert account_scan_helpers._avatar_url_policy("https://yt3.ggpht.com/avatar", now_epoch=200) == (
        "https://yt3.ggpht.com/avatar",
        "durable",
    )


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "javascript:alert(1)",
        "/relative/avatar.jpg",
        "relative/avatar.jpg",
        "//images.example/avatar.jpg",
        "ftp://images.example/avatar.jpg",
        "https:///missing-host.jpg",
    ),
)
def test_avatar_policy_rejects_non_http_or_hostless_urls(unsafe_url: str) -> None:
    assert account_scan_helpers._avatar_url_policy(unsafe_url) == ("", "invalid")


def test_profile_extraction_prefers_nested_profile_avatar_over_post_snapshot() -> None:
    profile = account_scan_helpers._profile_from_items(
        "instagram",
        "creator",
        [
            {
                "avatar_url": "https://images.example/post-snapshot.jpg",
                "owner": {"avatar_url": "https://images.example/refreshed-profile.jpg"},
            }
        ],
    )

    assert profile["avatar_url"] == "https://images.example/refreshed-profile.jpg"
    assert profile["avatar_url_status"] == "durable"


def test_session_identity_fold_preserves_the_stronger_profile_avatar() -> None:
    expired = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=1&x-signature=old"
    durable = "https://images.example/current-profile-avatar.jpg"

    folded = canonicalize_session_creator_items(
        [
            {
                "id": 1,
                "item_type": "existing_kol",
                "dedupe_key": "existing",
                "payload": {
                    "platform": "tiktok",
                    "handle": "creator",
                    "avatar_url": expired,
                    "avatar_url_status": "expired",
                },
            },
            {
                "id": 2,
                "item_type": "new_creator",
                "dedupe_key": "discovered",
                "payload": {
                    "platform": "tiktok",
                    "handle": "creator",
                    "avatar_url": durable,
                    "avatar_url_status": "durable",
                },
            },
        ]
    )

    assert len(folded) == 1
    assert folded[0]["item_type"] == "existing_kol"
    assert folded[0]["payload"]["avatar_url"] == durable
    assert folded[0]["payload"]["avatar_url_status"] == "durable"


def test_session_avatar_pool_fallback_is_durable_exact_identity_and_read_only() -> None:
    live_ephemeral = (
        "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=4102444800&x-signature=live"
    )

    class _Rows:
        def fetchall(self):
            return [
                {
                    "id": 1,
                    "platform": "instagram",
                    "handle": "missing.creator",
                    "profile_url": "https://instagram.com/missing.creator/",
                    "avatar_url": "https://images.example/missing-current.jpg",
                    "raw_platform_data": {},
                },
                {
                    "id": 2,
                    "platform": "instagram",
                    "handle": "expired.creator",
                    "profile_url": "https://instagram.com/expired.creator/",
                    "avatar_url": "https://images.example/expired-current.jpg",
                    "raw_platform_data": {},
                },
                {
                    "id": 3,
                    "platform": "tiktok",
                    "handle": "pool.ephemeral",
                    "profile_url": "https://tiktok.com/@pool.ephemeral/",
                    "avatar_url": live_ephemeral,
                    "raw_platform_data": {},
                },
                {
                    "id": 4,
                    "platform": "tiktok",
                    "handle": "session.ephemeral",
                    "profile_url": "https://tiktok.com/@session.ephemeral/",
                    "avatar_url": "https://images.example/pool-durable.jpg",
                    "raw_platform_data": {},
                },
                {
                    "id": 5,
                    "platform": "instagram",
                    "handle": "different.creator",
                    "profile_url": "https://instagram.com/different.creator/",
                    "avatar_url": "https://images.example/wrong-identity.jpg",
                    "raw_platform_data": {},
                },
                {
                    "id": 6,
                    "platform": "instagram",
                    "handle": "video.owner",
                    "profile_url": "https://instagram.com/video.owner/",
                    "avatar_url": "https://images.example/video-owner.jpg",
                    "raw_platform_data": {},
                },
            ]

    class _Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, _params=()):
            self.sql.append(" ".join(sql.split()))
            return _Rows()

    class _Logger:
        @staticmethod
        def warning(*_args, **_kwargs) -> None:
            raise AssertionError("avatar fallback query should not fail")

    expired = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=1&x-signature=old"
    items = [
        {
            "id": 11,
            "kol_pool_id": 1,
            "item_type": "existing_kol",
            "payload": {"platform": "instagram", "handle": "missing.creator"},
        },
        {
            "id": 12,
            "kol_pool_id": 2,
            "item_type": "recall_candidate",
            "payload": {
                "platform": "instagram",
                "handle": "expired.creator",
                "avatar_url": expired,
                "avatar_url_status": "expired",
            },
        },
        {
            "id": 13,
            "kol_pool_id": 3,
            "item_type": "existing_kol",
            "payload": {
                "platform": "tiktok",
                "handle": "pool.ephemeral",
                "thumbnail_url": "https://images.example/content-cover.jpg",
            },
        },
        {
            "id": 14,
            "kol_pool_id": 4,
            "item_type": "existing_kol",
            "payload": {
                "platform": "tiktok",
                "handle": "session.ephemeral",
                "avatar_url": live_ephemeral,
                "avatar_url_status": "ephemeral",
            },
        },
        {
            "id": 15,
            "kol_pool_id": 5,
            "item_type": "existing_kol",
            "payload": {"platform": "instagram", "handle": "actual.creator"},
        },
        {
            "id": 16,
            "kol_pool_id": 6,
            "item_type": "video_evidence",
            "payload": {
                "platform": "instagram",
                "handle": "video.owner",
                "thumbnail_url": "https://images.example/video-cover.jpg",
            },
        },
    ]
    conn = _Conn()

    applied = hydrate_session_item_avatar_fallbacks(
        conn,
        items,
        logger=_Logger(),
    )

    assert applied == 2
    assert items[0]["payload"]["avatar_url"] == "https://images.example/missing-current.jpg"
    assert items[1]["payload"]["avatar_url"] == "https://images.example/expired-current.jpg"
    assert items[0]["payload"]["avatar_url_source"] == "pool_durable_read_fallback"
    assert "avatar_url" not in items[2]["payload"]
    assert items[2]["payload"]["thumbnail_url"].endswith("content-cover.jpg")
    assert items[3]["payload"]["avatar_url"] == live_ephemeral
    assert "avatar_url" not in items[4]["payload"]
    assert "avatar_url" not in items[5]["payload"]
    assert all("thumbnail" not in sql.lower() for sql in conn.sql)
    assert not any("UPDATE" in sql or "DELETE" in sql or "INSERT" in sql for sql in conn.sql)


def test_session_detail_and_history_list_share_pool_avatar_fallback(monkeypatch) -> None:
    durable = "https://images.example/current-pool-avatar.jpg"

    class _Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class _Conn:
        def __init__(self) -> None:
            self.sql: list[str] = []

        def execute(self, sql, _params=()):
            compact = " ".join(sql.split())
            self.sql.append(compact)
            if "FROM vkpi_kol_search_sessions" in compact:
                return _Rows(
                    [
                        {
                            "id": 44,
                            "query_text": "camera creator",
                            "query_type": "text_recall",
                            "source": "smart_kol_input",
                            "status": "ready",
                            "created_by": 7,
                            "input_payload_json": {},
                            "result_summary_json": {},
                            "approved_kol_ids": [],
                            "archived_at": None,
                        }
                    ]
                )
            if "FROM vkpi_kol_search_session_items" in compact:
                return _Rows(
                    [
                        {
                            "id": 101,
                            "session_id": 44,
                            "kol_pool_id": 501,
                            "item_type": "existing_kol",
                            "status": "ready",
                            "dedupe_key": "existing:501",
                            "source_url": "https://instagram.com/creator/",
                            "payload_json": {
                                "platform": "instagram",
                                "handle": "creator",
                                "avatar_url": "",
                                "avatar_url_status": "missing",
                            },
                        }
                    ]
                )
            if "FROM vkpi_kol_pool" in compact:
                return _Rows(
                    [
                        {
                            "id": 501,
                            "platform": "instagram",
                            "handle": "creator",
                            "profile_url": "https://instagram.com/creator/",
                            "avatar_url": durable,
                            "raw_platform_data": {},
                            "display_name": "Creator",
                            "email": "",
                            "contact_channels": {},
                            "other_contacts_json": [],
                            "audience_estimated_json": {},
                        }
                    ]
                )
            raise AssertionError(compact)

    detail_conn = _Conn()
    monkeypatch.setattr(search_sessions, "get_conn", lambda: detail_conn)
    monkeypatch.setattr(
        search_sessions,
        "observe_worker_health",
        lambda _conn: {"status": "ready"},
    )
    monkeypatch.setattr(
        search_sessions,
        "project_search_progress",
        lambda *_args, **_kwargs: {"status": "ready"},
    )
    monkeypatch.setattr(
        search_sessions,
        "_apply_reach_display_gate",
        lambda _conn, items: (items, {"hidden": 0}),
    )
    monkeypatch.setattr(search_sessions, "mask_contact_payload", lambda payload: payload)

    detail = search_sessions.get_session(44, staff={"id": 7}, scope_to_staff=True)

    history_conn = _Conn()
    monkeypatch.setattr(
        search_sessions_history,
        "observe_worker_health",
        lambda _conn: {"status": "ready"},
    )
    monkeypatch.setattr(
        search_sessions_history,
        "project_search_progress",
        lambda *_args, **_kwargs: {"status": "ready"},
    )
    history = search_sessions_history.list_history(
        staff={"id": 7},
        get_conn_fn=lambda: history_conn,
        apply_reach_display_gate_fn=lambda _conn, items: (items, {"hidden": 0}),
        mask_contact_payload_fn=lambda payload: payload,
    )

    detail_payload = detail["items"][0]["payload"]
    history_payload = history["items"][0]["items_preview"][0]["payload"]
    assert detail_payload["avatar_url"] == durable
    assert history_payload["avatar_url"] == durable
    assert detail_payload["avatar_url_status"] == "durable"
    assert history_payload["avatar_url_status"] == "durable"
    assert detail_payload["avatar_url_source"] == "pool_durable_read_fallback"
    assert history_payload["avatar_url_source"] == "pool_durable_read_fallback"
    assert not any(
        keyword in sql
        for conn in (detail_conn, history_conn)
        for sql in conn.sql
        for keyword in ("UPDATE", "DELETE", "INSERT")
    )


def test_strict_youtube_video_search_keeps_profile_avatar_separate_from_video_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCrawler:
        api_key = "test-key"

        def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
            if endpoint == "search":
                return {
                    "provider_status": "ok",
                    "items": [
                        {
                            "id": {"videoId": "video-1"},
                            "snippet": {
                                "channelId": "UC-creator",
                                "channelTitle": "Creator",
                                "title": "Viltrox lens test",
                                "publishedAt": "2026-08-23T00:00:00Z",
                                "thumbnails": {"high": {"url": "https://i.ytimg.com/video-cover.jpg"}},
                            },
                        }
                    ],
                }
            assert endpoint == "channels"
            return {
                "provider_status": "ok",
                "items": [
                    {
                        "id": "UC-creator",
                        "snippet": {
                            "customUrl": "@creator",
                            "thumbnails": {"high": {"url": "https://yt3.ggpht.com/profile-avatar"}},
                        },
                        "statistics": {"subscriberCount": "12000"},
                    }
                ],
            }

        @staticmethod
        def _should_use_apify_fallback(_payload: dict[str, Any]) -> bool:
            return False

    from app.platform.industry_crawlers import youtube_crawler

    monkeypatch.setattr(youtube_crawler, "YouTubeCrawler", FakeCrawler)
    result = asyncio.run(
        account_search_discovery._youtube_data_api_strict_video_search("Viltrox lens", safe_limit=5)
    )

    assert result is not None
    item = result["items"][0]
    assert item["avatar_url"] == "https://yt3.ggpht.com/profile-avatar"
    assert item["avatar_url_status"] == "durable"
    assert item["thumbnail_url"] == "https://i.ytimg.com/video-cover.jpg"


def test_content_search_never_promotes_video_cover_to_missing_tiktok_avatar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired = "https://p16-sign.tiktokcdn.com/avatar.jpeg?x-expires=1&x-signature=old"
    cover = "https://p16-sign.tiktokcdn.com/video-cover.jpeg"

    async def fake_run_actor(_actor_id: str, _payload: dict[str, Any], timeout: int = 600) -> list[dict[str, Any]]:
        del timeout
        return [
            {
                "authorMeta": {"name": "creator", "nickName": "Creator", "avatar": expired, "fans": 20000},
                "videoMeta": {"coverUrl": cover},
                "webVideoUrl": "https://www.tiktok.com/@creator/video/1",
                "text": "Viltrox lens test",
                "playCount": 10000,
            }
        ]

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)
    result = asyncio.run(account_search_discovery.search_platform_content("tiktok", "Viltrox lens"))

    item = result["items"][0]
    assert item["avatar_url"] == ""
    assert item["avatar_url_status"] == "expired"
    assert item["thumbnail_url"] == cover


def test_instagram_profile_refresh_overrides_post_avatar(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_actor(actor_id: str, _payload: dict[str, Any], timeout: int = 600) -> list[dict[str, Any]]:
        del timeout
        if "hashtag" in actor_id:
            return [
                {
                    "ownerUsername": "creator",
                    "ownerProfilePicUrl": "https://images.example/post-avatar.jpg",
                    "displayUrl": "https://images.example/post-cover.jpg",
                    "caption": "Viltrox portrait",
                }
            ]
        return [
            {
                "username": "creator",
                "profilePicUrlHD": "https://images.example/refreshed-avatar.jpg",
                "followersCount": 20000,
            }
        ]

    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)
    result = asyncio.run(account_search_discovery.search_platform_content("instagram", "Viltrox portrait"))

    item = result["items"][0]
    assert item["avatar_url"] == "https://images.example/refreshed-avatar.jpg"
    assert item["thumbnail_url"] == "https://images.example/post-cover.jpg"
