from __future__ import annotations

import json

import pytest

from app.domains.analysis import cache_repo
from app.domains.kol import analysis_readiness, audience_language, eleven_dimensions, llm_deep_analysis
from app.domains.kol import creator_gear
from app.domains.kol import pool as kol_pool


@pytest.fixture(autouse=True)
def _stub_readiness_denominator(monkeypatch):
    monkeypatch.setattr(
        analysis_readiness,
        "load_readiness_video_evidence",
        lambda *_args, **_kwargs: {
            "items": [],
            "limit": 200,
            "truncated": False,
            "sample_scope": "active_video_evidence_up_to_200",
        },
    )


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (24, 24),
        (200, 200),
        (999, 200),
    ],
)
def test_detail_bundle_honors_route_video_limit_contract(
    monkeypatch,
    requested: int,
    expected: int,
):
    requested_limits: list[int] = []

    def fake_video_evidence(_kol_pool_id: int, *, limit: int, **kwargs):
        if kwargs.get("only_with_cache"):
            return []
        requested_limits.append(limit)
        return [
            {
                "id": index + 1,
                "content_url": f"https://example.com/video/{index + 1}",
            }
            for index in range(limit)
        ]

    monkeypatch.setattr(kol_pool, "get_item", lambda _kol_pool_id, **_kwargs: {"item": {"id": _kol_pool_id}})
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", fake_video_evidence)
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=requested, llm_limit=20)

    assert requested_limits == [expected]
    assert len(result["item"]["video_evidence"]) == expected


def test_detail_bundle_skips_get_item_legacy_three_video_projection(monkeypatch):
    item_kwargs: dict[str, object] = {}

    def fake_get_item(_kol_pool_id: int, **kwargs):
        item_kwargs.update(kwargs)
        return {"item": {"id": _kol_pool_id}}

    monkeypatch.setattr(kol_pool, "get_item", fake_get_item)
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    assert item_kwargs["include_raw_for_derivation"] is True
    assert item_kwargs["include_video_evidence"] is False


def test_detail_bundle_batches_analysis_cache_reads(monkeypatch):
    calls: list[tuple[list[str], tuple[str, ...]]] = []
    monkeypatch.setattr(
        cache_repo,
        "canonical_final_v1_cache_reuse",
        lambda *_args, **_kwargs: {"reusable": True, "reasons": []},
    )

    def fake_video_evidence(_kol_pool_id: int, *, limit: int, **kwargs):
        if kwargs.get("only_with_cache"):
            return [{"id": index} for index in range(1, 51)]
        return []

    def fake_batch(_target_type, target_ids, *, derive_methods, conn=None):
        del conn
        calls.append((list(target_ids), tuple(derive_methods)))
        return {
            **{(str(evidence_id), "video_analysis_final_v1"): {
                "status": "ready",
                "result": {},
            } for evidence_id in target_ids},
            **{(str(evidence_id), "video_analysis_final_v1_keyframe_qa"): {
                "status": "ready",
                "result": {"qa_pass": True},
            } for evidence_id in target_ids},
        }

    monkeypatch.setattr(kol_pool, "get_item", lambda _kol_pool_id, **_kwargs: {"item": {"id": _kol_pool_id}})
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", fake_video_evidence)
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", fake_batch)
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    assert len(calls) == 1
    assert calls[0][0] == [str(index) for index in range(1, 51)]
    assert calls[0][1] == (
        "video_analysis_final_v1",
        "video_analysis_final_v1_keyframe_qa",
    )
    assert result["video_analysis"]["summary"]["ready_count"] == 50
    assert result["video_analysis"]["summary"]["qa_ready_count"] == 50

    monkeypatch.setattr(
        cache_repo,
        "canonical_final_v1_cache_reuse",
        lambda *_args, **_kwargs: {
            "reusable": False,
            "cache_id": 7,
            "reasons": ["cache_prompt_contract_mismatch"],
        },
    )
    legacy = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)
    legacy_item = legacy["video_analysis"]["items"][0]
    legacy_summary = legacy["video_analysis"]["summary"]
    assert legacy_item["state"] == "legacy_unverified"
    assert legacy_item["final_entry"] is None
    assert legacy_item["raw_final_entry"]["status"] == "ready"
    assert legacy_item["terminal"] is True
    assert legacy_item["revalidation_required"] is True
    assert legacy_summary["ready_count"] == 0
    assert legacy_summary["legacy_unverified_count"] == 50
    assert legacy_summary["qa_ready_count"] == 0
    assert legacy_summary["pending_count"] == 0


def test_detail_bundle_surfaces_quality_incomplete_as_terminal_not_pending(monkeypatch):
    def fake_video_evidence(_kol_pool_id: int, *, limit: int, **kwargs):
        del limit
        if kwargs.get("only_with_cache"):
            return [{"id": 77, "content_url": "https://example.com/video/77"}]
        return []

    monkeypatch.setattr(
        kol_pool,
        "get_item",
        lambda _kol_pool_id, **_kwargs: {"item": {"id": _kol_pool_id}},
    )
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", fake_video_evidence)
    monkeypatch.setattr(
        eleven_dimensions,
        "load_persisted_dimensions_11",
        lambda _kol_pool_id: None,
    )
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {
            "status": "empty",
            "count": 0,
            "limit": limit,
        },
    )
    monkeypatch.setattr(
        cache_repo,
        "get_analysis_cache_entries_for_targets",
        lambda *_args, **_kwargs: {
            ("77", "video_analysis_final_v1"): {
                "target_type": "video_quality_triage",
                "target_id": "77",
                "derive_method": "video_analysis_final_v1",
                "status": "quality_incomplete",
                "result": {
                    "quality_status": "quality_incomplete",
                    "quality_issues": ["missing_brand_product_evidence"],
                },
            }
        },
    )
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    item = result["video_analysis"]["items"][0]
    summary = result["video_analysis"]["summary"]
    assert item["state"] == "quality_incomplete"
    assert item["reason"] == "final_v1_quality_incomplete"
    assert item["final_entry"] is None
    assert summary["ready_count"] == 0
    assert summary["quality_incomplete_count"] == 1
    assert summary["pending_count"] == 0


def test_detail_bundle_derives_gear_from_raw_without_exposing_raw(monkeypatch):
    secret_raw = {"profile": {"description": "Sony FX3", "email": "private@example.com"}}
    observed_text: list[str] = []

    def item_stub(_kol_pool_id: int, **kwargs):
        assert kwargs["include_raw_for_derivation"] is True
        return {
            "item": {"id": _kol_pool_id, "bio": ""},
            "_raw_platform_data_for_derivation": secret_raw,
        }

    def gear_from_text(text: str):
        observed_text.append(text)
        return {"camera_body": "Sony FX3", "lens_brands": []}

    monkeypatch.setattr(kol_pool, "get_item", item_stub)
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(creator_gear, "aggregate_creator_gear", lambda _results: {})
    monkeypatch.setattr(creator_gear, "gear_from_text", gear_from_text)
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    assert observed_text and "Sony FX3" in observed_text[0]
    assert result["item"]["device_primary"] == "Sony FX3"
    assert "raw_platform_data" not in result["item"]
    assert "_raw_platform_data_for_derivation" not in result
    assert "private@example.com" not in json.dumps(result)


def test_detail_bundle_exposes_readiness_without_mutating_fit(monkeypatch):
    monkeypatch.setattr(
        kol_pool,
        "get_item",
        lambda _kol_pool_id, **_kwargs: {
            "item": {
                "id": _kol_pool_id,
                "viltrox_fit_score": 88,
                "updated_at": "2026-07-20T00:00:00Z",
            }
        },
    )
    monkeypatch.setattr(kol_pool, "_video_evidence_for_kol", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "missing", "count": 0, "items": [], "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    assert result["claim_status"] == "descriptive_only"
    assert result["analysis_readiness"]["level"] == "insufficient"
    assert result["analysis_readiness"]["decision_mode"] == "abstain"
    assert result["evidence_quality"]["gaps"]
    assert result["video_analysis"]["summary"]["analysis_readiness"]["status"] == "insufficient"
    assert result["item"]["viltrox_fit_score"] == 88
    assert result["diagnostics"]["viltrox_fit_score_write"] is False
    assert result["analysis_readiness"]["diagnostics"]["viltrox_fit_score_write"] is False


def test_detail_bundle_readiness_uses_30_active_rows_not_24_display_rows(monkeypatch):
    display_rows = [
        {
            "id": index,
            "evidence_id": index,
            "media_kind": "video",
            "view_count": 1000,
            "updated_at": "2026-07-20T00:00:00Z",
        }
        for index in range(1, 25)
    ]
    readiness_rows = [
        {
            "id": index,
            "evidence_id": index,
            "media_kind": "video",
            "view_count": 1000,
            "updated_at": "2026-07-20T00:00:00Z",
        }
        for index in range(1, 31)
    ]

    monkeypatch.setattr(
        kol_pool,
        "get_item",
        lambda _kol_pool_id, **_kwargs: {
            "item": {"id": _kol_pool_id, "updated_at": "2026-07-20T00:00:00Z"}
        },
    )
    monkeypatch.setattr(
        kol_pool,
        "_video_evidence_for_kol",
        lambda *_args, **kwargs: [] if kwargs.get("only_with_cache") else display_rows,
    )
    monkeypatch.setattr(
        analysis_readiness,
        "load_readiness_video_evidence",
        lambda *_args, **_kwargs: {
            "items": readiness_rows,
            "limit": 200,
            "truncated": False,
            "sample_scope": "active_video_evidence_up_to_200",
        },
    )
    monkeypatch.setattr(eleven_dimensions, "load_persisted_dimensions_11", lambda _kol_pool_id: None)
    monkeypatch.setattr(
        llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda _kol_pool_id, *, limit: {"status": "empty", "count": 0, "items": [], "limit": limit},
    )
    monkeypatch.setattr(cache_repo, "get_analysis_cache_entries_for_targets", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        audience_language,
        "audience_language_for_kol",
        lambda _kol_pool_id: {"sample_size": 0, "languages": []},
    )

    result = kol_pool.detail_bundle(13053, video_limit=24, llm_limit=20)

    assert len(result["item"]["video_evidence"]) == 24
    assert result["analysis_readiness"]["key_sample_count"] == 30
    assert result["analysis_readiness"]["evidence_coverage"]["sample_scope"] == (
        "active_video_evidence_up_to_200"
    )
    assert result["analysis_readiness"]["evidence_coverage"]["denominator_status"] == (
        "complete_for_scope"
    )
