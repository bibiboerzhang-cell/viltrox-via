from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.intelligence import account_scan_service
from app.services.intelligence import account_search_discovery as discovery


def test_legacy_youtube_actor_contract_preserves_identity_and_content_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the public facade before splitting its platform branches.

    A video title is discovery context, not a fabricated description or
    transcript.  Creator avatars, video covers, follower observations and
    account identity also stay in their own fields.
    """

    actor_calls: list[tuple[str, dict[str, Any], int]] = []

    async def no_data_api(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def fake_run_actor(
        actor_id: str,
        payload: dict[str, Any],
        timeout: int = 600,
    ) -> list[dict[str, Any]]:
        actor_calls.append((actor_id, dict(payload), timeout))
        return [
            {
                "channelName": "Field Creator",
                "channelHandle": "fieldcreator",
                "channelId": "UC-field",
                "channelUrl": "https://www.youtube.com/channel/UC-field",
                "channelAvatar": "https://yt3.ggpht.com/creator-avatar",
                "thumbnailUrl": "https://i.ytimg.com/video-cover.jpg",
                "url": "https://www.youtube.com/watch?v=first",
                "title": "Viltrox lens at a race track",
                "viewCount": "12,345",
                "likes": 321,
                "commentsCount": 17,
                "subscriberCount": "54,321",
                "uploadDate": "2026-08-20",
                "videoLanguage": "en",
            },
            {
                # Same account through another matched video: first provider
                # row wins so variants cannot create duplicate creator cards.
                "channelName": "Field Creator duplicate",
                "channelHandle": "@FieldCreator",
                "channelId": "UC-field",
                "title": "second match",
            },
            {
                "channelName": "Chef Optics",
                "channelId": "UC-chef",
                "channelUrl": "https://www.youtube.com/channel/UC-chef",
                "title": "Kitchen product photography",
                "description": "A real provider description about lighting food.",
                "transcript": "A real provider transcript about the shoot.",
            },
        ]

    monkeypatch.setattr(discovery, "_youtube_data_api_search", no_data_api)
    monkeypatch.setattr(account_scan_service, "provider_ready", lambda: True)
    monkeypatch.setattr(account_scan_service, "_run_actor", fake_run_actor)

    result = asyncio.run(
        discovery.search_platform_content(
            " YouTube ", "  practical lens users  ", market="US", max_results=3,
        )
    )

    assert actor_calls == [
        (
            "streamers/youtube-scraper",
            {
                "searchQueries": ["practical lens users US"],
                "maxResults": 3,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            },
            240,
        )
    ]
    assert result["status"] == "done"
    assert result["query"] == "practical lens users"
    assert result["market"] == "US"
    assert [item["handle"] for item in result["items"]] == ["fieldcreator", "UC-chef"]

    first, second = result["items"]
    assert first["avatar_url"] == "https://yt3.ggpht.com/creator-avatar"
    assert first["avatar_url_status"] == "durable"
    assert first["thumbnail_url"] == "https://i.ytimg.com/video-cover.jpg"
    assert first["followers"] == 54321
    assert first["sample_title"] == "Viltrox lens at a race track"
    assert "sample_description" not in first
    assert "sample_transcript" not in first
    assert second["sample_description"] == "A real provider description about lighting food."
    assert second["sample_transcript"] == "A real provider transcript about the shoot."
    assert result["metadata"] | {"searched_at": "<dynamic>"} == {
        "actor_id": "streamers/youtube-scraper",
        "requested": 3,
        "returned": 2,
        "provider_queries": ["practical lens users US"],
        "searched_at": "<dynamic>",
        "pagination_supported": False,
        "pagination_unsupported_reason": "actor_input_schema_has_no_cursor",
        "has_more": False,
    }


def test_exact_youtube_query_never_switches_to_unforecast_paid_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_data_api(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(discovery, "_youtube_data_api_search", no_data_api)
    monkeypatch.setattr(
        account_scan_service,
        "provider_ready",
        lambda: (_ for _ in ()).throw(AssertionError("Apify readiness must not be read")),
    )

    result = asyncio.run(
        discovery.search_platform_content(
            "youtube", "motorsport camera operator", exact_query=True,
        )
    )

    assert result == {
        "status": "provider_unavailable",
        "platform": "youtube",
        "items": [],
        "message": "YouTube Data API unavailable; exact-query fallback is disabled",
        "metadata": {
            "query_mode": "exact_query_cell",
            "fallback_policy": "disabled_unforecast_provider_switch",
            "provider_calls": 0,
        },
    }
