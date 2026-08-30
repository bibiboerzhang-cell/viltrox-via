"""Characterization fence for the provider discovery orchestration seam."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domains.kol import profile_discovery_provider as provider


def _creator(handle: str, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "handle": handle,
        "channel_name": handle.title(),
        "channel_url": f"https://www.youtube.com/@{handle}",
        "source_url": f"https://www.youtube.com/watch?v={handle}",
        "sample_title": "camera filmmaking portrait lighting",
        "bio": "camera filmmaker and photography creator",
        "followers": 2_000,
        "views": 5_000,
        "likes": 120,
        "comments": 30,
        "avg_views": 5_000,
        "avatar_url": "",
    }
    item.update(overrides)
    return item


def test_provider_discovery_preserves_call_gate_pagination_and_write_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_search(platform: str, query: str, **kwargs: Any) -> dict[str, Any]:
        events.append(("search", platform))
        calls.append((platform, query, dict(kwargs)))
        if platform == "youtube":
            return {
                "status": "done",
                "items": [
                    _creator("alpha"),
                    _creator("alpha", avatar_url="https://images.example/alpha.jpg"),
                    _creator("library"),
                    _creator("tiny", followers=100),
                    _creator(
                        "breadonly",
                        channel_name="Bread Only",
                        sample_title="bread baking recipe",
                        bio="home baker and recipe writer",
                    ),
                ],
                "metadata": {
                    "pagination_supported": True,
                    "next_page_cursor": {"portrait lighting": "YT_NEXT"},
                    "has_more": True,
                },
            }
        return {
            "status": "ready",
            "items": [
                _creator(
                    "beta",
                    channel_url="https://www.instagram.com/beta/",
                    source_url="https://www.instagram.com/beta/",
                )
            ],
            "metadata": {"pagination_supported": False, "has_more": False},
        }

    def annotate(items: list[dict[str, Any]], *, platform: str) -> list[dict[str, Any]]:
        output = [dict(item) for item in items]
        for item in output:
            if item.get("handle") == "library":
                item["historical_match"] = {"kol_pool_id": 99}
        return output

    def triage(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        events.append(("triage", [item.get("handle") for item in items]))
        return items, {"low_reach": 0, "analyzing": 0}

    def enroll(items: list[dict[str, Any]]) -> int:
        events.append(("enroll", [item.get("handle") for item in items]))
        return len(items)

    def warm(items: list[dict[str, Any]]) -> None:
        events.append(("warm", [item.get("handle") for item in items]))

    monkeypatch.setattr(provider, "search_platform_content", fake_search)
    monkeypatch.setattr(provider.history_match, "annotate_platform_items", annotate)
    monkeypatch.setattr(provider, "_triage_existing_matches_reach", triage)
    monkeypatch.setattr(provider, "_auto_enroll_discoveries", enroll)
    monkeypatch.setattr(provider, "_warm_discovery_avatar_cache", warm)

    result = asyncio.run(
        provider.discover_new_creators(
            query_text="portrait lighting",
            platforms=["youtube", "instagram"],
            market="US",
            limit=10,
            per_platform_limit=7,
            per_platform_limits={"youtube": 9},
            page_cursors={"youtube": {"portrait lighting": "YT_PREVIOUS"}},
            exact_query=True,
        )
    )

    assert [event[:2] for event in events] == [
        ("search", "youtube"),
        ("search", "instagram"),
        ("triage", ["library"]),
        ("enroll", ["alpha", "beta"]),
        ("warm", ["alpha", "beta"]),
    ]
    assert [call[0] for call in calls] == ["youtube", "instagram"]
    assert calls[0][1] == "portrait lighting"
    assert calls[0][2]["max_results"] == 9
    assert calls[1][2]["max_results"] == 7
    assert calls[0][2]["page_cursor"] == {"portrait lighting": "YT_PREVIOUS"}
    assert calls[1][2]["page_cursor"] is None
    assert calls[0][2]["exact_query"] is True
    assert calls[1][2]["exact_query"] is True

    assert [item["handle"] for item in result["new_creators"]] == ["alpha", "beta"]
    assert result["new_creators"][0]["avatar_url"] == "https://images.example/alpha.jpg"
    assert [item["handle"] for item in result["existing_matches"]] == ["library"]
    assert result["counts"]["filtered_low_reach"] == 1
    assert result["counts"]["auto_enrolled"] == 2
    assert result["counts"]["new_creators"] == 2
    assert result["items"] == [*result["existing_matches"], *result["new_creators"]]
    assert result["query_mode"] == "exact_query_cell"
    assert result["next_page_cursors"] == {
        "youtube": {"portrait lighting": "YT_NEXT"}
    }
    assert result["next_cursor"] == {
        "page_cursors": {"youtube": {"portrait lighting": "YT_NEXT"}},
        "has_more": {"instagram": False, "youtube": True},
        "supported": {"instagram": False, "youtube": True},
    }
    assert result["has_more"] is True


def test_provider_invalid_query_never_starts_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden_search(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("provider must not run for an invalid query")

    monkeypatch.setattr(provider, "search_platform_content", forbidden_search)

    result = asyncio.run(
        provider.discover_new_creators(query_text=" ", platforms=["youtube"])
    )

    assert result == {
        "status": "invalid_query",
        "query": "",
        "platforms": ["youtube"],
        "items": [],
        "new_creators": [],
        "existing_matches": [],
        "provider_calls": False,
        "message": "query is required",
    }
