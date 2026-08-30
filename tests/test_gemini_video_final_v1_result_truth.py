from __future__ import annotations

import json
import sys

import pytest

from app.services.ai.analyzers.gemini_video_results import (
    FINAL_V1_QUALITY_COMPLETE,
    FINAL_V1_QUALITY_INCOMPLETE,
    VIDEO_FINAL_LAYERS,
    InvalidFinalV1ResultError,
    _apply_final_v1_result,
    _normalise_final_v1_result,
    ensure_final_v1_result_cacheable,
    validate_final_v1_result,
)
from app.services.ai.analyzers.gemini_video_prompts import (
    _video_final_v1_dynamic_prompt,
    _video_final_v1_prompt,
    _video_final_v1_static_prompt,
)
from app.workers.apify_jobs_worker_helpers import _error_category, _failure_disposition


def _valid_legacy_payload() -> dict:
    return {
        "layer1_visual_content": {
            "content_summary": "A creator compares autofocus and flare performance.",
            "scene_timeline": [
                {"timestamp": "00:04", "what": "Lens close-up followed by autofocus samples."},
            ],
            "evidence": {"timestamps": ["00:04 lens close-up"]},
        },
        "layer6_flags_and_scores": {
            "final_verdict": "Useful category evidence for creator discovery.",
        },
    }


def _apply(parsed: dict, *, model: str = "gemini-test", subtitle_used: bool = False) -> dict:
    result = {"analyzed": False, "error": None}
    _apply_final_v1_result(
        result,
        parsed,
        method="gemini_fileapi_gemini-test",
        model=model,
        usage_metadata={"total_token_count": 123},
        subtitle_used=subtitle_used,
    )
    return result


def test_valid_legacy_fixture_remains_compatible_and_gains_truth_metadata():
    result = _apply(_valid_legacy_payload())

    assert result["analyzed"] is True
    assert result["status"] == "completed"
    assert result["quality_status"] == FINAL_V1_QUALITY_INCOMPLETE
    assert "missing_brand_product_evidence" in result["quality_issues"]
    assert "layer6_flags_and_scores.scores" in result["quality_issues"]
    assert result["error"] is None
    assert result["provenance"] == {
        "provider": "gemini",
        "model": "gemini-test",
        "method": "gemini_fileapi_gemini-test",
    }
    assert set(VIDEO_FINAL_LAYERS).issubset(result["video_analysis_final_v1"])
    assert result["video_analysis_final_v1"]["layer1_visual_content"]["evidence"]["subtitle_used"] is False
    assert result["brand_product_evidence"]["viltrox_status"] == "unknown"
    assert result["viltrox_detected"] is None
    assert result["viltrox_products_all"] == []
    assert validate_final_v1_result(result, allow_legacy_status=False) == []


def test_product_identification_plus_timestamp_evidence_is_a_valid_minimum():
    result = _apply(
        {
            "layer1_visual_content": {
                "product_presence": {"products": ["Viltrox AF 27mm F1.2 Pro"]},
                "evidence": {"timestamps": ["00:08 product name visible on lens barrel"]},
            },
        }
    )

    assert result["analyzed"] is True
    assert result["status"] == "completed"
    assert result["quality_status"] == FINAL_V1_QUALITY_INCOMPLETE


def _brand_fixture(brand_product_evidence: dict) -> dict:
    return {
        "layer1_visual_content": {
            "content_summary": "A non-title lens demonstration with attributable brand evidence.",
            "scene_timeline": [{"timestamp": "00:08", "what": "Lens demonstration."}],
            "brand_product_evidence": brand_product_evidence,
            "evidence": {"timestamps": ["00:08 lens demonstration"]},
        },
        "layer6_flags_and_scores": {"final_verdict": "Evidence-bounded brand review."},
    }


def _quality_complete_payload() -> dict:
    payload = _brand_fixture(
        {
            "viltrox_status": "unknown",
            "inspection_complete": True,
            "checked_modalities": ["visual", "audio"],
            "viltrox_evidence": [],
            "viltrox_products": [],
            "competitors": [],
        }
    )
    payload["layer6_flags_and_scores"] = {
        "risk_flags": [],
        "scores": {
            key: {"score": None, "confidence": 0.0, "rationale": "Insufficient evidence."}
            for key in (
                "content_quality_score",
                "viewer_heart_score",
                "channel_value_score",
                "asset_reuse_score",
                "product_proof_score",
                "marketing_value_score",
            )
        },
        "final_verdict": "Evidence-bounded brand review.",
        "key_hook": "No attributable brand claim was found.",
    }
    return payload


def test_complete_quality_contract_is_ready_without_inventing_scores():
    result = _apply(_quality_complete_payload())

    assert result["status"] == "completed"
    assert result["quality_status"] == FINAL_V1_QUALITY_COMPLETE
    assert result["quality_issues"] == []
    assert ensure_final_v1_result_cacheable(result) == "ready"


def _evidence(modality: str, detail: str, timestamp: str = "00:08") -> dict:
    return {
        "modality": modality,
        "timestamp": timestamp,
        "detail": detail,
        "confidence": 0.96,
    }


def test_non_title_visual_evidence_projects_present_truth_and_compat_aliases():
    result = _apply(
        _brand_fixture(
            {
                "viltrox_status": "present",
                "inspection_complete": True,
                "checked_modalities": ["visual", "audio"],
                "viltrox_evidence": [_evidence("visual", "Viltrox logo visible on the lens barrel")],
                "viltrox_products": [],
                "competitors": [],
            }
        )
    )

    assert result["brand_product_evidence"]["viltrox_status"] == "present"
    assert result["viltrox_detected"] is True
    assert result["viltrox_products_all"] == []


def test_non_title_subtitle_evidence_projects_product_without_prose_scan():
    subtitle_evidence = _evidence("subtitle", "Subtitle explicitly names Viltrox AF 27mm F1.2 Pro", "00:23")
    result = _apply(
        _brand_fixture(
            {
                "viltrox_status": "present",
                "inspection_complete": True,
                "checked_modalities": ["visual", "audio", "subtitle"],
                "viltrox_evidence": [subtitle_evidence],
                "viltrox_products": [
                    {
                        "name": "Viltrox AF 27mm F1.2 Pro",
                        "sku": None,
                        "confidence": 0.94,
                        "evidence": [subtitle_evidence],
                    }
                ],
                "competitors": [],
            }
        ),
        subtitle_used=True,
    )

    assert result["viltrox_detected"] is True
    assert result["viltrox_products_all"] == ["Viltrox AF 27mm F1.2 Pro"]
    assert result["video_analysis_final_v1"]["layer1_visual_content"]["evidence"]["subtitle_used"] is True


def test_sigma_only_complete_inspection_is_explicit_absent_and_keeps_competitor_evidence():
    sigma_evidence = _evidence("visual", "Sigma name and logo visible on comparison lens", "00:31")
    result = _apply(
        _brand_fixture(
            {
                "viltrox_status": "absent",
                "inspection_complete": True,
                "checked_modalities": ["visual", "audio", "metadata"],
                "viltrox_evidence": [],
                "viltrox_products": [],
                "competitors": [
                    {
                        "brand": "Sigma",
                        "products": ["Sigma 30mm F1.4"],
                        "confidence": 0.97,
                        "evidence": [sigma_evidence],
                    }
                ],
            }
        )
    )

    assert result["brand_product_evidence"]["viltrox_status"] == "absent"
    assert result["viltrox_detected"] is False
    assert result["viltrox_products_all"] == []
    assert result["competitor_mentions"][0]["brand"] == "Sigma"


def test_incomplete_absence_check_downgrades_to_unknown_not_false():
    result = _apply(
        _brand_fixture(
            {
                "viltrox_status": "absent",
                "inspection_complete": True,
                "checked_modalities": ["visual"],
                "viltrox_evidence": [],
                "viltrox_products": [],
                "competitors": [],
            }
        )
    )

    assert result["brand_product_evidence"]["viltrox_status"] == "unknown"
    assert result["viltrox_detected"] is None


def test_product_name_without_structured_evidence_cannot_create_present_truth():
    result = _apply(
        _brand_fixture(
            {
                "viltrox_status": "present",
                "inspection_complete": True,
                "checked_modalities": ["visual", "audio"],
                "viltrox_evidence": [],
                "viltrox_products": [
                    {"name": "Viltrox AF 85mm F1.4 Pro", "sku": "AF-85-PRO", "evidence": []}
                ],
                "competitors": [],
            }
        )
    )

    assert result["brand_product_evidence"]["viltrox_status"] == "unknown"
    assert result["brand_product_evidence"]["viltrox_products"] == []
    assert result["viltrox_detected"] is None
    assert result["viltrox_products_all"] == []


def test_brand_truth_prompt_requires_tri_state_and_timed_non_metadata_evidence():
    dynamic = _video_final_v1_prompt(
        title="Autofocus comparison",
        profile_ctx="",
        subtitle_ctx="",
        subtitle_used=False,
        performance_context={},
    )
    static = _video_final_v1_static_prompt()

    for prompt in (dynamic, static):
        assert "brand_product_evidence" in prompt
        assert "present" in prompt and "absent" in prompt and "unknown" in prompt
        assert "metadata" in prompt and "visual" in prompt and "audio" in prompt


def test_cached_and_dynamic_prompts_pin_brand_evidence_to_its_canonical_path():
    dynamic = _video_final_v1_dynamic_prompt(
        title="Autofocus comparison",
        profile_ctx="",
        subtitle_ctx="",
        subtitle_used=False,
        performance_context={},
    )
    static = _video_final_v1_static_prompt()

    for prompt in (dynamic, static):
        assert "/layer1_visual_content/brand_product_evidence" in prompt
        assert "evidence 同级" in prompt
        assert "对象数组" in prompt


@pytest.mark.parametrize(
    ("parsed", "reason"),
    [
        ({}, "empty_payload"),
        ({layer: {} for layer in VIDEO_FINAL_LAYERS}, "missing_core_content"),
        (
            {"layer1_visual_content": {"content_summary": "Summary without evidence."}},
            "missing_evidence",
        ),
        (
            {"layer1_visual_content": {"evidence": {"timestamps": ["00:03 close-up"]}}},
            "missing_core_content",
        ),
    ],
)
def test_invalid_payloads_never_become_analyzed(parsed: dict, reason: str):
    result = {"analyzed": False, "error": None}

    with pytest.raises(InvalidFinalV1ResultError, match=reason):
        _apply_final_v1_result(
            result,
            parsed,
            method="gemini_fileapi_gemini-test",
            model="gemini-test",
            usage_metadata={},
            subtitle_used=False,
        )

    assert result["analyzed"] is False
    assert result["status"] == "invalid_result"
    assert reason in result["error"]
    assert "video_analysis_final_v1" not in result


def test_normalizer_itself_rejects_empty_layers():
    with pytest.raises(InvalidFinalV1ResultError, match="missing_core_content"):
        _normalise_final_v1_result(
            {layer: {} for layer in VIDEO_FINAL_LAYERS},
            subtitle_used=False,
        )


def test_missing_model_is_invalid_even_with_real_content_and_evidence():
    result = {"analyzed": False}

    with pytest.raises(InvalidFinalV1ResultError, match="missing_model"):
        _apply_final_v1_result(
            result,
            _valid_legacy_payload(),
            method="gemini_fileapi_unknown",
            model="",
            usage_metadata={},
            subtitle_used=False,
        )

    assert result["status"] == "invalid_result"
    assert result["analyzed"] is False


def test_later_valid_model_clears_an_earlier_invalid_result_state():
    result = {"analyzed": False, "error": None}
    with pytest.raises(InvalidFinalV1ResultError):
        _apply_final_v1_result(
            result,
            {},
            method="gemini_fileapi_first",
            model="gemini-first",
            usage_metadata={},
            subtitle_used=False,
        )

    _apply_final_v1_result(
        result,
        _valid_legacy_payload(),
        method="gemini_fileapi_second",
        model="gemini-second",
        usage_metadata={},
        subtitle_used=False,
    )

    assert result["analyzed"] is True
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["model"] == "gemini-second"


def test_cache_validator_keeps_legacy_execution_but_does_not_promote_its_quality():
    raw = {
        "analyzed": True,
        "method": "gemini_fileapi_legacy",
        "model": "gemini-legacy",
        "video_analysis_final_v1": _valid_legacy_payload(),
    }

    cache_status = ensure_final_v1_result_cacheable(raw)

    assert raw["status"] == "completed"
    assert raw["provenance"]["model"] == "gemini-legacy"
    assert raw["quality_status"] == FINAL_V1_QUALITY_INCOMPLETE
    assert cache_status == "quality_incomplete"


def test_missing_completion_state_is_not_cacheable():
    raw = {
        "method": "gemini_fileapi_gemini-test",
        "model": "gemini-test",
        "video_analysis_final_v1": _valid_legacy_payload(),
    }

    with pytest.raises(InvalidFinalV1ResultError, match="missing_completion_state"):
        ensure_final_v1_result_cacheable(raw)

    assert raw["status"] == "invalid_result"
    assert raw["analyzed"] is False


def test_invalid_result_error_remains_retryable_in_worker_policy():
    category = _error_category("invalid_result: final_v1 validation failed (missing_evidence)")

    assert category == "unknown"
    assert _failure_disposition(category) == "retry"


def test_worker_cache_guard_stops_before_ready_transaction():
    from app.workers import apify_jobs_worker

    class NeverTransactionConnection:
        transaction_called = False

        def transaction(self):
            self.transaction_called = True
            raise AssertionError("invalid final_v1 must not open a cache transaction")

    conn = NeverTransactionConnection()
    raw = {
        "analyzed": True,
        "status": "completed",
        "method": "gemini_fileapi_gemini-test",
        "model": "gemini-test",
        "video_analysis_final_v1": {layer: {} for layer in VIDEO_FINAL_LAYERS},
    }

    with pytest.raises(InvalidFinalV1ResultError, match="missing_core_content"):
        apify_jobs_worker._write_gemini_cache(
            conn,
            job={"id": 99},
            payload={"target_type": "video", "target_id": "123"},
            evidence={"id": 123},
            raw=raw,
            cost=0.0,
            cost_basis="test",
            preflight_cost=0.0,
            latency_ms=1,
            derive_method="video_analysis_final_v1",
        )

    assert conn.transaction_called is False
    assert raw["status"] == "invalid_result"
    assert raw["analyzed"] is False


def test_authorization_snapshot_missing_is_triaged_and_never_runs_ready_followups(monkeypatch):
    import app.workers.apify_jobs_worker  # noqa: F401
    from app.workers import apify_jobs_worker_gemini as worker_gemini

    statements: list[tuple[str, tuple]] = []
    syncs: list[dict] = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=()):
            statements.append((" ".join(str(sql).split()), params))

        def fetchone(self):
            return (42,)

    class _Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Connection:
        def transaction(self):
            return _Transaction()

        def cursor(self, **_kwargs):
            return _Cursor()

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("quality_incomplete entered a ready-only followup")

    monkeypatch.setattr(
        worker_gemini,
        "_sync_search_session_job",
        lambda *_args, **kwargs: syncs.append(kwargs) or True,
    )
    monkeypatch.setattr(worker_gemini, "_sync_deep_analysis_result_from_cache", _forbidden)
    monkeypatch.setattr(worker_gemini, "_enqueue_account_dossier_extract_after_final_v1", _forbidden)
    monkeypatch.setattr(worker_gemini, "_enqueue_content_fit_after_final_v1", _forbidden)
    monkeypatch.setattr(worker_gemini, "extract_lens_evidence_after_final_v1", _forbidden)

    raw = {
        "analyzed": True,
        "status": "completed",
        "method": "gemini_fileapi_gemini-test",
        "model": "gemini-test",
        "video_analysis_final_v1": _valid_legacy_payload(),
        "llm_execution": {
            "production_authorized": False,
            "authorization_snapshot_match": False,
            "authorization_issue": "authorization_snapshot_missing",
        },
    }
    worker_gemini._mark_authorization_snapshot_missing(raw)
    worker_gemini._write_gemini_cache(
        _Connection(),
        job={"id": 99},
        payload={"target_type": "video", "target_id": "123"},
        evidence={"id": 123},
        raw=raw,
        cost=0.01,
        cost_basis="test",
        preflight_cost=0.01,
        latency_ms=10,
        derive_method="video_analysis_final_v1",
    )

    insert = next((sql, params) for sql, params in statements if "INSERT INTO vkpi_analysis_cache" in sql)
    assert insert[1][0] == "video_quality_triage"
    assert insert[1][9] == "quality_incomplete"
    assert json.loads(insert[1][4])["quality_status"] == "quality_incomplete"
    triage = next((sql, params) for sql, params in statements if "SET status='triage'" in sql)
    assert json.loads(triage[1][0])["reason"] == "authorization_snapshot_missing"
    assert not any("SET status='done'" in sql for sql, _params in statements)
    assert syncs == [
        {
            "raw_status": "triage",
            "reason": "authorization_snapshot_missing",
            "analysis_summary": {
                "status": "quality_incomplete",
                "cache_id": 42,
                "derive_method": "video_analysis_final_v1",
                "target_type": "video",
                "cache_target_type": "video_quality_triage",
                "target_id": "123",
                "quality_issues": raw["quality_issues"],
            },
        }
    ]


def test_worker_rejects_forged_analyzed_result_without_downstream(monkeypatch):
    from app.workers import apify_jobs_worker
    from app.workers import apify_jobs_worker_paid_scope

    gemini_worker = sys.modules[apify_jobs_worker._process_gemini_video.__module__]
    raw = {
        "analyzed": True,
        "status": "completed",
        # Keep the exact-model contract valid so this test reaches the
        # independent final_v1 structure guard it is intended to exercise.
        "method": f"gemini_fileapi_{apify_jobs_worker.WORKER_GEMINI_MODEL}",
        "model": apify_jobs_worker.WORKER_GEMINI_MODEL,
        "video_analysis_final_v1": {layer: {} for layer in VIDEO_FINAL_LAYERS},
    }
    ledger_inputs = []
    downstream_calls = []

    monkeypatch.setattr(
        gemini_worker,
        "_load_video_evidence",
        lambda _conn, _target_id: {
            "id": 123,
            "content_url": "https://www.youtube.com/watch?v=test",
            "title": "test",
        },
    )
    monkeypatch.setattr(gemini_worker, "_run_gemini_analyzer_with_timeout", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(gemini_worker, "_gemini_cost", lambda *_args: (0.01, "test", 10, 10))
    monkeypatch.setattr(
        apify_jobs_worker_paid_scope,
        "revalidate_paid_job_scope",
        lambda *_args, **_kwargs: ("video_analysis", "", {"id": 1}),
    )

    def record_cost(**kwargs):
        ledger_inputs.append(kwargs["raw"].copy())
        return {"status": "recorded"}

    monkeypatch.setattr(gemini_worker, "_record_gemini_cost", record_cost)
    monkeypatch.setattr(
        gemini_worker,
        "_sync_deep_analysis_result_from_cache",
        lambda *_args, **_kwargs: downstream_calls.append("deep_sync"),
    )
    monkeypatch.setattr(
        gemini_worker,
        "_enqueue_account_dossier_extract_after_final_v1",
        lambda *_args, **_kwargs: downstream_calls.append("dossier"),
    )
    monkeypatch.setattr(
        gemini_worker,
        "_enqueue_content_fit_after_final_v1",
        lambda *_args, **_kwargs: downstream_calls.append("content_fit"),
    )

    class NeverTransactionConnection:
        def transaction(self):
            raise AssertionError("invalid final_v1 must not open a cache transaction")

    with pytest.raises(InvalidFinalV1ResultError, match="missing_core_content"):
        gemini_worker._process_gemini_video(
            NeverTransactionConnection(),
            {"id": 99},
            {
                "target_type": "video",
                "target_id": "123",
                "derive_method": "video_analysis_final_v1",
            },
            0.01,
        )

    assert ledger_inputs[0]["status"] == "invalid_result"
    assert ledger_inputs[0]["analyzed"] is False
    assert downstream_calls == []
