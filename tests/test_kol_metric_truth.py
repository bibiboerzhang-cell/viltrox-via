from __future__ import annotations

import json

from app.domains.kol import metric_truth
from app.domains.kol.metric_truth import (
    project_evidence_item_truth,
    project_pool_item_truth,
)
from app.domains.kol.pool_common import mask_pool_item


def _observed_zero_raw() -> dict:
    return {
        "source": "youtube_api",
        "providerStatus": "success",
        "profile": {
            "items": [
                {
                    "kind": "youtube#channel",
                    "statistics": {"subscriberCount": "0"},
                }
            ]
        },
        "engagementRate": 0,
        "videos": [
            {
                "kind": "youtube#video",
                "id": "observed-zero",
                "title": "Observed zero sample",
                "statistics": {
                    "viewCount": "0",
                    "likeCount": "0",
                    "commentCount": "0",
                },
            }
        ],
    }


def test_unknown_and_placeholder_values_never_become_zero() -> None:
    projected = project_pool_item_truth(
        {
            "followers": "N/A",
            "avg_views": None,
            "avg_likes": "unknown",
            "avg_comments": "-",
            "engagement_rate": "null",
            "source_type": "manual",
        }
    )

    for field in ("followers", "avg_views", "avg_likes", "avg_comments", "engagement_rate"):
        assert projected[field] is None
        assert projected["data_truth"]["fields"][field]["displayable"] is False


def test_stored_zero_requires_matching_successful_raw_observation() -> None:
    without_receipt = project_pool_item_truth(
        {
            "followers": 0,
            "avg_views": 0,
            "avg_likes": 0,
            "avg_comments": 0,
            "engagement_rate": 0,
            "source_type": "manual",
            "raw_platform_data": {},
        }
    )
    with_receipt = project_pool_item_truth(
        {
            "followers": 0,
            "avg_views": 0,
            "avg_likes": 0,
            "avg_comments": 0,
            "engagement_rate": 0,
            "source_type": "provider",
            "raw_platform_data": _observed_zero_raw(),
        }
    )

    for field in ("followers", "avg_views", "avg_likes", "avg_comments", "engagement_rate"):
        assert without_receipt[field] is None
        assert without_receipt["data_truth"]["fields"][field]["status"] == "zero_sentinel_suppressed"
        assert with_receipt[field] == 0
        assert with_receipt["data_truth"]["fields"][field]["status"] == "observed_zero"
        assert with_receipt["data_truth"]["fields"][field]["factual"] is True


def test_pool_truth_indexes_raw_source_and_each_metric_once(
    monkeypatch,
) -> None:
    calls = {"source": 0, "evidence": 0}
    source = metric_truth._raw_source_state
    evidence = metric_truth._raw_metric_evidence

    def counted_source(raw):
        calls["source"] += 1
        return source(raw)

    def counted_evidence(raw, field):
        calls["evidence"] += 1
        return evidence(raw, field)

    monkeypatch.setattr(metric_truth, "_raw_source_state", counted_source)
    monkeypatch.setattr(metric_truth, "_raw_metric_evidence", counted_evidence)

    project_pool_item_truth({
        "followers": 0,
        "avg_views": 0,
        "avg_likes": 0,
        "avg_comments": 0,
        "engagement_rate": 0,
        "source_type": "provider",
        "raw_platform_data": _observed_zero_raw(),
    })

    assert calls == {"source": 1, "evidence": len(metric_truth.POOL_NUMERIC_FIELDS)}


def test_no_results_payload_does_not_prove_zero() -> None:
    projected = project_pool_item_truth(
        {
            "avg_views": 0,
            "source_type": "provider",
            "raw_platform_data": {
                "source": "youtube_api",
                "provider_status": "success",
                "videos": [
                    {
                        "kind": "youtube#video",
                        "error": "NO_RESULTS",
                        "statistics": {"viewCount": 0},
                    }
                ],
            },
        }
    )

    assert projected["avg_views"] is None
    assert projected["data_truth"]["fields"]["avg_views"]["status"] == "zero_sentinel_suppressed"


def test_raw_value_must_match_stored_value_before_it_is_factual() -> None:
    projected = project_pool_item_truth(
        {
            "avg_views": 999,
            "source_type": "provider",
            "source_ref": "provider-job-42",
            "raw_platform_data": {
                "source": "youtube_api",
                "provider_status": "success",
                "videos": [
                    {"kind": "youtube#video", "id": "one", "statistics": {"viewCount": 100}},
                    {"kind": "youtube#video", "id": "two", "statistics": {"viewCount": 200}},
                ],
            },
        }
    )

    assert projected["avg_views"] == 999
    receipt = projected["data_truth"]["fields"]["avg_views"]
    assert receipt["status"] == "declared"
    assert receipt["factual"] is False


def test_unreferenced_manual_nonzero_metric_is_suppressed() -> None:
    projected = project_pool_item_truth(
        {"followers": 1234, "source_type": "manual", "source_ref": "", "raw_platform_data": {}}
    )

    assert projected["followers"] is None
    assert projected["data_truth"]["fields"]["followers"]["status"] == "unverified_suppressed"


def test_legacy_engagement_and_sample_backed_real_er_have_separate_identities() -> None:
    projected = project_pool_item_truth(
        {
            "engagement_rate": 3.2,
            "source_type": "legacy_excel_p2d",
            "source_ref": "kol-import.xlsx#22",
            "real_er": 0,
            "real_er_sample_n": 8,
            "real_er_computed_at": "2026-08-03T12:00:00Z",
            "real_er_method": "evidence10_pooled_v1",
        }
    )

    legacy = projected["data_truth"]["fields"]["engagement_rate"]
    real = projected["data_truth"]["fields"]["real_er"]
    assert projected["engagement_rate"] == 3.2
    assert legacy["metric_identity"] == "legacy_engagement_rate"
    assert legacy["verified_real_er"] is False
    assert projected["real_er"] == 0
    assert real["metric_identity"] == "real_er"
    assert real["denominator"] == "views"
    assert real["status"] == "observed_zero"
    assert real["sample_n"] == 8


def test_real_er_without_full_receipt_is_suppressed() -> None:
    projected = project_pool_item_truth(
        {
            "real_er": 4.2,
            "real_er_sample_n": 10,
            "real_er_computed_at": "",
            "real_er_method": "evidence10_pooled_v1",
        }
    )

    assert projected["real_er"] is None
    assert projected["data_truth"]["fields"]["real_er"]["status"] == "receipt_incomplete_suppressed"


def test_fractional_sample_counts_and_placeholder_timestamps_are_not_receipts() -> None:
    projected = project_pool_item_truth(
        {
            "real_er": 4.2,
            "real_er_sample_n": 1.5,
            "real_er_computed_at": "N/A",
            "real_er_method": "evidence10_pooled_v1",
            "audience_estimated_json": {"method": "ensemble_v1", "sample_size": 2.5},
        }
    )

    assert projected["real_er"] is None
    assert projected["audience_estimated_json"] is None


def test_audience_estimate_needs_method_and_positive_sample() -> None:
    invalid = project_pool_item_truth(
        {"audience_estimated_json": {"method": "ensemble_v1", "sample_size": 0, "country": "US"}}
    )
    valid = project_pool_item_truth(
        {
            "audience_estimated_json": {
                "method": "ensemble_v1",
                "sample_size": 25,
                "confidence": 0.72,
                "country": "US",
            }
        }
    )

    assert invalid["audience_estimated_json"] is None
    assert valid["audience_estimated_json"]["country"] == "US"
    assert valid["data_truth"]["fields"]["audience_estimated"]["status"] == "estimated"
    assert valid["data_truth"]["fields"]["audience_estimated"]["factual"] is False


def test_only_confirmed_or_evidence_linked_collaborations_remain() -> None:
    projected = project_pool_item_truth(
        {
            "brand_collaborations_json": json.dumps(
                [
                    {"brand": "Planned Co", "status": "planned"},
                    {"brand": "Published Co", "status": "published"},
                    {"brand": "Evidence Co", "evidence_url": "https://example.test/post"},
                    {"brand": "Unsupported Co"},
                ]
            )
        }
    )

    collaborations = json.loads(projected["brand_collaborations_json"])
    assert [item["brand"] for item in collaborations] == ["Published Co", "Evidence Co"]
    receipt = projected["data_truth"]["fields"]["brand_collaborations"]
    assert receipt["observed_count"] == 2
    assert receipt["suppressed_unverified_or_planned_count"] == 2


def test_evidence_zero_requires_metric_source_timestamp_and_nonfailure_status() -> None:
    no_receipt = project_evidence_item_truth(
        {"id": 7, "content_url": "https://example.test/video", "view_count": 0, "source": "manual"}
    )
    observed = project_evidence_item_truth(
        {
            "id": 8,
            "content_url": "https://example.test/video-2",
            "view_count": 0,
            "metrics_source": "manual_url",
            "metrics_scraped_at": "2026-08-03T12:00:00Z",
            "scrape_status": "success",
        }
    )
    failed = project_evidence_item_truth(
        {
            "id": 9,
            "content_url": "https://example.test/video-3",
            "view_count": 0,
            "metrics_source": "manual_url",
            "metrics_scraped_at": "2026-08-03T12:00:00Z",
            "scrape_status": "failed",
        }
    )

    assert no_receipt["view_count"] is None
    assert observed["view_count"] == 0
    assert observed["data_truth"]["fields"]["view_count"]["factual"] is True
    assert failed["view_count"] is None


def test_mask_pool_item_applies_truth_projection_and_removes_internal_raw_alias() -> None:
    projected = mask_pool_item(
        {
            "email": "creator@example.test",
            "followers": 0,
            "source_type": "provider",
            "metric_truth_raw_platform_data": _observed_zero_raw(),
        }
    )

    assert projected["followers"] == 0
    assert "metric_truth_raw_platform_data" not in projected
    assert projected["email"] != "creator@example.test"
    assert projected["contact_masked"] is True


def test_truth_summary_exposes_safe_provenance_and_metric_time() -> None:
    projected = project_pool_item_truth(
        {
            "followers": 0,
            "source_type": "legacy_excel_p2d",
            "source_ref": "/Users/private/import.xlsx?token=should-not-leak",
            "last_seen_at": "2026-08-03T13:00:00Z",
            "raw_platform_data": {
                "source": "youtube_api",
                "provider_status": "success",
                "fetched_at": "2026-08-03T12:59:00Z",
                "profile": {
                    "kind": "youtube#channel",
                    "statistics": {"subscriberCount": 0},
                },
            },
        }
    )

    truth = projected["data_truth"]
    assert truth["source_type"] == "legacy_excel_p2d"
    assert truth["source_ref"] == "import.xlsx"
    assert truth["metric_observed_at"] == "2026-08-03T12:59:00Z"
    assert truth["metric_recorded_at"] == "2026-08-03T13:00:00Z"
    assert "/Users/private" not in json.dumps(truth)
    assert "should-not-leak" not in json.dumps(truth)

    url_projected = project_pool_item_truth(
        {
            "followers": 10,
            "source_type": "provider",
            "source_ref": "https://user:password@example.test/source?token=must-not-leak",
        }
    )
    url_ref = url_projected["data_truth"]["source_ref"]
    assert url_ref == "https://example.test/source"
    assert "password" not in url_ref
    assert "token" not in url_ref
