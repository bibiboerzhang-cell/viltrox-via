from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.routers import vkpi_kol_pool_intel
from app.domains.kol import audience_stats


def _commenters(count: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "author_key": f"creator-{index}",
            "display_name": f"Creator {index}",
            "bio": "camera reviewer",
            "comment_text": "I use this lens for client work",
        }
        for index in range(1, count + 1)
    ]


def test_age_llm_batch_uses_validated_json_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform import llm_gateway

    captured: dict[str, Any] = {}

    def fake_invoke_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        payload = [
            {"i": 1, "age": "19-29", "gender": "", "conf": 0.4},
            {"i": 2, "age": "30-39", "gender": "female", "conf": 0.6},
        ]
        assert kwargs["validator"](payload) == (True, "")
        return {"status": "success", "provider": "google", "json": payload}

    monkeypatch.setattr(llm_gateway, "invoke_json", fake_invoke_json)

    result, stats = audience_stats._age_llm_batches(_commenters())

    assert set(result) == {"creator-1", "creator-2"}
    assert stats["status"] == "ok"
    assert stats["reason"] == ""
    assert captured["deadline_seconds"] == audience_stats.AGE_LLM_DEADLINE_SECONDS
    assert captured["max_provider_attempts"] == 1


@pytest.mark.parametrize(
    ("gateway_result", "expected_reason"),
    [
        (
            {
                "status": "fallback_to_rule",
                "provider": "rule_v0",
                "reason": "all_providers_failed",
                "json": None,
                "errors": [{"status": "parse_failure"}],
            },
            "invalid_json",
        ),
        (
            {
                "status": "fallback_to_rule",
                "provider": "rule_v0",
                "reason": "deadline_exceeded",
                "json": None,
                "errors": [{"status": "deadline_exceeded"}],
            },
            "provider_timeout",
        ),
    ],
)
def test_age_llm_bad_json_and_timeout_are_explicit_partial_inputs(
    monkeypatch: pytest.MonkeyPatch,
    gateway_result: dict[str, Any],
    expected_reason: str,
) -> None:
    from app.platform import llm_gateway

    monkeypatch.setattr(llm_gateway, "invoke_json", lambda *args, **kwargs: gateway_result)

    result, stats = audience_stats._age_llm_batches(_commenters(1))

    assert result == {}
    assert stats["status"] == "failed"
    assert stats["reason"] == expected_reason
    assert stats["failure_counts"] == {expected_reason: 1}


def test_age_llm_provider_exception_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform import llm_gateway

    monkeypatch.setattr(
        llm_gateway,
        "invoke_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("provider timeout")),
    )

    result, stats = audience_stats._age_llm_batches(_commenters(1))

    assert result == {}
    assert stats["status"] == "failed"
    assert stats["reason"] == "provider_exception"


def test_age_llm_non_object_gateway_response_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform import llm_gateway

    monkeypatch.setattr(llm_gateway, "invoke_json", lambda *args, **kwargs: ["not", "an", "object"])

    result, stats = audience_stats._age_llm_batches(_commenters(1))

    assert result == {}
    assert stats["status"] == "failed"
    assert stats["reason"] == "invalid_gateway_response"


def test_youtube_profile_timeout_preserves_partial_comment_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audience_stats, "_yt_api_key", lambda: "test-key")
    monkeypatch.setattr(audience_stats, "_resolve_channel_id", lambda _ref: "UCtest-channel-123456789")

    def fake_yt_get(endpoint: str, params: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
        if endpoint == "commentThreads":
            return {
                "items": [{
                    "snippet": {
                        "topLevelComment": {
                            "snippet": {
                                "authorDisplayName": "Reviewer",
                                "authorChannelId": {"value": "UCreviewer-1234567890"},
                                "textOriginal": "Useful review",
                            }
                        },
                        "totalReplyCount": 0,
                    }
                }]
            }
        raise RuntimeError("profile enrichment timed out")

    monkeypatch.setattr(audience_stats, "_yt_get", fake_yt_get)

    result = audience_stats.sample_youtube_commenters("@reviewer", max_comments=1)

    assert result["status"] == "ok"
    assert result["partial"] is True
    assert result["reason"] == "youtube_commenter_profile_enrichment_unavailable"
    assert result["comments_scanned"] == 1
    assert len(result["commenters"]) == 1


@pytest.mark.parametrize(
    ("service_result", "expected_status", "expected_reason"),
    [
        (
            {"status": "network_error", "reason": "upstream timed out"},
            502,
            "audience_provider_unavailable",
        ),
        (
            {"status": "not_configured", "reason": "missing key"},
            503,
            "audience_provider_not_configured",
        ),
    ],
)
def test_audience_refresh_maps_provider_failures_to_diagnostic_http(
    monkeypatch: pytest.MonkeyPatch,
    service_result: dict[str, Any],
    expected_status: int,
    expected_reason: str,
) -> None:
    async def direct_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(vkpi_kol_pool_intel, "run_in_threadpool", direct_call)
    monkeypatch.setattr(audience_stats, "refresh_audience_stats", lambda _kol_id: service_result)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(vkpi_kol_pool_intel.refresh_kol_audience_stats(7, staff={"id": 3}))

    assert raised.value.status_code == expected_status
    assert raised.value.detail["reason"] == expected_reason
    assert raised.value.detail["retryable"] is True


def test_audience_refresh_preserves_honest_empty_and_partial_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def direct_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    partial = {
        "status": "partial",
        "reason": "invalid_json",
        "sample_size": 42,
        "partial_components": ["age_llm"],
    }
    monkeypatch.setattr(vkpi_kol_pool_intel, "run_in_threadpool", direct_call)
    monkeypatch.setattr(audience_stats, "refresh_audience_stats", lambda _kol_id: partial)

    result = asyncio.run(vkpi_kol_pool_intel.refresh_kol_audience_stats(7, staff={"id": 3}))

    assert result == partial


def test_deep_analysis_read_failure_returns_stable_unavailable_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vkpi_kol_pool_intel.kol_llm_deep_analysis,
        "get_kol_llm_deep_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    result = vkpi_kol_pool_intel.get_pool_item_llm_deep_analysis(19, limit=20, staff={"id": 2})

    assert result["status"] == "unavailable"
    assert result["reason"] == "deep_analysis_read_failed"
    assert result["items"] == []
    assert result["count"] == 0


def test_content_fit_generate_failure_is_diagnostic_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def direct_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    monkeypatch.setattr(vkpi_kol_pool_intel, "run_in_threadpool", direct_call)
    monkeypatch.setattr(
        vkpi_kol_pool_intel,
        "_enqueue_content_fit_on_demand",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            vkpi_kol_pool_intel.get_pool_item_content_fit(
                23,
                analyze=True,
                force=False,
                product_sku=None,
                staff={"id": 2},
            )
        )

    assert raised.value.status_code == 503
    assert raised.value.detail["reason"] == "content_fit_enqueue_failed"


def test_bio_translation_provider_failure_returns_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.platform import llm_gateway

    vkpi_kol_pool_intel._BIO_ZH_CACHE.clear()
    monkeypatch.setattr(
        llm_gateway,
        "invoke",
        lambda **kwargs: {
            "status": "fallback_to_rule",
            "reason": "all_providers_failed",
            "text": "",
        },
    )

    result = vkpi_kol_pool_intel.translate_bio({"text": "camera reviewer"}, staff={"id": 5})

    assert result["status"] == "partial"
    assert result["reason"] == "all_providers_failed"
    assert result["translated"] == ""
