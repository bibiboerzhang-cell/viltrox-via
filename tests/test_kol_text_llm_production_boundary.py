from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.domains.kol import content_fit_analysis, outreach_pack, product_fit_helpers


def _content_payload() -> dict[str, Any]:
    return {
        "creator_type": "camera reviewer",
        "content_summary": "The creator demonstrates lenses in real shooting scenes.",
        "audience_signal": "Comments ask about autofocus and image quality.",
        "fit_verdict": "fit",
        "fit_reasons": ["The reviewed footage includes a concrete lens test."],
        "confidence": 0.82,
    }


def _stub_content_fit_evidence(monkeypatch) -> list[dict[str, Any]]:
    writes: list[dict[str, Any]] = []
    monkeypatch.setattr(content_fit_analysis, "get_conn", lambda: object())
    monkeypatch.setattr(
        content_fit_analysis,
        "_kol_row",
        lambda *_args, **_kwargs: {
            "id": 42,
            "handle": "creator",
            "display_name": "Creator",
            "platform": "youtube",
            "followers": 1000,
            "primary_topic": "camera",
        },
    )
    monkeypatch.setattr(content_fit_analysis, "_read_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        content_fit_analysis,
        "_video_analyses",
        lambda *_args, **_kwargs: [{"evidence_id": 7, "platform": "youtube", "title": "Lens review"}],
    )
    monkeypatch.setattr(content_fit_analysis, "_fan_comments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(content_fit_analysis, "_dimensions_11", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        content_fit_analysis,
        "_resolve_product",
        lambda *_args, **_kwargs: {"mode": "persona", "persona": "camera lens"},
    )
    monkeypatch.setattr(
        content_fit_analysis,
        "_write_cache",
        lambda *_args, **kwargs: writes.append(kwargs),
    )
    monkeypatch.setattr(
        content_fit_analysis,
        "_model_binding",
        lambda: ("openai", "gpt-5.4-mini"),
    )
    return writes


def test_content_fit_uses_exact_atomic_json_boundary_and_progress_metadata(monkeypatch) -> None:
    writes = _stub_content_fit_evidence(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": _content_payload(),
            "cost_micro_usd": 17,
        }

    monkeypatch.setattr(content_fit_analysis.llm_production, "generate_json", fake_generate_json)
    result = content_fit_analysis.analyze_content_fit(
        42,
        product_persona="camera lens",
        force=True,
        staff={"user_id": 9},
    )

    assert result["state"] == "ready"
    assert result["result"]["provenance"] == {
        "model": "gpt-5.4-mini",
        "provider": "openai",
        "task_binding": "kol_content_fit_analysis",
        "fallback_used": False,
        "generated_at": result["result"]["provenance"]["generated_at"],
    }
    assert len(writes) == 1
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-5.4-mini"
    assert captured["purpose"] == "vkpi_kol_content_fit"
    assert captured["required_keys"]
    assert captured["metadata"]["phase"] == "kol_analysis"
    assert captured["metadata"]["subphase"] == "content_fit"
    assert captured["metadata"]["attempt_index"] == 1
    assert captured["metadata"]["target_label"] == "creator"


def test_content_fit_readiness_block_is_honest_and_never_cached(monkeypatch) -> None:
    writes = _stub_content_fit_evidence(monkeypatch)
    calls: list[dict[str, Any]] = []

    def blocked(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "degraded",
            "failure": {"code": "readiness_not_production_ready"},
        }

    monkeypatch.setattr(content_fit_analysis.llm_production, "generate_json", blocked)
    result = content_fit_analysis.analyze_content_fit(42, force=True)

    assert result["state"] == "llm_failed"
    assert result["reason"] == "readiness_not_production_ready"
    assert len(calls) == 1
    assert writes == []


def test_content_fit_retries_nested_transient_errors_with_exact_attempt_metadata(monkeypatch) -> None:
    writes = _stub_content_fit_evidence(monkeypatch)
    calls: list[dict[str, Any]] = []

    def rate_limited(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "degraded",
            "reason": "all_providers_failed",
            "errors": [
                {
                    "provider": "openai",
                    "error": {
                        "response": {
                            "error": {"code": "rate_limit_exceeded", "message": "retry later"}
                        }
                    },
                }
            ],
        }

    monkeypatch.setattr(content_fit_analysis.llm_production, "generate_json", rate_limited)
    result = content_fit_analysis.analyze_content_fit(42, force=True)

    assert result["state"] == "llm_failed"
    assert result["reason"] == "rate_limit_exceeded"
    assert [call["metadata"]["attempt_index"] for call in calls] == [1, 2, 3]
    assert all(call["provider"] == "openai" for call in calls)
    assert all(call["model"] == "gpt-5.4-mini" for call in calls)
    assert writes == []


def test_content_fit_classifies_nested_transport_errors_as_retryable() -> None:
    cases = (
        ({"error": {"type": "TimeoutError"}}, "TimeoutError"),
        ({"cause": {"message": "connection reset by peer"}}, "connection_error"),
        ({"response": {"status": 429, "message": "throttled"}}, "429"),
    )
    for nested, expected in cases:
        result = {
            "status": "degraded",
            "reason": "all_providers_failed",
            "errors": [{"provider": "openai", "error": nested}],
        }
        assert content_fit_analysis._failure_code(result) == expected
        assert content_fit_analysis._retryable_failure(result) is True


def _json_contract_fallback(status: str) -> dict[str, Any]:
    code = "schema_failure" if status in {"parse_failure", "validation_failure"} else status
    return {
        "status": "fallback_to_rule",
        "provider": "rule_v0",
        "model": "rule_v0",
        "json": None,
        "reason": "all_providers_failed",
        "failure": {
            "version": "llm_runtime_error_v1",
            "code": code,
            "category": "response_contract",
            "retryable": False,
        },
        "errors": [
            {
                "version": "llm_runtime_error_v1",
                "provider": "openai",
                "model": "gpt-5.4-mini",
                "status": status,
                "code": code,
                "category": "response_contract",
                "retryable": False,
                "error": status,
            }
        ],
    }


@pytest.mark.parametrize("failure_status", ["parse_failure", "validation_failure", "empty_response"])
def test_content_fit_retries_real_gateway_json_contract_fallbacks(
    monkeypatch,
    failure_status: str,
) -> None:
    writes = _stub_content_fit_evidence(monkeypatch)
    calls: list[dict[str, Any]] = []

    def contract_failure(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _json_contract_fallback(failure_status)

    monkeypatch.setattr(content_fit_analysis.llm_production, "generate_json", contract_failure)
    result = content_fit_analysis.analyze_content_fit(42, force=True)

    assert result["state"] == "llm_failed"
    assert result["reason"] == failure_status
    assert [call["metadata"]["attempt_index"] for call in calls] == [1, 2, 3]
    assert all(call["provider"] == "openai" for call in calls)
    assert all(call["model"] == "gpt-5.4-mini" for call in calls)
    assert writes == []


def test_content_fit_contract_retry_can_recover_on_third_exact_attempt(monkeypatch) -> None:
    writes = _stub_content_fit_evidence(monkeypatch)
    calls: list[dict[str, Any]] = []
    responses = [
        _json_contract_fallback("parse_failure"),
        _json_contract_fallback("validation_failure"),
        {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": _content_payload(),
        },
    ]

    def eventually_valid(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return responses[len(calls) - 1]

    monkeypatch.setattr(content_fit_analysis.llm_production, "generate_json", eventually_valid)
    result = content_fit_analysis.analyze_content_fit(42, force=True)

    assert result["state"] == "ready"
    assert [call["metadata"]["attempt_index"] for call in calls] == [1, 2, 3]
    assert len(writes) == 1


def test_content_fit_budget_block_wins_over_nested_connection_error(monkeypatch) -> None:
    _stub_content_fit_evidence(monkeypatch)
    calls: list[dict[str, Any]] = []

    def budget_blocked(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "status": "degraded",
            "failure": {"code": "budget_exceeded"},
            "errors": [{"error": {"message": "connection reset by peer"}}],
        }

    monkeypatch.setattr(content_fit_analysis.llm_production, "generate_json", budget_blocked)
    result = content_fit_analysis.analyze_content_fit(42, force=True)

    assert result["reason"] == "budget_exceeded"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "failure_code",
    [
        "readiness_not_production_ready",
        "budget_blocked",
        "provider_auth_failed",
        "model_binding_blocked",
        "model_not_registered",
        "provider_not_configured",
        "model_mismatch",
    ],
)
def test_content_fit_does_not_retry_terminal_gate_or_binding_failures(failure_code: str) -> None:
    result = {
        "status": "fallback_to_rule",
        "failure": {"code": failure_code},
        "errors": [{"status": failure_code, "code": failure_code}],
    }
    assert content_fit_analysis._retryable_failure(result) is False


def test_product_fit_reason_uses_exact_json_or_rule_fallback(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        product_fit_helpers,
        "_reason_model_binding",
        lambda: ("openai", "gpt-5.4-mini"),
    )

    def ready(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "json": {
                "short_reason": "The evidence supports this fit.",
                "pitch_angle": "Lead with the creator's lens reviews.",
                "caution_note": "Confirm availability before outreach.",
            },
        }

    monkeypatch.setattr(product_fit_helpers.llm_production, "generate_json", ready)
    payload = {"kol": {"kol_entity_uid": "kol-1", "handle": "creator"}}
    item = {
        "rank": 1,
        "product_family_uid": "family-1",
        "product_family_name": "Prime lenses",
        "evidence_pro": [{"detail": "review history"}],
        "evidence_con": [],
    }
    product_fit_helpers._attach_reason(payload, item)

    assert item["recommendation_reason"]["mode"] == "llm"
    assert item["recommendation_reason"]["status"] == "success"
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-5.4-mini"
    assert captured["metadata"]["phase"] == "kol_recommendation"
    assert captured["metadata"]["target_label"] == "Prime lenses"

    monkeypatch.setattr(
        product_fit_helpers.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "google",
            "model": "gemini-3.5-flash",
            "json": {
                "short_reason": "wrong binding",
                "pitch_angle": "wrong binding",
                "caution_note": "wrong binding",
            },
        },
    )
    fallback_item = dict(item)
    fallback_item.pop("recommendation_reason", None)
    product_fit_helpers._attach_reason(payload, fallback_item)
    assert fallback_item["recommendation_reason"]["mode"] == "deterministic_fallback"
    assert fallback_item["recommendation_reason"]["provider"] == "rule_v0"
    assert fallback_item["recommendation_reason"]["status"] == "degraded"
    assert fallback_item["recommendation_reason"]["fallback_reason"] == "exact_model_or_json_contract_mismatch"


def test_outreach_pack_requires_exact_valid_bilingual_json(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        outreach_pack,
        "_model_binding",
        lambda: ("anthropic", "claude-sonnet-4-6"),
    )

    def ready(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "json": {
                "subject": "VILTROX collaboration",
                "email_en": "Hello Creator, this is a collaboration proposal.",
                "email_zh": "Creator 您好，这是一份合作建议。",
                "talking_points": ["真实测评"],
            },
        }

    monkeypatch.setattr(outreach_pack.llm_production, "generate_json", ready)
    draft, provenance = outreach_pack._generate_email_draft(
        {"id": 3, "display_name": "Creator", "handle": "creator", "platform": "youtube"},
        {"why_fit": ["camera reviews"]},
        staff={"user_id": 8},
    )

    assert draft["personalized"] is True
    assert provenance["llm_used"] is True
    assert provenance["provider"] == "anthropic"
    assert provenance["model"] == "claude-sonnet-4-6"
    assert captured["required_keys"] == ("subject", "email_en", "email_zh", "talking_points")
    assert captured["metadata"]["phase"] == "kol_outreach"
    assert captured["metadata"]["subphase"] == "bilingual_draft"
    assert captured["metadata"]["attempt_index"] == 1


def test_kol_text_paths_have_no_legacy_gateway_invocation() -> None:
    for module in (content_fit_analysis, product_fit_helpers, outreach_pack):
        source = inspect.getsource(module)
        assert "llm_gateway.invoke(" not in source
        assert "llm_gateway.invoke_json(" not in source
        assert "llm_production.generate_json(" in source
