from __future__ import annotations

import json

from app.domains.market.signal_review_package import (
    build_external_signal_review_package,
    build_external_signal_review_package_from_files,
    build_market_signal_review_package,
    build_market_signal_review_package_from_file,
    competitor_signal_rows_from_review_package,
)
from app.domains.market.signal_commit import (
    build_competitor_signal_run_summary,
    build_competitor_signal_write_result,
    validate_competitor_signal_write_request,
)


def _candidate(**overrides):
    base = {
        "signal_uid": "sig-1",
        "brand": "sigma",
        "normalized_brand": "sigma",
        "signal_type": "product_comparison",
        "severity": "medium",
        "score": 42.0,
        "product_hints": ["sigma", "lens"],
        "source_table": "vkpi_market_mentions",
        "source_id": 10,
        "source_url": "https://example.test/sigma",
        "platform": "reddit",
        "detail": "Sigma versus Tamron lens comparison",
        "evidence": {"confidence": 0.72},
        "review_status": "pending_review",
    }
    base.update(overrides)
    return base


def _payload(candidates):
    return {
        "mode": "market_signal_classifier_v0",
        "generated_at": "2026-05-24T00:00:00Z",
        "write_db": False,
        "passed": True,
        "summary": {"promotion_candidates": len(candidates)},
        "promotion_candidates": candidates,
    }


def _external_smoke_payload(items):
    return {
        "mode": "market_external_signal_smoke_v0",
        "generated_at": "2026-05-24T00:00:00Z",
        "provider_calls": True,
        "external_http_calls": True,
        "write_db": False,
        "llm_calls": False,
        "gemini_calls": False,
        "sync_triggered": False,
        "passed": True,
        "summary": {"selected_source_group": "rss_industry_watch", "items_loaded": len(items)},
        "items": items,
    }


def _external_item(**overrides):
    base = {
        "source_uid": "external-1",
        "provider": "rss",
        "source_key": "rss_test",
        "source_type": "rss_feed",
        "source_url": "https://petapixel.com/example",
        "title": "7Artisans 135mm lens review",
        "summary": "7Artisans lens review for Sony and Nikon users.",
        "published_at": "2026-05-24T00:00:00Z",
        "score": 0.42,
        "business_signal": True,
        "keyword_hits": ["7artisans", "lens", "sony"],
        "keyword_groups": {
            "tier1_lens_competitors": ["7artisans"],
            "camera_ecosystem": ["sony"],
            "generic_imaging_terms": ["lens"],
        },
    }
    base.update(overrides)
    return base


def test_review_package_marks_high_context_candidate_ready() -> None:
    package = build_market_signal_review_package(_payload([_candidate()]))

    assert package["passed"] is True
    assert package["summary"]["ready_for_promotion"] == 1
    assert package["ready_candidates"][0]["suggested_action"] == "ready"
    assert "signal_type:product_comparison" in package["ready_candidates"][0]["reasons"]
    assert package["write_db"] is False


def test_review_package_keeps_missing_source_pending() -> None:
    package = build_market_signal_review_package(
        _payload([_candidate(signal_uid="sig-2", source_url="", source_id=None)])
    )

    assert package["summary"]["pending_manual_review"] == 1
    assert package["pending_candidates"][0]["suggested_action"] == "pending_review"
    assert "missing_source" in package["pending_candidates"][0]["reasons"]


def test_review_package_ignores_low_context_generic_mention() -> None:
    package = build_market_signal_review_package(
        _payload(
            [
                _candidate(
                    signal_uid="sig-3",
                    signal_type="competitor_mention",
                    score=18.0,
                    product_hints=["sigma"],
                    evidence={"confidence": 0.62},
                    detail="Sigma",
                )
            ]
        )
    )

    assert package["summary"]["ignored"] == 1
    assert package["ignored_candidates"][0]["suggested_action"] == "ignored"
    assert "generic_competitor_mention" in package["ignored_candidates"][0]["reasons"]


def test_review_package_rejects_viltrox_brand_as_competitor() -> None:
    package = build_market_signal_review_package(
        _payload(
            [
                _candidate(
                    signal_uid="sig-4",
                    brand="viltrox",
                    normalized_brand="viltrox",
                    product_hints=["viltrox"],
                )
            ]
        )
    )

    assert package["summary"]["rejected"] == 1
    assert package["rejected_candidates"][0]["suggested_action"] == "rejected"
    assert "viltrox_brand_not_competitor" in package["rejected_candidates"][0]["reasons"]
    assert package["checks"]["ready_excludes_viltrox"] is True


def test_review_package_deduplicates_candidates_and_keeps_boundaries() -> None:
    candidate = _candidate(signal_uid="sig-5")
    package = build_market_signal_review_package(_payload([candidate, candidate.copy()]))

    assert package["summary"]["candidates_loaded"] == 1
    assert package["checks"]["write_db_blocked"] is True
    assert package["checks"]["llm_calls_blocked"] is True
    assert package["checks"]["gemini_calls_blocked"] is True
    assert package["policy"]["backup_required_before_write"] is True


def test_ready_candidates_map_to_competitor_signal_rows() -> None:
    package = build_market_signal_review_package(_payload([_candidate(signal_uid="sig-6")]))
    rows = competitor_signal_rows_from_review_package(package)

    assert len(rows) == 1
    assert rows[0]["signal_uid"] == "sig-6"
    assert rows[0]["source_table"] == "vkpi_market_mentions"
    assert rows[0]["review_status"] == "pending_review"
    assert "market_signal_promotion_review_package_v0" in rows[0]["evidence_json"]


def test_competitor_signal_commit_helpers_validate_and_summarize_without_db() -> None:
    package = build_market_signal_review_package(_payload([_candidate(signal_uid="sig-commit")]))
    rows = validate_competitor_signal_write_request(package, backup_ref="backup-20260524T000000Z")

    assert rows[0]["signal_uid"] == "sig-commit"

    summary = build_competitor_signal_run_summary(
        package,
        backup_ref="backup-20260524T000000Z",
        ready_count=1,
        insert_count=1,
        skipped_existing=0,
    )
    assert summary["backup_ref"] == "backup-20260524T000000Z"
    assert summary["provider_calls"] is False

    result = build_competitor_signal_write_result(
        generated_at="2026-05-24T00:00:00Z",
        backup_ref="backup-20260524T000000Z",
        run_uid="mktprom-unit",
        run_id=10,
        inserted=1,
        skipped_existing=0,
        ready_candidates=1,
        before_counts={"vkpi_competitor_signal_runs": 2, "vkpi_competitor_signals": 4},
        after_counts={"vkpi_competitor_signal_runs": 3, "vkpi_competitor_signals": 5},
    )
    assert result["write_db"] is True
    assert result["checks"]["competitor_signals_delta_matches"] is True


def test_from_file_accepts_existing_review_package(tmp_path) -> None:
    package = build_market_signal_review_package(_payload([_candidate(signal_uid="sig-7")]))
    path = tmp_path / "review-package.json"
    path.write_text(json.dumps(package), encoding="utf-8")

    loaded = build_market_signal_review_package_from_file(path)

    assert loaded["mode"] == "market_signal_promotion_review_package_v0"
    assert loaded["summary"]["ready_for_promotion"] == 1


def test_external_signal_review_package_marks_tier1_ready_without_writing() -> None:
    package = build_external_signal_review_package([_external_smoke_payload([_external_item()])])

    assert package["passed"] is True
    assert package["write_db"] is False
    assert package["provider_calls"] is False
    assert package["summary"]["ready_for_market_mentions"] == 1
    ready = package["ready_candidates"][0]
    assert ready["write_target"] == "vkpi_market_mentions"
    assert ready["secondary_target"] == "vkpi_competitor_signals_after_market_mention"
    assert "tier1_competitor_or_ecosystem_signal" in ready["reasons"]
    assert package["checks"]["ready_requires_market_mention_first"] is True


def test_external_signal_review_package_ignores_noise_items() -> None:
    package = build_external_signal_review_package(
        [
            _external_smoke_payload([
                _external_item(
                    source_uid="external-noise",
                    title="Remote underwater camera captures seal behavior",
                    score=0.12,
                    business_signal=False,
                    keyword_hits=[],
                    keyword_groups={},
                )
            ])
        ]
    )

    assert package["summary"]["ignored"] == 1
    assert package["ignored_candidates"][0]["write_target"] == ""
    assert "no_taxonomy_keyword_hit" in package["ignored_candidates"][0]["reasons"]


def test_external_signal_review_package_deduplicates_across_reports() -> None:
    item = _external_item(source_uid="external-dupe")
    package = build_external_signal_review_package([
        _external_smoke_payload([item]),
        _external_smoke_payload([item.copy()]),
    ])

    assert package["summary"]["items_loaded"] == 1
    assert package["summary"]["source_report_count"] == 2


def test_external_signal_review_package_from_files(tmp_path) -> None:
    path = tmp_path / "external-smoke.json"
    path.write_text(json.dumps(_external_smoke_payload([_external_item(source_uid="external-file")])), encoding="utf-8")

    package = build_external_signal_review_package_from_files([path])

    assert package["mode"] == "market_external_signal_review_package_v0"
    assert package["summary"]["ready_for_market_mentions"] == 1
