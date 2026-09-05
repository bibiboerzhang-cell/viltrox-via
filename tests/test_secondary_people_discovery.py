from __future__ import annotations

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.platform.apify_budget import ApifyExecutionClaimBlocked
from app.platform.apify_result_contract import ActorRunError
from app.platform.industry_crawlers.reddit_crawler import RedditCrawler
from app.platform.industry_crawlers.x_crawler import XCrawler
from app.platform.industry_crawlers.reddit_people_normalize import author_candidates, person_handle, x_records
from app.services.intelligence import account_scan_service, account_search_secondary as secondary
from app.services.intelligence.account_search_discovery import search_platform_content
from app.domains.kol import (
    profile_online_identity as identity, profile_online_qualification as qualification,
    profile_recall_activity_gate as activity, search_sessions_serde as serde,
    url_deep_crawl as deep, url_deep_crawl_execute as execution,
    url_deep_crawl_execute_profile_data as profile_data,
    url_deep_crawl_maintenance_fence as fence,
)


def tweet(**overrides):
    return {"id": "987654321012345678", "author": {
        "id": "44196397", "userName": "LensMaker", "name": "Lens Maker",
        "followers": 5000, "description": "portrait lighting studio tutorial creator",
    }, "text": "portrait lighting studio tutorial", "createdAt": "2026-09-01T00:00:00Z",
        "lang": "en", "likeCount": 10, **overrides}


@pytest.fixture(autouse=True)
def no_live(monkeypatch):
    for key in ("APIFY_TOKEN", "X_BEARER_TOKEN", "APIFY_X_ACTOR_ID", "APIFY_X_SEARCH_ACTOR_ID",
                "APIFY_X_ACCOUNT_ACTOR_ID", "APIFY_TWITTER_ACCOUNT_ACTOR_ID", "REDDIT_CLIENT_ID",
                "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.delenv(key, raising=False)
    async def forbidden(*_args, **_kwargs):
        pytest.fail("unexpected paid actor start")
    monkeypatch.setattr(account_scan_service, "_run_actor", forbidden)


@pytest.fixture
def x_enabled(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "fixture-no-network")
    monkeypatch.setenv("APIFY_X_ACTOR_ID", "apidojo/twitter-scraper-lite")


@pytest.mark.parametrize("platform,url", [
    ("x", "https://x.com.evil.test/LensMaker"), ("x", "https://evil.test/x.com/LensMaker"),
    ("x", "https://x.com/LensMaker/status/123"), ("x", "https://x.com:bad/LensMaker"),
    ("reddit", "https://reddit.com/r/LensMaker/"), ("reddit", "r/LensMaker"),
    ("reddit", "https://reddit.com/user/LensMaker/submitted/"),
    ("reddit", "https://reddit.com.evil.test/user/LensMaker/"),
])
def test_person_locators_reject_content_community_and_spoofs(platform, url):
    assert person_handle(platform, url) == ""
    result = asyncio.run(secondary.scan_secondary_profile(platform, url))
    assert result["status"] == "invalid_person_identity"
    assert result["metadata"]["provider_calls"] == 0


def test_x_only_bearer_is_not_budget_authority(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "fixture-no-network")
    result = asyncio.run(search_platform_content("x", "portrait photographer"))
    assert result["status"] == "not_configured"
    assert result["metadata"]["error_code"] == "official_budget_binding_required"


def test_x_discovery_normalizes_author_and_exact_query(x_enabled, monkeypatch):
    calls = []
    async def run(actor, payload, timeout):
        calls.append((actor, payload, timeout))
        return [tweet(), tweet(), tweet(id="987654321012345679", author={"userName": "Unknown"})]
    monkeypatch.setattr(account_scan_service, "_run_actor", run)
    result = asyncio.run(search_platform_content("twitter", "portrait photographer", market="US", max_results=8, exact_query=True))
    assert calls[0][1] == {"searchTerms": ["portrait photographer"], "maxItems": 8, "sort": "Latest"}
    assert result["status"] == "partial"
    row, = result["items"]
    assert row["platform_user_id"] == "44196397"
    assert row["profile_url"] == "https://x.com/LensMaker"
    assert row["followers"] == 5000
    assert len(row["posts"]) == 1
    assert row["representative_evidence"][0]["evidence_type"] == "post"
    assert result["metadata"]["has_more"] is False
    assert row.get("country") != "US"  # the requested market is not evidence


def test_x_partial_retains_rows_without_replacement_actor(x_enabled, monkeypatch):
    calls = []
    async def run(*args, **kwargs):
        calls.append(args)
        raise ActorRunError("dataset_failed", partial_items=[tweet()], provider_outcome_unknown=True)
    monkeypatch.setattr(account_scan_service, "_run_actor", run)
    result = asyncio.run(search_platform_content("x", "portrait"))
    assert result["status"] == "partial" and len(result["items"]) == 1
    assert result["metadata"]["provider_outcome_unknown"] is True
    assert result["metadata"]["retry_safe"] is False
    assert len(calls) == 1


def test_x_budget_fence_propagates(x_enabled, monkeypatch):
    async def blocked(*args, **kwargs):
        raise ApifyExecutionClaimBlocked("fixture lease denied")
    monkeypatch.setattr(account_scan_service, "_run_actor", blocked)
    with pytest.raises(ApifyExecutionClaimBlocked):
        asyncio.run(search_platform_content("x", "portrait"))


def test_x_scan_account_calls_secondary_not_legacy(x_enabled, monkeypatch):
    async def run(*args, **kwargs):
        return [tweet(), tweet(id="987654321012345679", author={"id": "23456789", "userName": "OtherPerson"})]
    monkeypatch.setattr(account_scan_service, "_run_actor", run)
    result = asyncio.run(account_scan_service.scan_account("twitter", "https://x.com/LensMaker", 12))
    assert result["platform"] == "x" and result["handle"] == "LensMaker"
    assert result["status"] == "partial" and len(result["posts"]) == 1
    assert result["stats"]["total_posts"] == 1


def reddit_client(monkeypatch, *, error=None):
    from app.platform.industry_crawlers import reddit_praw_path
    monkeypatch.setattr(reddit_praw_path, "_PRAW_AVAILABLE", True)
    for key in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USER_AGENT"):
        monkeypatch.setenv(key, "fixture-no-network")
    author = SimpleNamespace(name="LensMaker", id="abc123", is_suspended=False,
        subreddit={"title": "Lens Maker", "public_description": "portrait creator", "subscribers": 900000},
        comment_karma=800000, link_karma=700000, icon_img="")
    post = SimpleNamespace(id="def456", author=author, title="portrait photography",
        selftext="studio lighting tutorial", created_utc=1788220800, score=5, num_comments=3,
        subreddit=SimpleNamespace(display_name="Photography", subscribers=1000000))
    def listing(*args, **kwargs):
        if error:
            raise error
        return iter([post])
    author.submissions = SimpleNamespace(new=listing)
    client = SimpleNamespace(subreddit=lambda _: SimpleNamespace(search=listing), redditor=lambda _: author)
    monkeypatch.setattr(RedditCrawler, "_get_praw_client", lambda _: client)
    return author


def test_reddit_public_json_is_not_person_authorization():
    assert RedditCrawler().configured is True
    result = asyncio.run(search_platform_content("reddit", "portrait photographer"))
    assert result["status"] == "not_configured" and result["items"] == []
    assert result["capabilities"]["followers_unavailable"] is True


def test_reddit_discovers_real_author_not_community_metrics(monkeypatch):
    reddit_client(monkeypatch)
    result = asyncio.run(search_platform_content("reddit", "portrait"))
    row, = result["items"]
    assert row["platform_user_id"] == "t2_abc123" and row["handle"] == "LensMaker"
    assert row["profile_url"] == "https://www.reddit.com/user/LensMaker/"
    assert row["followers"] is None and row["account_metrics"]["link_karma"] == 700000
    assert row["posts"][0]["community"] == "Photography"
    assert row["audience_market_distribution"] is None
    assert result["capabilities"]["qualification_pending_reason"] == "followers_unknown"
    refreshed = asyncio.run(account_scan_service.scan_account("reddit", row["profile_url"], 12))
    assert refreshed["status"] == "done" and refreshed["profile"]["platform_user_id"] == "t2_abc123"
    assert refreshed["follower_count"] is None


@pytest.mark.parametrize("code,expected", [(401, "authentication_required"), (403, "permission_denied"), (429, "rate_limited")])
def test_reddit_errors_do_not_become_empty_or_fallback(monkeypatch, code, expected):
    error = RuntimeError("secret raw provider text")
    error.response = SimpleNamespace(status_code=code)
    reddit_client(monkeypatch, error=error)
    result = asyncio.run(search_platform_content("reddit", "portrait"))
    assert result["status"] == "failed" and result["metadata"]["error_code"] == expected
    assert "secret" not in str(result)


@pytest.mark.parametrize("platform,url", [("x", "https://x.com/LensMaker"), ("reddit", "https://www.reddit.com/user/LensMaker/")])
def test_secondary_profile_refresh_projection_and_fence(platform, url, monkeypatch):
    if platform == "reddit":
        reddit_client(monkeypatch)
    else:
        from app.platform.industry_crawlers import x_crawler
        monkeypatch.setenv("APIFY_TOKEN", "fixture-no-network")
        monkeypatch.setenv("APIFY_X_ACTOR_ID", "apidojo/twitter-scraper-lite")
        monkeypatch.setattr(x_crawler, "managed_apify_client", lambda _: nullcontext(object()))
        calls = []
        monkeypatch.setattr(x_crawler, "call_apify_actor", lambda *args, **kwargs: calls.append(kwargs) or {"status": "SUCCEEDED", "defaultDatasetId": "fixture"})
        import app.platform.apify_result_contract as contract
        import app.platform.industry_crawlers as crawlers
        monkeypatch.setattr(contract, "read_actor_dataset", lambda *_: [tweet()])
        monkeypatch.setattr(crawlers, "record_apify_run_cost", lambda *args, **kwargs: None)
    classified = deep.classify_url(url)
    assert classified.url_type == "profile" and classified.platform == platform
    crawl = profile_data._crawl_profile_basics(classified, target=url, max_posts=12)
    assert crawl["status"] == "synced" and crawl["videos_items"] == []
    projected = profile_data._profile_data_from_crawl(classified, crawl, existing_match={}, max_posts=12)
    assert projected["profile_url"] == url
    assert '"content_kind": "post"' in projected["raw_platform_data"]
    assert "last_video_at" not in projected  # not a fake video timestamp
    target_fence = fence._validated_maintenance_profile_identity({**projected, "handle": "LensMaker"}, url)
    assert target_fence["stable_native_ids"]
    execution._verify_maintenance_crawl_identity({"maintenance_refresh": True, "maintenance_target_fence": target_fence}, crawl)
    if platform == "x":
        assert len(calls) == 1 and calls[0]["platform"] == "x"


def test_secondary_post_activity_is_explicit_and_platform_limited():
    records, _ = x_records([tweet()])
    candidate, = author_candidates(records, limit=1)
    evidence = identity.latest_video_evidence(candidate)
    assert evidence["evidence_type"] == "post" and evidence["content_kind"] == "post"
    kwargs = dict(latest=evidence, now=datetime(2026, 9, 4, tzinfo=timezone.utc), max_video_age_days=90, fresh_priority_days=30)
    assert activity.evaluate_activity(**kwargs)["passed"] is True
    assert activity.evaluate_activity(**{**kwargs, "latest": {**evidence, "platform": "youtube"}})["passed"] is False
    assert activity.evaluate_activity(**{**kwargs, "latest": {**evidence, "source": "unverified"}})["passed"] is False
    assert identity.is_platform_video_url("https://reddit.com.evil.test/comments/def456", platform="reddit") is False
    assert serde.project_public_profile_url("reddit", "LensMaker", "https://reddit.com/r/LensMaker/") == ""


@pytest.mark.parametrize("flag,reason", [("provider_outcome_unknown", "provider_outcome_unknown"), ("dispatch_blocked", "provider_dispatch_blocked")])
def test_unknown_provider_round_is_not_exhaustion(flag, reason):
    async def fetch(**kwargs):
        return {"status": "partial", flag: True, "items": [], "has_more": True, "next_cursor": "do-not-use"}
    async def enroll(_):
        pytest.fail("unknown outcomes must not enroll")
    result = asyncio.run(qualification.collect_strict_online_candidates(query_text="portrait", policy=qualification.online_policy(),
        local_canonical_keys=set(), fetch_batch=fetch, enroll_candidate=enroll))
    assert result["provider_rounds"] == 1 and result["exhausted"] is False
    assert result["shortfall_reasons"][reason] == 30


def test_real_author_followers_pass_but_reddit_karma_remains_pending(monkeypatch):
    reddit_client(monkeypatch)
    reddit = asyncio.run(search_platform_content("reddit", "portrait lighting"))["items"][0]
    records, _ = x_records([tweet()])
    x = author_candidates(records, limit=1)[0]
    for candidate in (x, reddit):
        candidate.update(country="US", country_source="platform_profile", language="en", language_source="platform_profile",
                         profile_type="creator", profile_type_source="provider_declared")
    policy = qualification.online_policy(market="US", platforms=["x", "reddit"], languages=["en"],
                                          profile_types=["creator"], followers_min=3000)
    result = qualification.qualify_online_candidates([x, reddit], query_text="portrait lighting", policy=policy,
                                                     as_of=datetime(2026, 9, 4, tzinfo=timezone.utc))
    assert len(result["accepted"]) == 1 and result["accepted"][0]["platform"] == "x"
    assert result["counts"].get("pending") == 1
    assert result["rejected_by_reason"]["followers_unknown"] == 1


def test_saved_post_evidence_is_author_bound_and_does_not_become_video():
    from app.domains.kol.profile_online_post_evidence import SCHEMA_KEY, build_post_evidence, read_post_evidence
    records, _ = x_records([tweet()])
    row = author_candidates(records, limit=1)[0]
    schema = build_post_evidence(row, row["posts"])
    row["raw_platform_data"] = {"online_identity_v1": {"platform_user_id": row["platform_user_id"]}, SCHEMA_KEY: schema}
    evidence = read_post_evidence(row)
    assert len(evidence) == 1 and evidence[0]["evidence_type"] == "post"
    assert read_post_evidence({**row, "platform_user_id": "12345678"}) == []
    assert read_post_evidence({**row, "handle": "OtherPerson"}) == []
    for invalid in ({**schema, "version": 2}, {**schema, "author_id": "12345678"}):
        assert read_post_evidence({**row, "raw_platform_data": {**row["raw_platform_data"], SCHEMA_KEY: invalid}}) == []
    for bad in ({"url": "https://x.com.evil.test/LensMaker/status/987654321012345678"},
                {"author_id": "12345678"}, {"published": "2999-01-01T00:00:00Z"},
                {"content_id": "not-the-url-id"}):
        assert build_post_evidence(row, [{**row["posts"][0], **bad}])["posts"] == []


def test_local_context_reads_secondary_posts_without_video_table(monkeypatch):
    from app.domains.kol.profile_online_post_evidence import SCHEMA_KEY, build_post_evidence
    from app.domains.kol import profile_recall_support as support
    records, _ = x_records([tweet()])
    candidate = author_candidates(records, limit=1)[0]
    raw = {"online_identity_v1": {"platform_user_id": candidate["platform_user_id"]},
           SCHEMA_KEY: build_post_evidence(candidate, candidate["posts"])}
    conn = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: [{"kol_pool_id": 7, "raw_platform_data": raw}]))
    monkeypatch.setattr(support, "attach_private_content_evidence", lambda evidence, **kwargs: evidence)
    rows, evidence = support.smart_local_qualification_context([7], rows_by_id={7: candidate}, evidence_by_id={},
        get_connection=lambda: conn, table_columns=lambda _, table: {"raw_platform_data"} if table == "vkpi_kol_pool" else set())
    assert evidence[7]["latest_real_video"]["evidence_type"] == "post"
    assert evidence[7]["latest_real_video"]["content_kind"] == "post"


def test_x_incomplete_refresh_never_settles_cost(x_enabled, monkeypatch):
    from app.platform.industry_crawlers import x_crawler
    import app.platform.industry_crawlers as crawlers
    monkeypatch.setattr(x_crawler, "managed_apify_client", lambda _: nullcontext(object()))
    monkeypatch.setattr(x_crawler, "call_apify_actor", lambda *args, **kwargs: {"status": "RUNNING", "id": "fixture"})
    monkeypatch.setattr(crawlers, "record_apify_run_cost", lambda *args, **kwargs: pytest.fail("must retain unresolved reservation"))
    result = XCrawler().crawl_person_profile("LensMaker")
    assert result["status"] == "failed" and result["metadata"]["provider_outcome_unknown"] is True


@pytest.mark.parametrize("platform", ["x", "reddit"])
def test_inventory_payload_dispatches_to_secondary_refresh_and_honest_worker_outcome(monkeypatch, platform):
    from app.domains.kol import search_inventory_refresh as refresh, url_deep_crawl_queue as queue
    from app.platform.industry_crawlers.reddit_people_normalize import profile_payload
    from app.workers import apify_jobs_worker_handlers as handlers
    if platform == "reddit":
        reddit_client(monkeypatch)
        records = RedditCrawler().search_people("portrait")["records"]
    else:
        records, _ = x_records([tweet()])
    profile = records[0]["profile"]
    locator = profile["profile_url"]
    candidate = {"kol_pool_id": 7, "platform": platform, "handle": profile["handle"], "profile_url": locator}
    captured, worker_receipts, projected_rows = [], [], []
    conn = SimpleNamespace(execute=lambda *args, **kwargs: SimpleNamespace(fetchone=lambda: {"used": 0}), commit=lambda: None, rollback=lambda: None)
    monkeypatch.setattr(refresh, "get_conn", lambda: conn)
    monkeypatch.setattr(refresh, "table_exists", lambda name: name in {"apify_jobs", refresh.DAILY_SLOT_TABLE})
    monkeypatch.setattr(refresh, "select_refresh_candidates", lambda *args, **kwargs: [candidate])
    monkeypatch.setattr(refresh, "_reserve_daily_job_slots", lambda *args, **kwargs: {"reservation_token": "fixture", "reserved_slots": [1], "used_before": 0, "used_after_reservation": 1, "hard_limit": 5})
    monkeypatch.setattr(refresh, "_bind_daily_job_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr(refresh, "_release_daily_job_slots", lambda *args, **kwargs: 0)
    def enqueue(url, **kwargs):
        captured.append({"url": url, **kwargs, "maintenance_target_fence": {
            "platform": platform, "stable_handle": profile["handle"].lower(),
            "stable_native_ids": {"account_id": profile["platform_user_id"]},
        }})
        return {"status": "queued", "job_id": 91}
    monkeypatch.setattr(deep, "enqueue_profile_deep_crawl_job", enqueue)
    receipt = refresh.enqueue_daily_refresh(50, as_of=datetime(2026, 9, 4, 12, tzinfo=timezone.utc))
    assert receipt["queued"] == 1 and receipt["daily_limit"] == 5
    assert receipt["provider_calls_performed"] is False
    payload, = captured
    assert payload["max_posts"] == 1 and payload["maintenance_refresh"] is True
    assert payload["suppress_final_v1"] and payload["suppress_profile_followups"]

    monkeypatch.setattr(queue, "_maintenance_refresh_execution_block_reason", lambda *_: "")
    monkeypatch.setattr(queue, "_revalidate_maintenance_target_fence", lambda *args, **kwargs: candidate)
    monkeypatch.setattr(queue, "_revalidate_target_write_fence", lambda *_: None)
    crawler_type = XCrawler if platform == "x" else RedditCrawler
    monkeypatch.setattr(crawler_type, "crawl_person_profile", lambda self, target, max_posts: profile_payload({"status": "done", "records": records, "metadata": {"provider_mode": "fixture"}}))
    def execute(body):
        classified = deep.classify_url(body["url"])
        crawl = profile_data._crawl_profile_basics(classified, target=body["url"], max_posts=body["max_posts"])
        execution._verify_maintenance_crawl_identity(body, crawl)
        projected_rows.append(profile_data._profile_data_from_crawl(classified, crawl, existing_match={}, max_posts=1))
        return {"status": "ready", "kol_pool_id": 7, "provider_calls_performed": True}
    monkeypatch.setattr(deep, "dry_run_url_deep_crawl", execute)
    monkeypatch.setattr(handlers, "_resolve_job_staff", lambda *args: {})
    monkeypatch.setattr(handlers, "db_connection_sync_scope", nullcontext)
    monkeypatch.setattr(handlers.deep_crawl_worker, "persist_crawl_outcome", lambda *args, **kwargs: worker_receipts.append(kwargs))
    monkeypatch.setattr(handlers.deep_crawl_worker, "record_monitor_terminal", lambda *args, **kwargs: None)
    monkeypatch.setattr(handlers.deep_crawl_worker, "run_success_followups", lambda _, p, *args, **kwargs: p["suppress_profile_followups"])
    handlers._process_kol_profile_deep_crawl(conn, {"id": 91, "job_type": refresh.JOB_TYPE}, payload)
    assert worker_receipts[0]["ok"] is True and worker_receipts[0]["status"] == "ready"
    assert '"secondary_post_evidence_v1"' in projected_rows[0]["raw_platform_data"]
    assert "last_video_at" not in projected_rows[0]
    # A partial provider result follows the exact same queue lane, not success.
    monkeypatch.setattr(deep, "dry_run_url_deep_crawl", lambda _: {"status": "crawl_failed", "crawl_status": "partial", "provider_calls_performed": True})
    handlers._process_kol_profile_deep_crawl(conn, {"id": 92, "job_type": refresh.JOB_TYPE}, payload)
    assert worker_receipts[-1]["ok"] is False
