from __future__ import annotations

from app.services.vkpi import reddit_stability_strategy
from app.services.vkpi.industry_crawlers.reddit_crawler import RedditCrawler


def test_reddit_crawler_public_json_gate_can_disable_best_effort(monkeypatch) -> None:
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.setenv("VKPI_REDDIT_PUBLIC_JSON_ENABLED", "0")

    crawler = RedditCrawler()

    assert crawler.configured is False
    assert crawler.primary_path == "none"
    assert crawler.provider_status()["public_json_enabled"] is False


def test_reddit_stability_report_is_strategy_only(monkeypatch) -> None:
    monkeypatch.setattr(reddit_stability_strategy, "_table_exists", lambda table: True)
    monkeypatch.setattr(reddit_stability_strategy, "_count", lambda table: 0)

    report = reddit_stability_strategy.build_reddit_stability_report()

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["llm_calls"] is False
    assert report["write_db"] is False
    assert report["sync_triggered"] is False
    assert report["external_http_calls"] is False
    assert report["policy"]["no_full_reddit_claim"] is True
    assert report["policy"]["comments_require_selected_post"] is True
    assert report["checks"]["no_full_reddit_promise"] is True


def test_reddit_stability_report_fails_without_market_tables(monkeypatch) -> None:
    monkeypatch.setattr(reddit_stability_strategy, "_table_exists", lambda table: False)
    monkeypatch.setattr(reddit_stability_strategy, "_count", lambda table: 0)

    report = reddit_stability_strategy.build_reddit_stability_report()

    assert report["passed"] is False
    assert report["checks"]["market_storage_ready"] is False
    assert report["checks"]["review_storage_ready"] is False
