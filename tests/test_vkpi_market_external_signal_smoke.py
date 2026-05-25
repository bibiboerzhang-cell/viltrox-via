from __future__ import annotations

from app.domains.market.external_signal_smoke import (
    build_external_daily_candidate_plan,
    build_external_source_matrix,
    build_external_signal_smoke,
    google_news_rss_url,
)


def _fixture_items():
    return [
        {
            "provider": "google_news",
            "source_key": "fixture_google",
            "source_type": "google_news_rss",
            "source_url": "https://news.google.com/rss/articles/example",
            "title": "DJI and Sigma creator camera gear trend",
            "summary": "Creators compare DJI, Sigma and Tamron lens options for compact video kits.",
            "published_at": "2026-05-24T00:00:00Z",
        },
        {
            "provider": "rss",
            "source_key": "fixture_rss",
            "source_type": "rss_feed",
            "source_url": "https://petapixel.com/example",
            "title": "Viltrox AF 35mm lens sample images",
            "summary": "A short item about Viltrox AF 35mm and autofocus performance.",
            "published_at": "2026-05-24T00:00:00Z",
        },
    ]


def test_external_signal_smoke_normalizes_raw_items_without_http() -> None:
    report = build_external_signal_smoke(raw_items=_fixture_items())

    assert report["passed"] is True
    assert report["provider_calls"] is False
    assert report["external_http_calls"] is False
    assert report["write_db"] is False
    assert report["summary"]["items_loaded"] == 2
    assert report["summary"]["business_signal_items"] == 2
    assert report["summary"]["tier1_mentions"] >= 2
    assert report["top_candidates"][0]["keyword_hits"]


def test_external_signal_smoke_default_does_not_fetch_http() -> None:
    report = build_external_signal_smoke()

    assert report["passed"] is True
    assert report["items"] == []
    assert report["provider_calls"] is False
    assert all(status["status"] == "skipped" for status in report["source_statuses"])


def test_external_source_matrix_groups_are_allowlisted() -> None:
    matrix = build_external_source_matrix()

    assert matrix["passed"] is True
    assert matrix["provider_calls"] is False
    assert matrix["summary"]["source_count"] >= 8
    assert matrix["summary"]["source_groups"]["rss_industry_watch"] >= 3
    assert all(source["allowlisted"] for source in matrix["sources"])


def test_external_signal_smoke_can_filter_source_group() -> None:
    report = build_external_signal_smoke(source_group="rss_industry_watch")

    assert report["passed"] is True
    assert report["summary"]["selected_source_group"] == "rss_industry_watch"
    assert report["summary"]["sources_requested"] >= 3
    assert {status["source_group"] for status in report["source_statuses"]} == {"rss_industry_watch"}


def test_external_daily_candidate_plan_is_read_only_and_bounded() -> None:
    plan = build_external_daily_candidate_plan(max_http_calls=4, limit_per_source=5)

    assert plan["passed"] is True
    assert plan["provider_calls"] is False
    assert plan["external_http_calls"] is False
    assert plan["llm_calls"] is False
    assert plan["write_db"] is False
    assert plan["sync_triggered"] is False
    assert plan["task_enqueued"] is False
    assert plan["summary"]["planned_http_calls"] <= 4
    assert plan["summary"]["planned_item_limit"] == plan["summary"]["planned_http_calls"] * 5
    assert plan["summary"]["estimated_cost_usd"] == 0.0
    assert plan["summary"]["blocked_auto_run"] is True
    assert all(group["requires_human_ack"] for group in plan["groups"])
    assert all(source["allowlisted"] for source in plan["planned_sources"])


def test_external_daily_candidate_plan_can_filter_source_group() -> None:
    plan = build_external_daily_candidate_plan(
        source_group="rss_industry_watch",
        max_http_calls=2,
        limit_per_source=3,
    )

    assert plan["passed"] is True
    assert plan["summary"]["selected_source_group"] == "rss_industry_watch"
    assert plan["summary"]["planned_http_calls"] == 2
    assert plan["summary"]["planned_item_limit"] == 6
    assert {source["source_group"] for source in plan["planned_sources"]} == {"rss_industry_watch"}


def test_external_daily_candidate_plan_reports_unmatched_group() -> None:
    plan = build_external_daily_candidate_plan(source_group="missing_group")

    assert plan["passed"] is False
    assert plan["checks"]["source_filter_matched"] is False
    assert plan["summary"]["planned_http_calls"] == 0


def test_external_signal_smoke_parses_allowlisted_rss_when_enabled(monkeypatch) -> None:
    xml = """<?xml version="1.0"?>
    <rss><channel><item>
      <title>Nanlite and Viltrox creator setup</title>
      <link>https://petapixel.com/nanlite-viltrox-example</link>
      <description>Nanlite lighting and Viltrox lens setup for video creators.</description>
      <pubDate>Sun, 24 May 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>
    """

    monkeypatch.setattr(
        "app.domains.market.external_signal_smoke._fetch_url_text",
        lambda *_args, **_kwargs: xml,
    )

    report = build_external_signal_smoke(
        sources=[{
            "source_key": "rss_test",
            "provider": "rss",
            "source_type": "rss_feed",
            "feed_url": "https://petapixel.com/feed/",
            "purpose": "test",
        }],
        execute_http_fetch=True,
        limit_per_source=3,
    )

    assert report["passed"] is True
    assert report["provider_calls"] is True
    assert report["summary"]["sources_fetched"] == 1
    assert report["summary"]["items_loaded"] == 1
    assert report["items"][0]["business_signal"] is True
    assert report["items"][0]["published_at"] == "2026-05-24T12:00:00Z"


def test_google_news_url_is_allowlisted_source_shape() -> None:
    url = google_news_rss_url("Viltrox lens")

    assert url.startswith("https://news.google.com/rss/search?")
    assert "Viltrox+lens" in url
