"""Offline outcome, no-replay and bounded refresh regression suite."""
from __future__ import annotations

import asyncio
import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.platform.apify_budget import ApifyExecutionClaimBlocked, ApifyProviderReplayBlocked
from app.platform.apify_result_contract import ActorRunError, read_actor_dataset
from app.platform.industry_crawlers.instagram_crawler import InstagramCrawler
from app.platform.industry_crawlers.tiktok_crawler import TikTokCrawler
from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler
from app.services.intelligence import account_scan_service as scan
from app.services.intelligence.account_search_terms import term_ledger_row
from app.services.intelligence.account_scan_helpers import _profile_from_items, _build_scan_result


def _client(rows=(), error=False):
    def iterate():
        yield from rows
        if error:
            raise TimeoutError("dataset unavailable")
    return SimpleNamespace(dataset=lambda _id: SimpleNamespace(iterate_items=iterate))


@pytest.mark.parametrize("state,unknown", [("RUNNING", True), ("", True), ("FAILED", False), ("TIMED-OUT", False)])
def test_nonterminal_and_failed_runs_are_not_empty_success(state, unknown):
    with pytest.raises(ActorRunError) as caught:
        read_actor_dataset(_client(), {"id": "run-1", "status": state, "defaultDatasetId": "d"})
    assert caught.value.provider_outcome_unknown is unknown
    assert caught.value.as_result("tiktok")["status"] == "failed"
    assert caught.value.retry_safe is False


def test_dataset_partial_retains_rows_and_run_identity():
    with pytest.raises(ActorRunError) as caught:
        read_actor_dataset(_client([{"id": "first"}], error=True), {"id": "run-1", "status": "SUCCEEDED", "defaultDatasetId": "d"})
    result = caught.value.as_result("instagram")
    assert result["status"] == "partial"
    assert result["items"] == [{"id": "first"}]
    assert result["metadata"]["run_id"] == "run-1"
    assert result["metadata"]["provider_outcome_unknown"] is False
    assert result["metadata"]["exhaustion_proven"] is False


def test_successful_empty_dataset_is_not_failure():
    assert read_actor_dataset(_client(), {"status": "SUCCEEDED", "defaultDatasetId": "d"}) == []


def test_missing_dataset_is_failure():
    with pytest.raises(ActorRunError, match="actor_dataset_missing"):
        read_actor_dataset(_client(), {"status": "SUCCEEDED"})


def test_runner_missing_client_starts_nothing(monkeypatch):
    monkeypatch.setattr(scan, "_client", lambda: None)
    monkeypatch.setattr(scan, "call_apify_actor", lambda *_a, **_kw: pytest.fail("provider called"))
    with pytest.raises(ActorRunError, match="actor_not_configured"):
        asyncio.run(scan._run_actor("actor", {}))


@pytest.mark.parametrize("error", [TimeoutError("unknown"), ApifyExecutionClaimBlocked("claim"), ApifyProviderReplayBlocked("unknown")])
def test_runner_propagates_fences_and_marks_unknown_start(monkeypatch, error):
    calls = []
    def invoke(*_args, **_kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(scan, "_client", lambda: _client())
    monkeypatch.setattr(scan, "call_apify_actor", invoke)
    expected = ActorRunError if isinstance(error, TimeoutError) else type(error)
    with pytest.raises(expected) as caught:
        asyncio.run(scan._run_actor("actor", {}))
    assert calls == [1]
    if expected is ActorRunError:
        assert caught.value.provider_outcome_unknown is True


@pytest.mark.parametrize("first", [
    {"provider_status": "error", "provider_outcome_unknown": True},
    {"provider_status": "budget_blocked"},
    {"provider_status": "error", "status": "failed"},
])
def test_instagram_failure_never_starts_details_fallback(monkeypatch, first):
    crawler = InstagramCrawler(api_token="test")
    calls = []
    def run(*_a, **_kw):
        calls.append(1)
        return {**first, "items": []}
    monkeypatch.setattr(crawler, "_start_run", run)
    result = crawler.crawl_channel_profile("@person")
    assert len(calls) == 1
    assert result["provider_status"] == first["provider_status"]


def test_instagram_known_empty_fallback_failure_is_visible(monkeypatch):
    crawler = InstagramCrawler(api_token="test")
    outcomes = iter([
        {"provider_status": "ok", "items": []},
        {"provider_status": "error", "status": "failed", "items": [], "provider_outcome_unknown": True},
    ])
    monkeypatch.setattr(crawler, "_start_run", lambda *_a, **_kw: next(outcomes))
    result = crawler.crawl_channel_profile("@person", since="2026-08-01")
    assert result["status"] == "failed"
    assert result["provider_outcome_unknown"] is True
    assert result["metadata"]["date_filter"] == "client_window_only"


@pytest.mark.parametrize("crawler_cls", [InstagramCrawler, TikTokCrawler])
@pytest.mark.parametrize("operation", ["crawl_channel_profile", "crawl_channel_videos"])
def test_actor_dates_are_requested_not_claimed_complete(monkeypatch, crawler_cls, operation):
    crawler = crawler_cls(api_token="test")
    inputs = []
    def run(payload, **_kw):
        inputs.append(payload)
        return {"provider_status": "ok", "items": [{"id": "one"}]}
    monkeypatch.setattr(crawler, "_start_run", run)
    result = getattr(crawler, operation)("person", since="2026-08-01")
    assert result["metadata"]["date_window_complete"] is False
    assert result["metadata"]["exhaustion_proven"] is False
    assert result["metadata"]["pagination_supported"] is False
    assert "pageToken" not in inputs[0]


def _youtube(monkeypatch, pages, video_rows=None):
    crawler = YouTubeCrawler(api_key="test", apify_token="unused")
    calls = []
    def request(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "channels":
            return {"provider_status": "ok", "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
        if endpoint == "playlistItems":
            return pages[str(params.get("pageToken") or "")]
        if endpoint == "videos":
            ids = str(params["id"]).split(",")
            return {"provider_status": "ok", "items": video_rows if video_rows is not None else [{"id": key} for key in ids]}
        pytest.fail("search endpoint must not run for known uploads")
    monkeypatch.setattr(crawler, "_request", request)
    monkeypatch.setattr(crawler, "_start_apify_run", lambda *_a, **_kw: pytest.fail("fallback must not run"))
    return crawler, calls


def test_since_uses_playlist_dates_without_assuming_sort_order(monkeypatch):
    crawler, calls = _youtube(monkeypatch, {"": {"provider_status": "ok", "items": [
        {"contentDetails": {"videoId": "old", "videoPublishedAt": "2025-01-01T00:00:00Z"}},
        {"contentDetails": {"videoId": "new", "videoPublishedAt": "2026-08-02T00:00:00Z"}},
    ]}})
    result = crawler.crawl_channel_videos("UCperson", max_results=5, since="2026-08-01")
    assert [row["id"] for row in result["items"]] == ["new"]
    assert result["status"] == "done"
    assert result["metadata"]["date_window_complete"] is True
    assert result["metadata"]["youtube_search_calls"] == 0
    assert result["metadata"]["youtube_combined_quota_units"] == 3
    assert [entry[0] for entry in calls] == ["channels", "playlistItems", "videos"]


def test_since_scan_bound_is_partial_not_exhausted(monkeypatch):
    crawler, calls = _youtube(monkeypatch, {"": {"provider_status": "ok", "nextPageToken": "P2", "items": [
        {"contentDetails": {"videoId": "old", "videoPublishedAt": "2025-01-01T00:00:00Z"}},
    ]}})
    result = crawler.crawl_channel_videos("UCperson", max_results=5, since="2026-08-01")
    assert result["status"] == "partial"
    assert result["metadata"]["has_more"] is True
    assert result["metadata"]["date_window_complete"] is False
    assert len(calls) == 2


def test_since_unknown_date_not_invented(monkeypatch):
    crawler, _ = _youtube(monkeypatch, {"": {"provider_status": "ok", "items": [
        {"contentDetails": {"videoId": "unknown"}},
    ]}})
    result = crawler.crawl_channel_videos("UCperson", since="2026-08-01")
    assert result["status"] == "partial"
    assert result["items"] == []
    assert result["metadata"]["date_unknown_count"] == 1


def test_quota_ledger_uses_independent_buckets():
    row = term_ledger_row("photographer")
    assert row["youtube_search_calls"] == 1
    assert row["quota_units"] == row["youtube_combined_quota_units"] == 0
    assert term_ledger_row("skipped", quota_units=0)["youtube_search_calls"] == 0


@pytest.mark.parametrize("raw,expected", [(None, None), ("hidden", None), ("12,345", 12345), (0, 0), (False, None)])
def test_missing_followers_are_not_zero(raw, expected):
    profile = _profile_from_items("instagram", "person", [{"username": "person", "followersCount": raw, "likesCount": 9999}])
    assert profile["follower_count"] == expected
    assert profile["followers_known"] is (expected is not None)
    assert _build_scan_result("instagram", "person", [], 0, profile)["follower_count"] == expected


@pytest.mark.parametrize("error", [ApifyExecutionClaimBlocked("claim"), ApifyProviderReplayBlocked("unknown")])
def test_ig_enrichment_never_swallows_execution_fences(monkeypatch, error):
    async def actor(actor_id, *_args, **_kwargs):
        if "hashtag" in actor_id:
            return [{"ownerUsername": "person", "caption": "photographer"}]
        raise error
    monkeypatch.setattr(scan, "provider_ready", lambda: True)
    monkeypatch.setattr(scan, "_run_actor", actor)
    with pytest.raises(type(error)):
        asyncio.run(scan.search_platform_content("instagram", "photographer"))


@pytest.mark.parametrize("platform", ["youtube", "instagram", "tiktok"])
@pytest.mark.parametrize("mode", ["partial", "unknown", "claim", "pending", "missing_status"])
def test_crawler_actor_boundary_keeps_partial_and_execution_fences(monkeypatch, platform, mode):
    import apify_client
    from app.platform import industry_crawlers

    module = importlib.import_module(f"app.platform.industry_crawlers.{platform}_crawler")
    fake = _client([{"id": "one"}], error=True)
    monkeypatch.setattr(apify_client, "ApifyClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(industry_crawlers, "record_apify_run_cost", lambda *_a, **_kw: pytest.fail("incomplete run must not settle"))
    calls = []
    def invoke(*_a, **_kw):
        calls.append(1)
        if mode == "unknown":
            raise TimeoutError("already possibly billed")
        if mode == "claim":
            raise ApifyExecutionClaimBlocked("claim unavailable")
        return {"id": "run-1", "status": "RUNNING" if mode == "pending" else "" if mode == "missing_status" else "SUCCEEDED", "defaultDatasetId": "dataset"}
    monkeypatch.setattr(module, "call_apify_actor", invoke)
    if platform == "youtube":
        crawler = YouTubeCrawler(api_key="test", apify_token="test")
        run = lambda: crawler._start_apify_run({}, operation="test")
    else:
        crawler = (InstagramCrawler if platform == "instagram" else TikTokCrawler)(api_token="test")
        run = lambda: crawler._start_run({})
    if mode == "claim":
        with pytest.raises(ApifyExecutionClaimBlocked):
            run()
    else:
        result = run()
        assert result["status"] == ("partial" if mode == "partial" else "failed")
        assert result["provider_outcome_unknown"] is (mode in {"unknown", "pending", "missing_status"})
        assert result["retry_safe"] is False
        assert result["items"] == ([{"id": "one"}] if mode == "partial" else [])
    assert calls == [1]


def test_invalid_youtube_since_does_not_start_provider(monkeypatch):
    crawler = YouTubeCrawler(api_key="test")
    monkeypatch.setattr(crawler, "_request", lambda *_a, **_kw: pytest.fail("invalid date started request"))
    assert crawler.crawl_channel_videos("UCperson", since="2026-99-99")["error_code"] == "invalid_since"


def test_instagram_account_profile_failure_keeps_successful_posts(monkeypatch):
    calls = []
    async def actor(_actor_id, payload, **_kwargs):
        calls.append(payload["resultsType"])
        if payload["resultsType"] == "posts":
            return [{"ownerUsername": "person", "caption": "work", "url": "https://instagram.com/p/post"}]
        raise ActorRunError("actor_provider_failed", provider_outcome_unknown=True)
    monkeypatch.setattr(scan, "_run_actor", actor)
    result = asyncio.run(scan.scan_instagram_account("person", max_posts=1))
    assert calls == ["posts", "details"]
    assert result["status"] == "partial"
    assert len(result["posts"]) == 1
    assert result["metadata"]["provider_outcome_unknown"] is True
    assert result["follower_count"] is None


@pytest.mark.parametrize("state,error", [("RUNNING", False), ("", False), ("SUCCEEDED", True), ("SUCCEEDED", False)])
def test_runner_settles_only_complete_dataset_and_records_item_count(monkeypatch, state, error):
    from app.domains.costs import budget_guard

    recorded = []
    monkeypatch.setattr(scan, "_client", lambda: _client([{"id": "one"}], error=error))
    monkeypatch.setattr(scan, "call_apify_actor", lambda *_a, **_kw: {"id": "run-1", "status": state, "defaultDatasetId": "d"})
    monkeypatch.setattr(budget_guard, "record_apify_run", lambda *_a, **kw: recorded.append(kw))
    if state != "SUCCEEDED" or error:
        with pytest.raises(ActorRunError):
            asyncio.run(scan._run_actor("actor", {}))
        assert recorded == []
    else:
        assert asyncio.run(scan._run_actor("actor", {})) == [{"id": "one"}]
        assert recorded[0]["dataset_item_count"] == 1


def test_dataset_overflow_is_bounded_partial_without_settlement(monkeypatch):
    from app.domains.costs import budget_guard

    fetched = []
    def rows():
        for index in range(100_000):
            fetched.append(index)
            yield {"id": index}
    client = SimpleNamespace(dataset=lambda _id: SimpleNamespace(iterate_items=rows))
    monkeypatch.setattr(scan, "_client", lambda: client)
    monkeypatch.setattr(scan, "call_apify_actor", lambda *_a, **_kw: {"id": "existing-run", "status": "SUCCEEDED", "defaultDatasetId": "d"})
    monkeypatch.setattr(budget_guard, "record_apify_run", lambda *_a, **_kw: pytest.fail("overflow must retain reservation"))
    with pytest.raises(ActorRunError) as caught:
        asyncio.run(scan._run_actor("actor", {}))
    result = caught.value.as_result("tiktok")
    assert result["status"] == "partial"
    assert result["metadata"]["limit_reached"] is True
    assert result["metadata"]["run_id"] == "existing-run"
    assert result["metadata"]["retry_safe"] is False
    assert len(result["items"]) == 2000
    assert len(fetched) == 2001  # one bounded probe proves overflow


def test_incremental_upload_helpers_remain_within_complexity_ceiling():
    from scripts.vkpi_engineering_health_collect import collect_complexity

    path = "backend/app/platform/industry_crawlers/youtube_crawler_incremental.py"
    root = Path(__file__).resolve().parents[1]
    rows = collect_complexity({path: ast.parse((root / path).read_text(encoding="utf-8"))})
    by_name = {row.qualified_name: row.cc for row in rows}
    assert {"crawl_incremental_uploads", "_filter_dated_items"} <= set(by_name)
    assert all(complexity <= 40 for complexity in by_name.values()), by_name
