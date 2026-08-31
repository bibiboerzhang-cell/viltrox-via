from __future__ import annotations

from app.services.deepsight import repository
from app.services.ingestion import pipeline
from app.services.intelligence import viltrox_matrix


def test_normalize_ingest_payload_preserves_priority_defaults_and_raw_payload(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_now", lambda: "2026-08-31T12:00:00Z")
    raw = {
        "topic": " Created ",
        "resource_type": " Video ",
        "id": " post-7 ",
        "handle": " creator ",
        "country": " us ",
        "sku": " AF-01 ",
        "product_name": " Lens ",
        "metrics": "not-a-dict",
        "user_id": "9",
        "scene_tags": ["street"],
    }

    normalized = pipeline.normalize_ingest_payload(" YouTube ", raw)

    assert normalized == {
        "source_platform": "youtube",
        "event_type": "created",
        "entity_type": "video",
        "external_id": "post-7",
        "source_url": "post-7",
        "creator_handle": "creator",
        "user_id": 9,
        "submission_id": 0,
        "region_code": "US",
        "observed_at": "2026-08-31T12:00:00Z",
        "dedupe_key": "youtube:created:video:post-7",
        "product_key": "AF-01",
        "product_label": "Lens",
        "product_family": "",
        "mount_type": "",
        "scene_tags": ["street"],
        "feature_tags": [],
        "alias_terms": [],
        "metrics": {},
        "summary": "",
        "note": "",
        "payload": raw,
    }


def test_build_scan_payload_preserves_legacy_projection_and_fallbacks() -> None:
    bundle = {
        "run": {
            "id": 4,
            "run_key": "scan-4",
            "status": "",
            "started_at": "2026-08-31T10:00:00Z",
            "scanned_accounts": 0,
            "total_accounts": 0,
            "total_posts": 1,
            "total_views": 20,
            "total_likes": 3,
            "total_comments": 2,
        },
        "accounts": [{"id": 8, "name": "Official", "platform": "youtube", "handle": "@viltrox"}],
        "scan_accounts": [{"account_id": 8, "total_posts": 0, "duration_sec": "1.5"}],
        "posts": [{
            "account_id": 8,
            "title": "Post",
            "post_url": "https://example.test/p",
            "thumbnail_url": "https://example.test/t.jpg",
            "views": 20,
            "likes": 3,
            "comments": 2,
            "shares": 1,
            "published_at": "2026-08-30",
            "content_type": "review",
        }],
    }

    assert viltrox_matrix._build_scan_payload({}) is None
    assert viltrox_matrix._build_scan_payload(bundle) == {
        "run_id": 4,
        "run_key": "scan-4",
        "status": "completed",
        "timestamp": "2026-08-31T10:00:00Z",
        "results": [{
            "account": {"id": 8, "name": "Official", "platform": "youtube", "handle": "@viltrox"},
            "data": {
                "overview": {"total_posts": 1, "total_views": 0, "total_likes": 0, "total_comments": 0},
                "posts": [{
                    "title": "Post",
                    "url": "https://example.test/p",
                    "thumbnail": "https://example.test/t.jpg",
                    "views": 20,
                    "likes": 3,
                    "comments": 2,
                    "shares": 1,
                    "published": "2026-08-30",
                    "type": "review",
                }],
                "error": None,
            },
            "duration_sec": 1.5,
        }],
        "scanned": 1,
        "total": 1,
        "aggregate": {"total_posts": 1, "total_views": 20, "total_likes": 3, "total_comments": 2},
    }


def test_fetch_submissions_window_filters_then_preserves_normalized_contract(monkeypatch) -> None:
    rows = [
        {
            "id": 9,
            "created_at": "2099-01-01",
            "platform": "YouTube",
            "extracted_handle": " @creator ",
            "title": "Review",
            "url": "https://example.test/video",
            "product_series": "Air",
            "product_label": "",
            "content_types": "fallback/type",
            "content_genre": "fallback-topic",
            "recommendation": "fallback-summary",
            "views": 100,
            "likes": 10,
            "comments": 2,
            "shares": 1,
            "favorites": 3,
            "risk_score": 4,
            "video_analysis": {
                "viltrox_lens": "AF 50",
                "content_types": ["review", "test"],
                "content_topic": "lens test",
                "content_summary": "analysis summary",
                "quality_scores": {"light": 8},
                "quality_overall": 7,
                "visible_comments": ["great"],
                "competitor_brands": "Sigma|Sony",
                "brand_elements": "logo/signage",
                "reference_reasons": ["sharpness"],
            },
        },
        {"id": 8, "created_at": "2099-01-01", "platform": "TikTok"},
        {"id": 7, "created_at": "2000-01-01", "platform": "YouTube"},
    ]

    class Cursor:
        def fetchall(self):
            return rows

    class Conn:
        def execute(self, sql):
            assert sql == "SELECT * FROM submissions ORDER BY id DESC"
            return Cursor()

    monkeypatch.setattr(repository, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        repository,
        "analyze_comments",
        lambda comments: {"sample_comments": comments, "sample_size": len(comments)},
    )
    monkeypatch.setattr(
        repository,
        "compute_visual_life_score",
        lambda payload: {"visual_life_score": payload["campaign_score"] + payload["sample_size"]},
    )

    result = repository.fetch_submissions_window(30, platforms=["youtube"])

    assert len(result) == 1
    item = result[0]
    assert item["id"] == 9
    assert item["handle"] == "@creator"
    assert item["channel"] == "creator"
    assert item["product_label"] == "AF 50"
    assert item["content_types"] == ["review", "test"]
    assert item["competitor_brands"] == ["Sigma", "Sony"]
    assert item["brand_elements"] == ["logo", "signage"]
    assert item["engagement_rate"] == 0.16
    assert item["comment_analysis"] == {"sample_comments": ["great"], "sample_size": 1}
    assert item["visual_life_score"] == item["campaign_score"] + 1
