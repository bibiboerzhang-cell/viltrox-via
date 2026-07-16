from __future__ import annotations

import inspect
import json
from typing import Any

import pytest

from app.domains.channels import official_daily_report
from app.domains.dashboard import report_analysis
from app.domains.market import ai_today


OFFICIAL_PAYLOAD = {
    "play_performance": "Views increased based on the supplied rows.",
    "comment_insights": "No unsupported conclusion.",
    "visual_quality": "pending",
    "data_trend": "stable",
    "suggestions": ["Review the supplied weak post."],
    "headline": "Evidence-bounded daily report",
}

REPORT_PAYLOAD = {
    "executive_summary": "Evidence-bounded summary.",
    "highlights": ["12 observed rows"],
    "risks": ["GMV remains pending"],
    "recommendations": ["Connect the missing source"],
    "market_insights": [],
}


@pytest.mark.parametrize(
    ("module", "payload", "purpose", "scope", "tokens", "surface"),
    [
        (
            official_daily_report,
            OFFICIAL_PAYLOAD,
            "channels.official_daily_report",
            "cron:official_daily_report",
            3500,
            "official_daily_report",
        ),
        (
            report_analysis,
            REPORT_PAYLOAD,
            "dashboard.report_analysis",
            "dashboard:report_analysis",
            2500,
            "dashboard_report_analysis",
        ),
    ],
)
def test_primary_generation_uses_one_exact_atomic_production_call(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    payload: dict[str, Any],
    purpose: str,
    scope: str,
    tokens: int,
    surface: str,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"prompt": prompt, **kwargs})
        assert kwargs["validator"](payload) is True
        return {
            "status": "success",
            "provider": "anthropic",
            "model": module.CLAUDE_MODEL,
            "json": payload,
        }

    monkeypatch.setattr(module.llm_production, "generate_json", fake_generate_json)

    raw, model = module._generate("only supplied evidence")

    assert json.loads(raw) == payload
    assert model == f"claude:{module.CLAUDE_MODEL}"
    assert len(calls) == 1
    call = calls[0]
    assert call["provider"] == "anthropic"
    assert call["model"] == module.CLAUDE_MODEL
    assert call["purpose"] == purpose
    assert call["cost_tag"] == scope
    assert call["max_output_tokens"] == tokens
    assert call["metadata"] == {
        "surface": surface,
        "model_stage": "primary",
        "explicit_cross_model_fallback": False,
    }


@pytest.mark.parametrize(
    ("module", "payload"),
    [
        (official_daily_report, OFFICIAL_PAYLOAD),
        (report_analysis, REPORT_PAYLOAD),
    ],
)
def test_cross_model_fallback_is_explicit_and_each_attempt_is_exact(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    payload: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_generate_json(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"prompt": prompt, **kwargs})
        if kwargs["provider"] == "anthropic":
            return {
                "status": "fallback",
                "provider": "rule_v0",
                "model": "rule_v0",
                "json": None,
                "reason": "model_binding_blocked",
            }
        return {
            "status": "success",
            "provider": "google",
            "model": module.GEMINI_MODEL,
            "json": payload,
        }

    monkeypatch.setattr(module.llm_production, "generate_json", fake_generate_json)

    raw, model = module._generate("only supplied evidence")

    assert json.loads(raw) == payload
    assert model == f"gemini:{module.GEMINI_MODEL}"
    assert [(call["provider"], call["model"]) for call in calls] == [
        ("anthropic", module.CLAUDE_MODEL),
        ("google", module.GEMINI_MODEL),
    ]
    assert calls[0]["metadata"]["explicit_cross_model_fallback"] is False
    assert calls[1]["metadata"]["explicit_cross_model_fallback"] is True
    assert calls[1]["metadata"]["model_stage"] == "explicit_fallback"


@pytest.mark.parametrize("module", [official_daily_report, report_analysis])
def test_ai_off_or_unverified_models_fail_closed_without_static_analysis(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
) -> None:
    calls: list[dict[str, Any]] = []

    def blocked(prompt: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"prompt": prompt, **kwargs})
        return {
            "status": "fallback",
            "provider": "rule_v0",
            "model": "rule_v0",
            "json": None,
            "reason": "model_binding_blocked",
        }

    monkeypatch.setattr(module.llm_production, "generate_json", blocked)

    assert module._generate("only supplied evidence") == ("", "")
    assert [call["provider"] for call in calls] == ["anthropic", "google"]


def test_plain_json_migrations_have_no_direct_provider_sdk_or_double_cost() -> None:
    for module in (official_daily_report, report_analysis):
        source = inspect.getsource(module)
        assert ".messages.create(" not in source
        assert ".models.generate_content(" not in source
        assert "llm_production.generate_json(" in source
        assert "budget_guard.record_cost(" not in source


def test_ai_today_grounding_debt_requires_tool_and_citation_contract() -> None:
    source = inspect.getsource(ai_today._generate)
    assert "strict grounded-JSON boundary" in source
    assert "candidate-level citation metadata" in source
    assert source.count(".models.generate_content(") == 1
    assert source.count(".messages.create(") == 1
    assert "llm_production.generate_json(" not in source
