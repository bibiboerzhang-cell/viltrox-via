from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool


def _assert_error(exc: HTTPException, *, status: int, code: str, retryable: bool) -> None:
    assert exc.status_code == status
    assert isinstance(exc.detail, dict)
    assert exc.detail["code"] == code
    assert exc.detail["retryable"] is retryable
    assert len(exc.detail["correlation_id"]) == 32


def test_single_video_enqueue_parameter_and_queue_errors_are_stable(monkeypatch):
    with pytest.raises(HTTPException) as invalid:
        vkpi_kol_pool.enqueue_pool_item_video_analysis(10, {"evidence_id": "bad"}, staff={})
    _assert_error(
        invalid.value,
        status=422,
        code="video_analysis_enqueue_invalid_request",
        retryable=False,
    )

    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_final_v1_video_analysis",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable password=secret")),
    )
    with pytest.raises(HTTPException) as unavailable:
        vkpi_kol_pool.enqueue_pool_item_video_analysis(10, {"evidence_id": 20}, staff={})
    _assert_error(
        unavailable.value,
        status=503,
        code="video_analysis_enqueue_queue_unavailable",
        retryable=True,
    )
    assert "secret" not in str(unavailable.value.detail)


def test_video_enqueue_conflict_and_unknown_errors_do_not_leak(monkeypatch):
    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_final_v1_video_analysis",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("viltrox_fit_score_changed_ids=[10]; rolled back")),
    )
    with pytest.raises(HTTPException) as conflict:
        vkpi_kol_pool.enqueue_pool_item_video_analysis(10, {"evidence_id": 20}, staff={})
    _assert_error(
        conflict.value,
        status=409,
        code="video_analysis_enqueue_conflict",
        retryable=False,
    )

    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_all_kol_videos",
        lambda **_kwargs: (_ for _ in ()).throw(Exception("internal dsn secret")),
    )
    with pytest.raises(HTTPException) as unknown:
        vkpi_kol_pool.enqueue_pool_all_videos(10, staff={})
    _assert_error(
        unknown.value,
        status=500,
        code="video_analysis_all_enqueue_internal_error",
        retryable=False,
    )
    assert "internal dsn secret" not in str(unknown.value.detail)


def test_batch_errors_are_sanitized_and_promote_validates_mode(monkeypatch):
    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_final_v1_video_analysis_batch",
        lambda **_kwargs: {
            "status": "completed",
            "items": [
                {"status": "error", "reason": "postgres password=secret", "kol_pool_id": 1},
                {"status": "not_found", "reason": "private row detail", "kol_pool_id": 2},
            ],
        },
    )
    result = vkpi_kol_pool.enqueue_pool_video_analysis_batch(
        {"items": [{"kol_pool_id": 1, "evidence_id": 2}]},
        staff={},
    )
    assert "secret" not in str(result)
    assert "private row detail" not in str(result)
    assert result["items"][0]["code"] == "video_analysis_item_failed"
    assert result["items"][1]["code"] == "video_evidence_not_found"

    with pytest.raises(HTTPException) as invalid_mode:
        vkpi_kol_pool.promote_to_main_kol(10, {"mode": "force"}, staff={})
    _assert_error(invalid_mode.value, status=422, code="kol_promote_invalid_request", retryable=False)


def test_batch_and_all_video_endpoint_failures_use_expected_statuses(monkeypatch):
    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_final_v1_video_analysis_batch",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("apify_jobs queue unavailable")),
    )
    with pytest.raises(HTTPException) as batch_unavailable:
        vkpi_kol_pool.enqueue_pool_video_analysis_batch(
            {"items": [{"kol_pool_id": 1, "evidence_id": 2}]},
            staff={},
        )
    _assert_error(
        batch_unavailable.value,
        status=503,
        code="video_analysis_batch_enqueue_queue_unavailable",
        retryable=True,
    )

    monkeypatch.setattr(
        vkpi_kol_pool.kol_video_analysis_enqueue,
        "enqueue_all_kol_videos",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("invalid limit")),
    )
    with pytest.raises(HTTPException) as all_invalid:
        vkpi_kol_pool.enqueue_pool_all_videos(10, staff={})
    _assert_error(
        all_invalid.value,
        status=422,
        code="video_analysis_all_enqueue_invalid_request",
        retryable=False,
    )


def test_promote_runtime_failure_is_service_unavailable(monkeypatch):
    monkeypatch.setattr(
        vkpi_kol_pool.kol_pool,
        "promote_to_main",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("kols table is not available")),
    )
    with pytest.raises(HTTPException) as unavailable:
        vkpi_kol_pool.promote_to_main_kol(10, {}, staff={})
    _assert_error(unavailable.value, status=503, code="kol_promote_queue_unavailable", retryable=True)
