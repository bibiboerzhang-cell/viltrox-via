from __future__ import annotations

import sys

import pytest

from app.services.ai.analyzers.gemini_video_results import (
    VIDEO_FINAL_LAYERS,
    InvalidFinalV1ResultError,
    _apply_final_v1_result,
    _normalise_final_v1_result,
    ensure_final_v1_result_cacheable,
    validate_final_v1_result,
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


def _apply(parsed: dict, *, model: str = "gemini-test") -> dict:
    result = {"analyzed": False, "error": None}
    _apply_final_v1_result(
        result,
        parsed,
        method="gemini_fileapi_gemini-test",
        model=model,
        usage_metadata={"total_token_count": 123},
        subtitle_used=False,
    )
    return result


def test_valid_legacy_fixture_remains_compatible_and_gains_truth_metadata():
    result = _apply(_valid_legacy_payload())

    assert result["analyzed"] is True
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["provenance"] == {
        "provider": "gemini",
        "model": "gemini-test",
        "method": "gemini_fileapi_gemini-test",
    }
    assert set(VIDEO_FINAL_LAYERS).issubset(result["video_analysis_final_v1"])
    assert result["video_analysis_final_v1"]["layer1_visual_content"]["evidence"]["subtitle_used"] is False
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


def test_cache_validator_upgrades_only_a_valid_legacy_completion_envelope():
    raw = {
        "analyzed": True,
        "method": "gemini_fileapi_legacy",
        "model": "gemini-legacy",
        "video_analysis_final_v1": _valid_legacy_payload(),
    }

    ensure_final_v1_result_cacheable(raw)

    assert raw["status"] == "completed"
    assert raw["provenance"]["model"] == "gemini-legacy"


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


def test_worker_rejects_forged_analyzed_result_without_downstream(monkeypatch):
    from app.workers import apify_jobs_worker

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
