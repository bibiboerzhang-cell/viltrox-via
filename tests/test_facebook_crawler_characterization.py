"""FacebookCrawler characterization:拆类前先锁行为(class-LOC 棘轮 418→≤400 波)。

锁定对象 = 不碰网络就能走到的全部分支:
  - 配置探测(configured / primary_path / provider_status);
  - 路由壳(crawl_page_profile / crawl_brand_mentions / crawl_channel_* / crawl_video_comments
    的空输入、未配置、meta_graph 占位、apify 占位分支);
  - URL 规整三助手的逐字输出。

约定:全部断言按拆分前 facebook_crawler.py 的现值逐字冻结;拆分只许搬家不许改词。
"""
from __future__ import annotations

import pytest

from app.platform.industry_crawlers.facebook_crawler import FacebookCrawler

ENV_KEYS = (
    "APIFY_TOKEN",
    "APIFY_FACEBOOK_PAGES_ACTOR_ID",
    "APIFY_FACEBOOK_POSTS_ACTOR_ID",
    "APIFY_FACEBOOK_COMMENTS_ACTOR_ID",
    "APIFY_FACEBOOK_RUN_TIMEOUT_SECONDS",
    "META_GRAPH_ACCESS_TOKEN",
    "META_GRAPH_API_VERSION",
)


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_unconfigured_probe_surface_is_frozen(clean_env: pytest.MonkeyPatch) -> None:
    crawler = FacebookCrawler()
    assert crawler.configured is False
    assert crawler.primary_path == "none"
    assert crawler.pages_actor == "apify~facebook-pages-scraper"
    assert crawler.posts_actor == "apify~facebook-posts-scraper"
    assert crawler.meta_version == "v18.0"
    assert crawler.run_timeout_seconds == 420
    assert crawler.provider_status() == {
        "provider": "facebook",
        "configured": False,
        "provider_status": "not_configured",
        "primary_path": "none",
        "pages_actor": "apify~facebook-pages-scraper",
        "posts_actor": "apify~facebook-posts-scraper",
        "meta_graph_reserved": False,
        "key_visible": False,
    }


def test_env_overrides_and_path_priority(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("APIFY_TOKEN", "t-apify")
    clean_env.setenv("META_GRAPH_ACCESS_TOKEN", "t-meta")
    clean_env.setenv("APIFY_FACEBOOK_PAGES_ACTOR_ID", "acme/pages")
    clean_env.setenv("APIFY_FACEBOOK_POSTS_ACTOR_ID", "acme/posts")
    clean_env.setenv("APIFY_FACEBOOK_RUN_TIMEOUT_SECONDS", "9999")
    crawler = FacebookCrawler()
    assert crawler.configured is True
    assert crawler.primary_path == "apify"  # apify 优先于 meta_graph
    assert crawler.pages_actor == "acme~pages"
    assert crawler.posts_actor == "acme~posts"
    assert crawler.run_timeout_seconds == 900  # clamp 上限
    status = crawler.provider_status()
    assert status["provider_status"] == "configured"
    assert status["meta_graph_reserved"] is True
    assert status["key_visible"] is False
    assert "t-apify" not in str(status) and "t-meta" not in str(status)


def test_meta_only_falls_back_to_meta_graph_path(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("META_GRAPH_ACCESS_TOKEN", "t-meta")
    crawler = FacebookCrawler()
    assert crawler.primary_path == "meta_graph"
    result = crawler.crawl_page_profile("somepage")
    assert result == {
        "items": [],
        "provider_status": "not_implemented",
        "sync_status": "skip",
        "provider": "meta_graph",
        "error": (
            "Meta Graph API not implemented in P1.2. "
            "Reserved for long-term migration after Viltrox Meta App Review."
        ),
    }


def test_crawl_page_profile_guard_branches(clean_env: pytest.MonkeyPatch) -> None:
    crawler = FacebookCrawler()
    assert crawler.crawl_page_profile("   ") == {
        "items": [],
        "provider_status": "error",
        "sync_status": "fail",
        "error": "empty page_url",
    }
    assert crawler.crawl_page_profile("somepage") == {
        "items": [],
        "provider_status": "not_configured",
        "sync_status": "skip",
        "error": "Neither Apify nor Meta Graph configured",
    }


def test_crawl_brand_mentions_branches(clean_env: pytest.MonkeyPatch) -> None:
    crawler = FacebookCrawler()
    assert crawler.crawl_brand_mentions("  ") == {
        "items": [],
        "provider_status": "error",
        "sync_status": "fail",
        "error": "empty query",
    }
    assert crawler.crawl_brand_mentions("viltrox") == {
        "items": [],
        "provider_status": "not_supported",
        "sync_status": "skip",
        "error": "brand mention search not supported in P1.2",
    }
    clean_env.setenv("APIFY_TOKEN", "t-apify")
    apify_result = FacebookCrawler().crawl_brand_mentions("viltrox", limit=7)
    assert apify_result == {
        "items": [],
        "provider_status": "not_supported",
        "sync_status": "skip",
        "provider": "apify",
        "error": (
            "Facebook brand mention search not supported in P1.2. "
            "Use Page profile monitoring instead. "
            "Team: consider Meta Graph API for proper search (long-term)."
        ),
        "query": "viltrox",
    }


def test_channel_interfaces_route_through_page_profile(
    clean_env: pytest.MonkeyPatch,
) -> None:
    crawler = FacebookCrawler()
    seen: list[tuple[str, int]] = []

    def spy(page_url: str, *, max_posts: int = 10) -> dict:
        seen.append((page_url, max_posts))
        return {"items": [], "provider_status": "ok", "sync_status": "ok"}

    clean_env.setattr(crawler, "crawl_page_profile", spy)
    crawler.crawl_channel_profile("@viltrox", max_posts=5)
    crawler.crawl_channel_profile("ignored", channel_id="viltroxpage", max_posts=3)
    crawler.crawl_channel_videos("@viltrox", max_posts=2)
    assert seen == [
        ("https://www.facebook.com/viltrox", 5),
        ("https://www.facebook.com/viltroxpage", 3),
        ("https://www.facebook.com/viltrox", 2),
    ]


def test_crawl_video_comments_not_configured_short_circuits(
    clean_env: pytest.MonkeyPatch,
) -> None:
    # token 检查在 URL 规整之前:空 token 时连空 id 也直接 skip。
    crawler = FacebookCrawler()
    expected = {
        "items": [],
        "provider_status": "not_configured",
        "sync_status": "skip",
        "provider": "apify",
    }
    assert crawler.crawl_video_comments("12345") == expected
    assert crawler.crawl_video_comments("") == expected


def test_crawl_video_comments_empty_post_url_fails(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("APIFY_TOKEN", "t-apify")
    crawler = FacebookCrawler()
    assert crawler.crawl_video_comments("   ") == {
        "items": [],
        "provider_status": "error",
        "sync_status": "fail",
        "provider": "apify",
        "error": "could not construct Facebook post URL",
    }


def test_normalize_page_url_verbatim() -> None:
    normalize = FacebookCrawler._normalize_page_url
    assert normalize("") == ""
    assert normalize("   ") == ""
    assert normalize("https://www.facebook.com/viltrox/") == "https://www.facebook.com/viltrox"
    assert normalize("http://facebook.com/viltrox") == "http://facebook.com/viltrox"
    assert normalize("facebook.com/viltrox") == "https://www.facebook.com/viltrox"
    assert normalize("www.facebook.com/viltrox") == "https://www.facebook.com/viltrox"
    assert normalize("@viltrox") == "https://www.facebook.com/viltrox"
    assert normalize("/viltrox/") == "https://www.facebook.com/viltrox"
    assert normalize("viltrox") == "https://www.facebook.com/viltrox"


def test_handle_to_page_url_channel_id_wins() -> None:
    to_url = FacebookCrawler._handle_to_page_url
    assert to_url("@handle", "pageid") == "https://www.facebook.com/pageid"
    assert to_url("@handle", "   ") == "https://www.facebook.com/handle"
    assert to_url("@handle") == "https://www.facebook.com/handle"


def test_normalize_post_url_verbatim() -> None:
    normalize = FacebookCrawler._normalize_post_url
    assert normalize("") == ""
    assert normalize(None) == ""
    assert normalize("https://www.facebook.com/reel/1") == "https://www.facebook.com/reel/1"
    assert normalize("http://fb.example/p/2") == "http://fb.example/p/2"
    assert normalize("12345") == "https://www.facebook.com/posts/12345"
    assert normalize("/12345/") == "https://www.facebook.com/posts/12345"
