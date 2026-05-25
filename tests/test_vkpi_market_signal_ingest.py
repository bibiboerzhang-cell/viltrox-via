from __future__ import annotations

from app.domains.market.signal_write_package import build_external_market_signal_write_package, build_market_signal_write_package
from app.domains.market.signal_taxonomy import keyword_groups, keyword_hits, summarize_keyword_groups


def test_market_signal_taxonomy_classifies_full_chain_keywords() -> None:
    hits = keyword_hits("Viltrox and Sigma lens posts mention DJI, Nanlite, Blackmagic, and Teradek.")
    groups = keyword_groups(hits)

    assert "viltrox" in hits
    assert "sigma" in hits
    assert "dji" in hits
    assert "nanlite" in hits
    assert "blackmagic" in hits
    assert "teradek" in hits
    assert groups["viltrox_products"] == ["viltrox"]
    assert "sigma" in groups["tier1_lens_competitors"]
    assert "dji" in groups["tier1_cross_industry"]
    assert "nanlite" in groups["tier1_lighting_competitors"]
    assert "blackmagic" in groups["tier2_cinema_high_end"]
    assert "teradek" in groups["tier2_wireless_video"]


def test_summarize_keyword_groups_flags_tier2_only_when_tier1_is_sparse() -> None:
    posts = [
        {"keyword_hits": ["sigma"], "keyword_groups": {"tier1_lens_competitors": ["sigma"]}},
        {"keyword_hits": ["teradek"], "keyword_groups": {"tier2_wireless_video": ["teradek"]}},
    ]

    summary = summarize_keyword_groups(posts)

    assert summary["tier1_mentions"] == 1
    assert summary["tier2_mentions"] == 1
    assert summary["tier2_recommended_next_run"] is True


def test_market_signal_write_package_maps_reddit_smoke_without_db_write() -> None:
    report = {
        "mode": "reddit-signal-smoke-v0",
        "generated_at": "2026-05-24T16:33:48Z",
        "write_db": False,
        "llm_calls": False,
        "provider_status": {"provider_status": "configured", "primary_path": "json"},
        "summary": {"total_posts": 2, "keyword_hit_posts": 2, "tier1_mentions": 2},
        "errors": [],
        "subreddit_results": [
            {
                "subreddit": "fujifilm",
                "sample_posts": [
                    {
                        "source_uid": "reddit:abc",
                        "subreddit": "fujifilm",
                        "post_id": "abc",
                        "title": "XM5 with Viltrox 28mm pancake lens",
                        "author": "unit_author",
                        "score": 3,
                        "num_comments": 7,
                        "source_url": "https://www.reddit.com/r/fujifilm/comments/abc/unit/",
                        "keyword_hits": ["viltrox", "lens"],
                        "keyword_groups": {"viltrox_products": ["viltrox"], "generic_imaging_terms": ["lens"]},
                        "raw_payload_hash": "abc123",
                    }
                ],
            }
        ],
        "top_signal_candidates": [
            {
                "source_uid": "reddit:def",
                "subreddit": "SonyAlpha",
                "post_id": "def",
                "title": "Sigma lens question",
                "author": "sony_user",
                "score": 5,
                "num_comments": 12,
                "source_url": "https://www.reddit.com/r/SonyAlpha/comments/def/unit/",
                "keyword_hits": ["sigma", "lens"],
                "keyword_groups": {"tier1_lens_competitors": ["sigma"], "generic_imaging_terms": ["lens"]},
                "raw_payload_hash": "def456",
            },
            {
                "source_uid": "reddit:ghi",
                "subreddit": "photography",
                "post_id": "ghi",
                "title": "A generic wide angle lens question",
                "author": "generic_user",
                "score": 9,
                "num_comments": 20,
                "source_url": "https://www.reddit.com/r/photography/comments/ghi/unit/",
                "keyword_hits": ["wide angle", "lens"],
                "keyword_groups": {"generic_imaging_terms": ["wide angle", "lens"]},
                "raw_payload_hash": "ghi789",
            }
        ],
    }

    package = build_market_signal_write_package(report)

    assert package["write_db"] is False
    assert package["passed"] is True
    assert package["target_tables"] == ["vkpi_market_scan_runs", "vkpi_market_sources", "vkpi_market_mentions"]
    assert package["summary"]["sources_to_insert"] == 2
    assert package["summary"]["mentions_to_insert"] == 2
    assert package["mentions"][0]["product_sku"] == "viltrox"
    assert package["mentions"][1]["competitor_product"] == "sigma"
    assert package["checks"]["mentions_reference_sources"] is True
    assert package["policy"]["backup_required_before_write"] is True


def test_external_market_signal_write_package_maps_review_package_without_db_write() -> None:
    review_package = {
        "mode": "market_external_signal_review_package_v0",
        "generated_at": "2026-05-24T18:44:52Z",
        "write_db": False,
        "llm_calls": False,
        "summary": {
            "source_report_count": 2,
            "items_loaded": 4,
            "candidate_competitor_signal_after_market_mention": 1,
        },
        "ready_candidates": [
            {
                "source_uid": "external:abc",
                "provider": "google_news",
                "source_key": "google_news_lens_competitors",
                "source_group": "lens_competitor_watch",
                "source_type": "google_news_rss",
                "source_url": "https://news.google.com/rss/articles/abc",
                "source_host": "news.google.com",
                "title": "Sigma 35mm f/1.4 Art vs Sony GM",
                "summary": "A comparison of Sigma and Sony lenses.",
                "published_at": "2026-05-24T00:00:00Z",
                "score": 0.285,
                "keyword_hits": ["sigma", "sony", "35mm"],
                "keyword_groups": {
                    "tier1_lens_competitors": ["sigma"],
                    "camera_ecosystem": ["sony"],
                    "generic_imaging_terms": ["35mm"],
                },
                "primary_groups": ["tier1_lens_competitors"],
                "suggested_action": "ready",
                "reasons": ["tier1_competitor_or_ecosystem_signal"],
                "write_target": "vkpi_market_mentions",
                "secondary_target": "vkpi_competitor_signals_after_market_mention",
            }
        ],
    }

    package = build_external_market_signal_write_package(review_package)

    assert package["write_db"] is False
    assert package["passed"] is True
    assert package["summary"]["sources_to_insert"] == 1
    assert package["summary"]["mentions_to_insert"] == 1
    assert package["scan_run"]["scan_type"] == "external_signal_review"
    assert package["scan_run"]["platforms_json"] == ["google_news"]
    assert package["sources"][0]["source_type"] == "google_news_rss"
    assert package["mentions"][0]["competitor_product"] == "sigma"
    assert package["mentions"][0]["product_sku"] == ""
    assert package["mentions"][0]["metadata_json"]["promotion_target"] == "vkpi_competitor_signals_after_market_mention"
    assert package["checks"]["no_auto_competitor_signal_write"] is True
