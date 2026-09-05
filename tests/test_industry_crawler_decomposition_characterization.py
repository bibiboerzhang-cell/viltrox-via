"""Characterization tests locking YouTubeCrawler / RedditCrawler behavior across the
class-LOC decomposition (W4: 715/582 行巨类拆到 ≤400).

刻意只走「拆完仍然存在」的表面:公开方法 + strict_video 依赖的 patch 面
(``_request`` / ``_should_use_apify_fallback`` / ``_start_apify_run``)+ 实例级
私有方法(mixin 拆分后仍从实例可达)。拆分前后本文件必须原样全绿。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.platform.industry_crawlers import reddit_crawler as reddit_module
from app.platform.industry_crawlers.reddit_crawler import RedditCrawler
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler


# ─── helpers ────────────────────────────────────────────────────────────────


def _clear_youtube_env(monkeypatch) -> None:
    for name in (
        "YOUTUBE_API_KEY",
        "GOOGLE_YOUTUBE_API_KEY",
        "APIFY_TOKEN",
        "APIFY_YOUTUBE_ACTOR_ID",
        "VKPI_YOUTUBE_MAX_CHANNEL_VIDEOS",
    ):
        monkeypatch.delenv(name, raising=False)


def _clear_reddit_env(monkeypatch) -> None:
    for name in (
        "REDDIT_CLIENT_ID",
        "REDDIT_CLIENT_SECRET",
        "REDDIT_USER_AGENT",
        "APIFY_TOKEN",
        "APIFY_REDDIT_ACTOR_ID",
        "VKPI_REDDIT_PUBLIC_JSON_ENABLED",
        "APIFY_REDDIT_RUN_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


class _ScriptedRequest:
    """Fake for YouTubeCrawler._request: records calls, replays scripted pages."""

    def __init__(self, script) -> None:
        self.script = script
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, dict(params)))
        return self.script(endpoint, dict(params), len(self.calls))


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    merged = {"provider_status": "ok", "sync_status": "synced"}
    merged.update(payload)
    return merged


# ─── YouTube: 提名/URL 归一(公开 staticmethod,拆后保留) ────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", {"kind": "empty", "value": ""}),
        ("UCabc123456", {"kind": "channel_id", "value": "UCabc123456"}),
        ("@LensPro", {"kind": "handle", "value": "LensPro"}),
        ("https://www.youtube.com/@LensPro/videos", {"kind": "handle", "value": "LensPro"}),
        ("https://www.youtube.com/channel/UCzzz999888", {"kind": "channel_id", "value": "UCzzz999888"}),
        ("https://www.youtube.com/c/LegacyName", {"kind": "query", "value": "LegacyName"}),
        ("https://www.youtube.com/user/OldUser", {"kind": "query", "value": "OldUser"}),
        ("Some Channel Name", {"kind": "query", "value": "Some Channel Name"}),
    ],
)
def test_youtube_normalize_channel_ref(raw: str, expected: dict[str, str]) -> None:
    assert YouTubeCrawler.normalize_channel_ref(raw) == expected


# ─── YouTube: 配置门与状态汇报 ───────────────────────────────────────────


def test_youtube_provider_status_and_not_configured(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="", apify_token="")

    assert crawler.configured is False
    assert crawler.provider_status() == {
        "provider": "youtube",
        "configured": False,
        "provider_status": "not_configured",
        "youtube_api_configured": False,
        "apify_configured": False,
        "apify_actor_id": "streamers/youtube-scraper",
        "key_visible": False,
    }
    result = crawler.crawl_channel_videos("UCabc123456")
    assert result == {
        "provider": "youtube",
        "operation": "crawl_channel_videos",
        "provider_status": "not_configured",
        "sync_status": "not_configured",
        "items": [],
        "raw": {},
        "message": "YouTube API key 和 APIFY_TOKEN 均未配置，未执行外部抓取。",
    }


# ─── YouTube: uploads playlist 分页 + videos 富化(经公开方法) ──────────


def _uploads_script(page_tokens: dict[str, list[str]], uploads: str = "UUmain"):
    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        if endpoint == "channels":
            return _ok({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": uploads}}}]})
        if endpoint == "playlistItems":
            token = str(params.get("pageToken") or "")
            ids = page_tokens[token][: int(params["maxResults"])]
            next_token = f"T{len(token) + 1}" if f"T{len(token) + 1}" in page_tokens else ""
            payload = _ok({"items": [{"contentDetails": {"videoId": vid}} for vid in ids]})
            if next_token:
                payload["nextPageToken"] = next_token
            return payload
        if endpoint == "videos":
            ids = str(params["id"]).split(",")
            return _ok({"items": [{"id": vid} for vid in ids]})
        raise AssertionError(f"unexpected endpoint {endpoint}")

    return script


def test_youtube_channel_videos_uploads_playlist_paging_dedupes_and_enriches(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")
    fake = _ScriptedRequest(_uploads_script({"": ["v1", "v2"], "T1": ["v2", "v3"]}))
    monkeypatch.setattr(crawler, "_request", fake)

    result = crawler.crawl_channel_videos("UCabc123456", max_results=10)

    assert result["provider_status"] == "ok"
    assert result["sync_status"] == "synced"
    assert [item["id"] for item in result["items"]] == ["v1", "v2", "v3"]
    assert result["search_raw"]["mode"] == "uploads_playlist"
    assert result["search_raw"]["video_count"] == 3
    assert len(result["search_raw"]["pages"]) == 2
    endpoints = [endpoint for endpoint, _params in fake.calls]
    assert endpoints == ["channels", "playlistItems", "playlistItems", "videos"]
    first_page = fake.calls[1][1]
    assert first_page["playlistId"] == "UUmain"
    assert first_page["maxResults"] == 10
    second_page = fake.calls[2][1]
    assert second_page["pageToken"] == "T1"
    assert second_page["maxResults"] == 8
    assert fake.calls[3][1]["id"] == "v1,v2,v3"
    assert fake.calls[3][1]["part"] == "snippet,statistics,contentDetails"


def test_youtube_channel_videos_env_cap_clamps_target(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    monkeypatch.setenv("VKPI_YOUTUBE_MAX_CHANNEL_VIDEOS", "2")
    crawler = YouTubeCrawler(api_key="yt")
    fake = _ScriptedRequest(_uploads_script({"": ["v1", "v2", "v3"]}))
    monkeypatch.setattr(crawler, "_request", fake)

    result = crawler.crawl_channel_videos("UCabc123456", max_results=10)

    assert [item["id"] for item in result["items"]] == ["v1", "v2"]
    assert fake.calls[1][1]["maxResults"] == 2


def test_youtube_channel_videos_since_reuses_uploads_playlist(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")

    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        if endpoint == "channels":
            return _ok({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUmain"}}}]})
        if endpoint == "playlistItems":
            return _ok({"items": [{"contentDetails": {"videoId": "s1", "videoPublishedAt": "2026-05-02T00:00:00Z"}}]})
        if endpoint == "videos":
            return _ok({"items": [{"id": "s1"}]})
        raise AssertionError(f"unexpected endpoint {endpoint}")

    fake = _ScriptedRequest(script)
    monkeypatch.setattr(crawler, "_request", fake)

    result = crawler.crawl_channel_videos("UCabc123456", max_results=5, since="2026-05-01")

    assert result["search_raw"]["mode"] == "uploads_playlist"
    assert [item["id"] for item in result["items"]] == ["s1"]
    search_params = dict(fake.calls[1][1])
    assert fake.calls[1][0] == "playlistItems"
    assert search_params["playlistId"] == "UUmain"
    assert "publishedAfter" not in search_params
    assert result["metadata"]["date_window_complete"] is True


def test_youtube_video_details_quota_midway_triggers_fallback_with_partial_in_raw(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")
    all_ids = [f"v{index:02d}" for index in range(55)]

    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        if endpoint == "channels":
            return _ok({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUmain"}}}]})
        if endpoint == "playlistItems":
            token = str(params.get("pageToken") or "")
            if token == "":
                payload = _ok({"items": [{"contentDetails": {"videoId": vid}} for vid in all_ids[:50]]})
                payload["nextPageToken"] = "T1"
                return payload
            return _ok({"items": [{"contentDetails": {"videoId": vid}} for vid in all_ids[50:]]})
        if endpoint == "videos":
            ids = str(params["id"]).split(",")
            if ids[0] == "v00":
                return _ok({"items": [{"id": vid} for vid in ids]})
            return {
                "provider_status": "quota_exceeded",
                "sync_status": "quota_exceeded",
                "items": [],
                "error": "quota",
                "error_reason": "quotaExceeded",
                "http_status": 403,
            }
        raise AssertionError(f"unexpected endpoint {endpoint}")

    monkeypatch.setattr(crawler, "_request", _ScriptedRequest(script))

    result = crawler.crawl_channel_videos("UCabc123456", max_results=55)

    # Partial official results remain visible; do not buy a replacement run.
    assert result["provider_status"] == "quota_exceeded"
    assert result["sync_status"] == "quota_exceeded"
    assert len(result["items"]) == 50
    assert result["status"] == "partial"
    assert "apify_fallback_status" not in result
    assert result["error_reason"] == "quotaExceeded"
    assert result["http_status"] == 403
    assert result["raw"]["partial_count"] == 50
    assert result["raw"]["video_ids"] == all_ids


def test_youtube_video_details_empty_ids_propagates_last_page_status(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")

    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        if endpoint == "channels":
            return _ok({"items": []})
        if endpoint == "search":
            return _ok({"items": []})
        raise AssertionError(f"unexpected endpoint {endpoint}")

    monkeypatch.setattr(crawler, "_request", _ScriptedRequest(script))

    result = crawler.crawl_channel_videos("UCabc123456", max_results=5)

    assert result["provider_status"] == "ok"
    assert result["sync_status"] == "synced"
    assert result["items"] == []
    assert result["raw"]["mode"] == "search"


# ─── YouTube: 档案抓取的 query→search→channels 路径 ─────────────────────


def test_youtube_channel_profile_by_query_resolves_channel_id(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")

    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        if endpoint == "search":
            assert params["q"] == "Some Channel Name"
            assert params["type"] == "channel"
            assert params["maxResults"] == 1
            return _ok({"items": [{"id": {"channelId": "UCfound12345"}}]})
        if endpoint == "channels":
            assert params["id"] == "UCfound12345"
            return _ok({"items": [{"id": "UCfound12345"}]})
        raise AssertionError(f"unexpected endpoint {endpoint}")

    monkeypatch.setattr(crawler, "_request", _ScriptedRequest(script))

    result = crawler.crawl_channel_profile("Some Channel Name")

    assert result["provider_status"] == "ok"
    assert result["query"] == {"kind": "query", "value": "Some Channel Name"}
    assert result["items"] == [{"id": "UCfound12345"}]


def test_youtube_channel_profile_query_without_hits_is_no_results(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")
    monkeypatch.setattr(crawler, "_request", _ScriptedRequest(lambda *_a: _ok({"items": []})))

    result = crawler.crawl_channel_profile("Some Channel Name")

    assert result["provider_status"] == "no_results"
    assert result["sync_status"] == "no_results"
    assert result["items"] == []


# ─── YouTube: Apify fallback 输入构造与 item 归一(经 patch 面) ──────────


def test_youtube_apify_input_carries_oldest_post_date_and_channel_url(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="", apify_token="apify")
    captured: dict[str, Any] = {}

    def fake_start(input_payload: dict[str, Any], *, operation: str) -> dict[str, Any]:
        captured["operation"] = operation
        captured["input"] = input_payload
        return {
            "provider": "youtube",
            "provider_status": "ok",
            "sync_status": "synced",
            "items": [
                {
                    "title": "Clip",
                    "url": "https://youtu.be/zzz98765432",
                    "viewCount": "1,234",
                    "likes": "56",
                    "commentsCount": None,
                    "date": "2026-05-02T00:00:00Z",
                    "duration": "PT2M",
                    "channelName": "Lens Channel",
                    "channelUrl": "https://www.youtube.com/channel/UCabc123456",
                }
            ],
            "raw": {"actor_id": "streamers/youtube-scraper", "input": input_payload},
        }

    monkeypatch.setattr(crawler, "_start_apify_run", fake_start)

    result = crawler.crawl_channel_videos("UCabc123456", max_results=3, since="2026-05-01")

    assert captured["operation"] == "crawl_channel_videos"
    assert captured["input"] == {
        "startUrls": [{"url": "https://www.youtube.com/channel/UCabc123456/videos"}],
        "maxResults": 3,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
        "oldestPostDate": "2026-05-01",
    }
    assert result["provider_source"] == "apify"
    assert result["fallback_from"] == "youtube_api_not_configured"
    item = result["items"][0]
    assert item["id"] == "zzz98765432"
    assert item["statistics"] == {"viewCount": 1234, "likeCount": 56, "commentCount": None}
    assert item["snippet"]["channelId"] == "UCabc123456"
    assert item["snippet"]["publishedAt"] == "2026-05-02T00:00:00Z"
    assert item["contentDetails"] == {"duration": "PT2M"}


def test_youtube_http_error_payload_maps_non_quota_reason_to_error() -> None:
    from io import BytesIO
    from urllib.error import HTTPError

    body = b'{"error":{"code":400,"errors":[{"reason":"badRequest"}]}}'
    exc = HTTPError("https://example.invalid", 400, "Bad Request", {}, BytesIO(body))
    result = YouTubeCrawler(api_key="yt")._http_error_payload(exc)

    assert result["provider_status"] == "error"
    assert result["sync_status"] == "error"
    assert result["error_reason"] == "badRequest"
    assert result["http_status"] == 400
    assert result["raw"]["error"]["error"]["errors"][0]["reason"] == "badRequest"


# ─── YouTube: 评论抓取(扁平化 + 回复补抓分页) ─────────────────────────


def test_youtube_video_comments_flattens_threads_and_fetches_missing_replies(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")

    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        if endpoint == "commentThreads":
            assert params["videoId"] == "abc12345678"
            assert params["order"] == "relevance"
            assert params["textFormat"] == "plainText"
            assert params["maxResults"] == 3
            return _ok(
                {
                    "items": [
                        {
                            "id": "t1",
                            "snippet": {
                                "topLevelComment": {"id": "c1", "snippet": {"textDisplay": "top"}},
                                "totalReplyCount": 2,
                            },
                            "replies": {"comments": [{"id": "r1"}]},
                        },
                        {
                            "id": "t2",
                            "snippet": {
                                "topLevelComment": {"id": "c2", "snippet": {"textDisplay": "other"}},
                                "totalReplyCount": 0,
                            },
                        },
                    ]
                }
            )
        if endpoint == "comments":
            assert params["parentId"] == "c1"
            assert params["maxResults"] == 2
            return _ok({"items": [{"id": "r2"}]})
        raise AssertionError(f"unexpected endpoint {endpoint}")

    monkeypatch.setattr(crawler, "_request", _ScriptedRequest(script))

    result = crawler.crawl_video_comments("https://youtu.be/abc12345678", max_results=3)

    assert result["raw"] == {"video_id": "abc12345678", "thread_count": 2}
    ids = [(item["id"], item["depth"]) for item in result["items"]]
    assert ids == [("c1", 0), ("r1", 1), ("r2", 1)]
    assert result["items"][0]["reply_count"] == 2


def test_youtube_video_comments_rejects_unparseable_reference(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")

    result = crawler.crawl_video_comments("???")

    assert result == {
        "provider": "youtube",
        "provider_status": "error",
        "sync_status": "error",
        "items": [],
        "error": "could not extract video_id",
    }


def test_youtube_video_comments_accepts_shorts_url(monkeypatch) -> None:
    _clear_youtube_env(monkeypatch)
    crawler = YouTubeCrawler(api_key="yt")

    def script(endpoint: str, params: dict[str, Any], _n: int) -> dict[str, Any]:
        assert endpoint == "commentThreads"
        assert params["videoId"] == "short123456"
        return _ok({"items": []})

    monkeypatch.setattr(crawler, "_request", _ScriptedRequest(script))

    result = crawler.crawl_video_comments("https://www.youtube.com/shorts/short123456/extra")

    assert result["items"] == []
    assert result["raw"]["video_id"] == "short123456"


# ─── Reddit: 引用归一(staticmethod,拆后保留) ─────────────────────────


@pytest.mark.parametrize(
    ("handle", "channel_id", "expected"),
    [
        ("https://reddit.com/r/cinematography/top", "", "cinematography"),
        ("r/videography", "", "videography"),
        ("/r/lenses", "", "lenses"),
        ("plainname", "", "plainname"),
        ("ignored", "r/fromid", "fromid"),
    ],
)
def test_reddit_normalize_subreddit_name(handle: str, channel_id: str, expected: str) -> None:
    assert RedditCrawler._normalize_subreddit_name(handle, channel_id) == expected


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("https://reddit.com/r/a/comments/xyz789/title/", "xyz789"),
        ("t3_abc123", "abc123"),
        ("abc123", "abc123"),
    ],
)
def test_reddit_normalize_post_id(ref: str, expected: str) -> None:
    assert RedditCrawler._normalize_post_id(ref) == expected


# ─── Reddit: 配置门/路径选择/状态汇报 ───────────────────────────────────


def test_reddit_provider_status_json_default(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()

    assert crawler.configured is True
    assert crawler.primary_path == "json"
    assert crawler.provider_status() == {
        "provider": "reddit",
        "configured": True,
        "provider_status": "configured",
        "primary_path": "json",
        "praw_available": reddit_module._PRAW_AVAILABLE,
        "public_json_enabled": True,
        "json_listing": True,
        "apify_configured": False,
        "apify_actor": "trudax~reddit-scraper-lite",
        "key_visible": False,
    }


def test_reddit_paths_apify_and_none(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("VKPI_REDDIT_PUBLIC_JSON_ENABLED", "0")
    monkeypatch.setenv("APIFY_TOKEN", "apify")
    assert RedditCrawler().primary_path == "apify"

    monkeypatch.delenv("APIFY_TOKEN")
    crawler = RedditCrawler()
    assert crawler.primary_path == "none"
    assert crawler.configured is False
    assert crawler.crawl_subreddit("cameras") == {
        "items": [],
        "provider_status": "not_configured",
        "sync_status": "skip",
        "error": "Neither PRAW nor Apify configured",
    }


def test_reddit_crawl_subreddit_rejects_empty_name(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    assert RedditCrawler().crawl_subreddit("r/") == {
        "items": [],
        "provider_status": "error",
        "sync_status": "fail",
        "error": "empty subreddit name",
    }


# ─── Reddit: crawl_subreddit 降级链(praw → json → apify) ───────────────


def test_reddit_subreddit_praw_failure_falls_to_json_then_apify(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APIFY_TOKEN", "apify")
    monkeypatch.setattr(reddit_module, "_PRAW_AVAILABLE", True)
    crawler = RedditCrawler()
    assert crawler.primary_path == "praw"
    order: list[str] = []

    monkeypatch.setattr(
        crawler,
        "_crawl_subreddit_via_praw",
        lambda name, limit: order.append("praw") or {"items": [], "provider_status": "error", "sync_status": "fail"},
    )
    monkeypatch.setattr(
        crawler,
        "_crawl_subreddit_via_json_api",
        lambda name, limit: order.append("json") or {"items": [], "provider_status": "error", "sync_status": "fail"},
    )
    monkeypatch.setattr(
        crawler,
        "_crawl_subreddit_via_apify",
        lambda name, limit: order.append("apify") or {"items": [{"id": "p"}], "provider_status": "ok", "sync_status": "ok"},
    )

    result = crawler.crawl_subreddit("r/cameras", limit=7)

    assert order == ["praw", "json", "apify"]
    assert result["provider_status"] == "ok"


def test_reddit_subreddit_json_path_falls_to_apify_only_with_token(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()
    calls: list[tuple[str, int]] = []
    failing = {"items": [], "provider_status": "error", "sync_status": "fail", "provider": "reddit_json"}
    monkeypatch.setattr(crawler, "_crawl_subreddit_via_json_api", lambda name, limit: calls.append((name, limit)) or failing)

    assert crawler.crawl_subreddit("cameras", limit=4) is failing
    assert calls == [("cameras", 4)]

    monkeypatch.setenv("APIFY_TOKEN", "apify")
    crawler_with_apify = RedditCrawler()
    monkeypatch.setattr(crawler_with_apify, "_crawl_subreddit_via_json_api", lambda name, limit: failing)
    monkeypatch.setattr(
        crawler_with_apify,
        "_crawl_subreddit_via_apify",
        lambda name, limit: {"items": [], "provider_status": "ok", "sync_status": "ok", "provider": "apify"},
    )
    assert crawler_with_apify.crawl_subreddit("cameras")["provider"] == "apify"


# ─── Reddit: brand mentions / post comments 门与降级链 ──────────────────


def test_reddit_brand_mentions_requires_praw(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()

    assert crawler.crawl_brand_mentions("  ") == {
        "items": [],
        "provider_status": "error",
        "sync_status": "fail",
        "error": "empty query",
    }
    assert crawler.crawl_brand_mentions("viltrox") == {
        "items": [],
        "provider_status": "not_configured",
        "sync_status": "skip",
        "error": "brand_mentions requires PRAW configuration",
    }


def test_reddit_post_comments_json_path_extracts_post_id(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()
    seen: list[str] = []

    def fake_json(post_id: str, max_depth: int = 3) -> dict[str, Any]:
        seen.append(post_id)
        return {"items": [], "provider_status": "ok", "sync_status": "ok", "provider": "reddit_json"}

    monkeypatch.setattr(crawler, "_crawl_post_comments_via_json_api", fake_json)

    result = crawler.crawl_post_comments("https://reddit.com/r/a/comments/xyz789/title/")

    assert seen == ["xyz789"]
    assert result["provider"] == "reddit_json"


def test_reddit_post_comments_falls_to_apify_with_raw_reference(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("APIFY_TOKEN", "apify")
    crawler = RedditCrawler()
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        crawler,
        "_crawl_post_comments_via_json_api",
        lambda post_id, max_depth=3: {"items": [], "provider_status": "error", "sync_status": "fail"},
    )

    def fake_apify(raw_ref: str, *, max_results: int) -> dict[str, Any]:
        captured["raw_ref"] = raw_ref
        captured["max_results"] = max_results
        return {"items": [], "provider_status": "ok", "sync_status": "ok", "provider": "apify"}

    monkeypatch.setattr(crawler, "_crawl_post_comments_via_apify", fake_apify)

    url = "https://reddit.com/r/a/comments/xyz789/title/"
    result = crawler.crawl_post_comments(url, max_results=44)

    assert captured == {"raw_ref": url, "max_results": 44}
    assert result["provider"] == "apify"


def test_reddit_post_comments_empty_reference_and_unconfigured(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    monkeypatch.setenv("VKPI_REDDIT_PUBLIC_JSON_ENABLED", "0")
    crawler = RedditCrawler()

    assert crawler.crawl_post_comments("  ") == {
        "items": [],
        "provider_status": "error",
        "sync_status": "fail",
        "error": "empty post_id",
    }
    assert crawler.crawl_post_comments("abc123") == {
        "items": [],
        "provider_status": "not_configured",
        "sync_status": "skip",
        "error": "Reddit public JSON, PRAW, and Apify are not configured",
    }


# ─── Reddit: PRAW 对象转换与评论扁平化(实例可达,拆后走 mixin) ─────────


def test_reddit_praw_post_to_dict_maps_fields(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()
    submission = SimpleNamespace(
        id="p1",
        title="Great lens",
        author=SimpleNamespace(name="reviewer"),
        subreddit=SimpleNamespace(display_name="cameras"),
        permalink="/r/cameras/comments/p1/great_lens/",
        url="https://example.com/x",
        selftext="body " * 2000,
        score=42,
        upvote_ratio=0.97,
        num_comments=7,
        created_utc=1234.0,
        is_video=False,
        is_self=True,
        over_18=False,
        stickied=False,
    )

    row = crawler._praw_post_to_dict(submission)

    assert row["type"] == "post"
    assert row["id"] == "p1"
    assert row["author"] == "reviewer"
    assert row["subreddit"] == "cameras"
    assert row["permalink"] == "https://reddit.com/r/cameras/comments/p1/great_lens/"
    assert len(row["selftext"]) == 5000
    assert row["score"] == 42
    assert row["num_comments"] == 7

    deleted = crawler._praw_post_to_dict(
        SimpleNamespace(
            id="p2",
            title="t",
            author=None,
            subreddit=SimpleNamespace(display_name="cameras"),
            permalink="/x",
            url="u",
            selftext=None,
            score=0,
            upvote_ratio=0.5,
            num_comments=0,
            created_utc=1.0,
            is_video=False,
            is_self=False,
            over_18=False,
            stickied=False,
        )
    )
    assert deleted["author"] == "[deleted]"
    assert deleted["selftext"] == ""


def test_reddit_flatten_comments_caps_depth_and_text(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()
    grandchild = SimpleNamespace(
        id="c3", author=None, body="deep", score=1, created_utc=3.0, is_submitter=False, replies=[]
    )
    child = SimpleNamespace(
        id="c2",
        author=SimpleNamespace(name="child"),
        body="x" * 6000,
        score=2,
        created_utc=2.0,
        is_submitter=True,
        replies=[grandchild],
    )
    top = SimpleNamespace(
        id="c1",
        author=SimpleNamespace(name="top"),
        body="hello",
        score=3,
        created_utc=1.0,
        is_submitter=False,
        replies=[child],
    )
    out: list[dict[str, Any]] = []

    crawler._flatten_comments([top, object()], out, depth=0, max_depth=1, parent_id=None)

    assert [(row["id"], row["depth"], row["parent_id"]) for row in out] == [
        ("c1", 0, None),
        ("c2", 1, "c1"),
    ]
    assert out[1]["author"] == "child"
    assert len(out[1]["body"]) == 5000


def test_reddit_get_praw_client_is_none_without_praw_or_creds(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()
    assert crawler._get_praw_client() is None


# ─── Reddit: V-KPI 统一接口委托 ─────────────────────────────────────────


def test_reddit_unified_interface_delegates(monkeypatch) -> None:
    _clear_reddit_env(monkeypatch)
    crawler = RedditCrawler()
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        crawler,
        "crawl_subreddit",
        lambda name, *, limit: calls.append((name, limit)) or {"items": [], "provider_status": "ok", "sync_status": "ok"},
    )

    crawler.crawl_channel_profile("https://reddit.com/r/cameras/hot", max_posts=9)
    crawler.crawl_channel_videos("r/lenses", max_posts=3)

    assert calls == [("cameras", 9), ("lenses", 3)]

    seen: dict[str, Any] = {}

    def fake_comments(post_id: str, *, max_depth: int, max_results: int) -> dict[str, Any]:
        seen.update({"post_id": post_id, "max_depth": max_depth, "max_results": max_results})
        return {"items": [], "provider_status": "ok", "sync_status": "ok"}

    monkeypatch.setattr(crawler, "crawl_post_comments", fake_comments)
    crawler.crawl_video_comments("t3_abc123", max_results=17)
    assert seen == {"post_id": "t3_abc123", "max_depth": 3, "max_results": 17}
