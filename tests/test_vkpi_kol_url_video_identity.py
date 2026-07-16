from app.domains.kol import url_deep_crawl
from app.domains.kol.search_sessions_attach import _url_result_item
from app.domains.kol.url_deep_crawl import (
    _filter_incremental_profile_videos,
    _max_posts,
    _profile_history_video_limit,
    _profile_should_materialize_history_videos,
    classify_url,
)
from app.domains.kol.video_evidence import _video_identity


def test_instagram_shortcode_urls_classify_as_video_with_or_without_username_prefix():
    samples = [
        ("https://www.instagram.com/reel/DYxGltBM_fY/", "DYxGltBM_fY"),
        ("https://www.instagram.com/shtefutsa/reel/DYxGltBM_fY/", "DYxGltBM_fY"),
        ("https://www.instagram.com/p/DYw3UWUCJ_6/", "DYw3UWUCJ_6"),
        ("https://www.instagram.com/jaysoundo/p/DYw3UWUCJ_6/", "DYw3UWUCJ_6"),
        ("https://www.instagram.com/tv/DYtvCode123/", "DYtvCode123"),
        ("https://www.instagram.com/somecreator/tv/DYtvCode123/", "DYtvCode123"),
    ]

    for url, expected_video_id in samples:
        classified = classify_url(url)

        assert classified.platform == "instagram"
        assert classified.url_type == "video"
        assert classified.video_id == expected_video_id


def test_instagram_evidence_identity_dedupes_direct_and_username_prefixed_shortcode_urls():
    equivalent_pairs = [
        (
            "https://www.instagram.com/reel/DYxGltBM_fY/",
            "https://www.instagram.com/shtefutsa/reel/DYxGltBM_fY/",
        ),
        (
            "https://www.instagram.com/p/DYw3UWUCJ_6/",
            "https://www.instagram.com/jaysoundo/p/DYw3UWUCJ_6/",
        ),
        (
            "https://www.instagram.com/tv/DYtvCode123/",
            "https://www.instagram.com/somecreator/tv/DYtvCode123/",
        ),
    ]

    for direct_url, prefixed_url in equivalent_pairs:
        assert _video_identity(direct_url) == _video_identity(prefixed_url)
        assert _video_identity(direct_url)[0] == "instagram"


def test_profile_history_mode_explicitly_expands_video_crawl_limit():
    assert _max_posts({}) == 3
    assert _max_posts({"max_posts": 80}) == 12
    assert _profile_should_materialize_history_videos({"mode": "profile_only"}) is False

    body = {"mode": "account_deep", "history_video_limit": 80}

    assert _profile_should_materialize_history_videos(body) is True
    assert _max_posts(body) == 80
    assert _profile_history_video_limit(body) == 80


def test_profile_history_incremental_filter_keeps_only_new_videos():
    videos = [
        {"content_url": "https://www.youtube.com/watch?v=new1", "posted_at": "2026-06-05"},
        {"content_url": "https://www.youtube.com/watch?v=old1", "posted_at": "2026-05-20"},
        {"content_url": "https://www.youtube.com/watch?v=new2", "publish_date": "2026-06-03"},
    ]

    selected, skipped = _filter_incremental_profile_videos(
        videos,
        {"last_video_at": "2026-06-01"},
        limit=10,
    )

    assert [item["content_url"] for item in selected] == [
        "https://www.youtube.com/watch?v=new1",
        "https://www.youtube.com/watch?v=new2",
    ]
    assert skipped == 1


def test_video_plan_reuses_stored_youtube_evidence_before_provider(monkeypatch):
    classified = url_deep_crawl.classify_url(
        "https://youtu.be/ObmYbT4OUXM?si=-awTJASHMutDqwRD"
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "find_video_evidence_by_url",
        lambda _url: {
            "id": 3955,
            "kol_pool_id": 14061,
            "platform": "youtube",
            "content_url": "https://www.youtube.com/watch?v=ObmYbT4OUXM",
            "title": "Stored video",
            "view_count": 147772,
            "channel_id": "UCtWLGD1JkD-LEymBfLf5yKw",
            "channel_name": "InfiCheesy",
            "scrape_source": "youtube_api",
            "scrape_status": "success",
        },
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "_fetch_video_metadata",
        lambda _url: (_ for _ in ()).throw(AssertionError("provider must not run")),
    )
    monkeypatch.setattr(
        url_deep_crawl,
        "_match_pool",
        lambda creator: [
            {
                "kol_pool_id": 14061,
                "platform": creator.platform,
                "handle": "UCtWLGD1JkD-LEymBfLf5yKw",
                "display_name": "InfiCheesy",
                "profile_url": creator.normalized_url,
                "match_source": "platform_channel_id",
                "match_priority": 1,
            }
        ],
    )

    flow, matches = url_deep_crawl._video_flow_plan(classified, [])

    assert matches[0]["kol_pool_id"] == 14061
    assert flow["status"] == "ready_to_execute"
    assert flow["creator_resolution_status"] == "resolved"
    assert flow["creator_identity"]["channel_id"] == "UCtWLGD1JkD-LEymBfLf5yKw"
    assert flow["creator_identity"]["display_name"] == "InfiCheesy"
    assert flow["video_metadata"]["view_count"] == 147772
    assert flow["evidence_id"] == 3955
    assert flow["evidence_lookup_source"] == "stored_video_evidence"
    assert flow["provider_calls_performed"] is False

    item = _url_result_item(
        1,
        {
            "url": {"normalized": classified.normalized_url},
            "url_type": "video",
            "platform": "youtube",
            "video_id": classified.video_id,
            "in_pool": True,
            "matched_kol_pool_id": 14061,
            "creator_identity": flow["creator_identity"],
            "video_metadata": flow["video_metadata"],
            "video_flow": flow,
        },
    )
    assert item["status"] == "identified"
    assert item["kol_pool_id"] == 14061
    assert item["evidence_id"] == 3955
