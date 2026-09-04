from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.kol import smart_query_planner as planner


def _without_product(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner.product_resolver, "resolve_product", lambda _query: None)
    monkeypatch.setattr(
        planner.product_resolver,
        "unresolved_product_request",
        lambda _query: None,
    )


def _valid_plan_json() -> dict[str, Any]:
    return {
        "objective": "prospective_growth",
        "segments": ["portrait photography"],
        "search_queries": ["portrait lens photographer"],
        "search_query": "portrait lens photographer",
        "product_focus": ["portrait photographer"],
        "target_persona": "US portrait photographers who publish lens tutorials.",
        "avoid_types": ["phone vlogger"],
        "product_positioning": "A portrait lens for working photographers.",
        "platforms": ["youtube"],
        "market": "US",
        "creator_quota": 15,
        "reviewer_quota": 15,
        "include_new_discovery": True,
        "new_discovery_limit": 15,
        "reason": "llm_plan",
    }


def test_planner_uses_validated_json_gateway_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_product(monkeypatch)
    captured: dict[str, Any] = {}

    def invoke_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"prompt": prompt, **kwargs})
        return {
            "status": "success",
            "provider": "google",
            "model": "gemini-test",
            "json": _valid_plan_json(),
            "text": "this legacy field must not be reparsed",
            "fallback_used": False,
            "provider_attempts": 1,
            "errors": [],
        }

    monkeypatch.setattr(planner.llm_gateway, "invoke_json", invoke_json)

    result = planner._plan_text_query_impl(
        "find US portrait lens creators",
        body={"use_product_persona": False},
    )

    assert captured["purpose"] == "kol_smart_search_query_plan"
    assert captured["required_keys"] == planner.PLANNER_REQUIRED_KEYS
    assert captured["validator"](_valid_plan_json()) == (True, "")
    assert result["status"] == "ready"
    assert result["provider_calls_performed"] is True
    assert result["provider_response_succeeded"] is True
    assert result["planner_parse_status"] == "success"
    assert result["planner_parse_failed"] is False


def test_planner_exposes_provider_success_and_parse_failure_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_product(monkeypatch)
    monkeypatch.setattr(
        planner.llm_gateway,
        "invoke_json",
        lambda *_args, **_kwargs: {
            "status": "fallback_to_rule",
            "provider": "rule_v0",
            "model": "rule_v0",
            "json": None,
            "text": "",
            "fallback_used": True,
            "provider_attempts": 1,
            "errors": [
                {
                    "provider": "google",
                    "model": "gemini-test",
                    "status": "parse_failure",
                    "code": "schema_failure",
                }
            ],
        },
    )

    result = planner._plan_text_query_impl(
        "find US portrait lens creators",
        body={"use_product_persona": False},
    )

    assert result["status"] == "fallback"
    assert result["fallback_used"] is True
    assert result["reason"] == "planner_parse_failed"
    assert result["provider_calls_performed"] is True
    assert result["provider_response_succeeded"] is True
    assert result["provider_attempts"] == 1
    assert result["provider_response_status"] == "fallback_to_rule"
    assert result["planner_parse_status"] == "planner_parse_failed"
    assert result["planner_parse_failed"] is True


def test_planner_keeps_legacy_text_envelope_mock_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_product(monkeypatch)
    monkeypatch.setattr(
        planner.llm_gateway,
        "invoke_json",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "google",
            "model": "legacy-provider-mock",
            "text": json.dumps(_valid_plan_json()),
            "fallback_used": False,
        },
    )

    result = planner._plan_text_query_impl(
        "find US portrait lens creators",
        body={"use_product_persona": False},
    )

    assert result["status"] == "ready"
    assert result["provider"] == "google"
    assert result["model"] == "legacy-provider-mock"
    assert result["provider_calls_performed"] is True
    assert result["planner_parse_status"] == "success"
    assert result["planner_parse_failed"] is False


def test_planner_reports_gateway_cache_hit_as_zero_current_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_product(monkeypatch)
    monkeypatch.setattr(
        planner.llm_gateway,
        "invoke_json",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "google",
            "model": "gemini-test",
            "json": _valid_plan_json(),
            "fallback_used": False,
            "provider_attempts": 0,
            "cache_hit": True,
            "cache_key": "vkpi:llm:cached-plan",
            "cache_origin_call_uid": "call-origin-123",
            "errors": [],
        },
    )

    result = planner._plan_text_query_impl(
        "find US portrait lens creators",
        body={"use_product_persona": False},
    )

    assert result["status"] == "ready"
    assert result["provider_calls_performed"] is False
    assert result["provider_response_succeeded"] is False
    assert result["provider_attempts"] == 0
    assert result["provider_response_status"] == "gateway_cache_hit"
    assert result["planner_parse_status"] == "cached_valid"
    assert result["gateway_cache_hit"] is True
    assert result["gateway_cache_key"] == "vkpi:llm:cached-plan"
    assert result["gateway_cache_origin_call_uid"] == "call-origin-123"


def test_plan_cache_hit_overwrites_historical_execution_truth_but_keeps_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from app.domains.analysis import cache_repo

    cached = {
        **_valid_plan_json(),
        "status": "ready",
        "fallback_used": False,
        "provider_calls_performed": True,
        "provider_response_succeeded": True,
        "provider_attempts": 1,
        "provider_response_status": "success",
        "planner_parse_status": "success",
        "planner_parse_failed": False,
        "gateway_cache_hit": True,
        "gateway_cache_key": "vkpi:llm:origin",
        "gateway_cache_origin_call_uid": "call-origin-456",
    }
    monkeypatch.setattr(
        cache_repo,
        "get_analysis_cache_entry",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "result": json.dumps(cached),
        },
    )

    result = planner.plan_text_query("find US portrait lens creators")

    assert result["plan_cache"] == "hit"
    assert result["provider_calls_performed"] is False
    assert result["provider_response_succeeded"] is False
    assert result["provider_attempts"] == 0
    assert result["provider_response_status"] == "plan_cache_hit"
    assert result["planner_parse_status"] == "cached_valid"
    assert result["gateway_cache_hit"] is False
    origin = result["plan_cache_origin_diagnostics"]
    assert origin["provider_calls_performed"] is True
    assert origin["provider_response_succeeded"] is True
    assert origin["provider_attempts"] == 1
    assert origin["gateway_cache_hit"] is True
    assert origin["gateway_cache_key"] == "vkpi:llm:origin"
    assert origin["gateway_cache_origin_call_uid"] == "call-origin-456"


def test_planner_rejects_structured_but_business_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_product(monkeypatch)
    monkeypatch.setattr(
        planner.llm_gateway,
        "invoke_json",
        lambda *_args, **_kwargs: {
            "status": "success",
            "provider": "google",
            "model": "gemini-test",
            "json": {
                "search_query": "",
                "product_focus": [],
                "target_persona": "",
                "platforms": [],
            },
            "fallback_used": False,
            "provider_attempts": 1,
            "errors": [],
        },
    )

    result = planner._plan_text_query_impl(
        "find US portrait lens creators",
        body={"use_product_persona": False},
    )

    assert result["status"] == "fallback"
    assert result["reason"] == "planner_parse_failed"
    assert result["provider_calls_performed"] is True
    assert result["provider_response_succeeded"] is True
    assert result["planner_parse_status"] == "planner_parse_failed"
    assert result["planner_parse_failed"] is True
