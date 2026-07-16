from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_strict_progress_metadata_defaults_are_complete_and_preserve_caller_values():
    from app.platform.llm_production import _progress_metadata

    inferred = _progress_metadata(
        "marketing_advisor",
        {"surface": "advisor"},
        phase="structured_generation",
    )
    assert inferred == {
        "surface": "advisor",
        "task_binding": "via_chat",
        "phase": "structured_generation",
        "subphase": "provider_generation",
        "attempt_index": 1,
        "attempt_total": 1,
        "target_label": "marketing_advisor",
    }

    explicit = _progress_metadata(
        "custom_analysis",
        {
            "task_binding": "reviewed_binding",
            "phase": "evaluation",
            "subphase": "qa",
            "attempt_index": 2,
            "total": 4,
            "target_label": "creator:42",
        },
        phase="structured_generation",
    )
    assert explicit["task_binding"] == "reviewed_binding"
    assert explicit["phase"] == "evaluation"
    assert explicit["subphase"] == "qa"
    assert explicit["attempt_index"] == 2
    assert explicit["attempt_total"] == 4
    assert explicit["target_label"] == "creator:42"


def test_generate_json_adds_progress_without_relaxing_strict_gates(monkeypatch):
    from app.platform import llm_production

    captured = {}

    def fake_invoke_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {"status": "blocked", "json": None}

    monkeypatch.setattr(llm_production.llm_gateway, "invoke_json", fake_invoke_json)
    result = llm_production.generate_json(
        "prompt",
        provider="anthropic",
        model="claude-opus-4-7",
        purpose="deepsight_strategy",
    )

    assert result["status"] == "blocked"
    assert captured["require_runtime_verified"] is True
    assert captured["require_configured_budget"] is True
    assert captured["enforce_atomic_reservation"] is True
    assert captured["max_provider_attempts"] == 1
    assert captured["model_fallbacks"] == ()
    assert captured["metadata"]["phase"] == "structured_generation"
    assert captured["metadata"]["subphase"] == "provider_generation"
    assert captured["metadata"]["attempt_index"] == 1
    assert captured["metadata"]["attempt_total"] == 1
    assert captured["metadata"]["target_label"] == "deepsight_strategy"


def test_via_summary_keeps_its_reviewed_task_binding(monkeypatch):
    from app.services.via import model_router

    captured = {}

    def fake_generate_json(_prompt, **kwargs):
        captured.update(kwargs)
        return {
            "status": "success",
            "provider": "anthropic",
            "model": kwargs["model"],
            "json": {"summary": "bounded", "keywords": []},
        }

    monkeypatch.setattr(
        model_router.llm_production,
        "generate_json",
        fake_generate_json,
    )
    result = asyncio.run(
        model_router.generate_json_with_route(
            purpose="summary",
            system_prompt="summarize",
            payload={"text": "hello"},
            route_override={
                "provider": "claude",
                "model": "claude-haiku-4-5-20251001",
            },
        )
    )

    assert result and result["data"]["summary"] == "bounded"
    assert captured["metadata"]["task_binding"] == "via_persona_summary"
    assert captured["metadata"]["phase"] == "structured_generation"
    assert captured["metadata"]["subphase"] == "provider_generation"


def test_deepsight_triad_uses_three_exact_strict_bindings(monkeypatch):
    from app.services.deepsight import triad

    bindings = {
        "deepsight_strategy": "anthropic/claude-opus-4-7",
        "deepsight_market_empath": "openai/gpt-5.5",
        "deepsight_opportunity": "google/gemini-2.5-pro",
    }
    calls = []

    monkeypatch.setattr(triad, "current_task_model_binding", lambda: bindings)

    def fake_generate_json(_prompt, **kwargs):
        calls.append(kwargs)
        task = kwargs["metadata"]["task_binding"]
        payloads = {
            "deepsight_strategy": {
                "summary": "structure",
                "risks": [],
                "platform_notes": [],
            },
            "deepsight_market_empath": {
                "summary": "empathy",
                "brand_mood": "stable",
                "positive_keywords": [],
                "negative_keywords": [],
                "purchase_keywords": [],
            },
            "deepsight_opportunity": {
                "summary": "growth",
                "opportunities": [],
            },
        }
        return {"status": "success", "json": payloads[task]}

    monkeypatch.setattr(triad.llm_production, "generate_json", fake_generate_json)
    result = asyncio.run(
        triad.run_triad(
            {
                "risk_flags": [],
                "platform_breakdown": [],
                "comment_analysis": {},
                "opportunities": [],
                "evidence_confidence": {"confidence_score": 0.8},
            }
        )
    )

    assert result["split_vote"] is False
    assert {call["metadata"]["task_binding"] for call in calls} == set(bindings)
    assert {
        (call["provider"], call["model"])
        for call in calls
    } == {
        ("anthropic", "claude-opus-4-7"),
        ("openai", "gpt-5.5"),
        ("google", "gemini-2.5-pro"),
    }
    for call in calls:
        assert call["cost_tag"] == "cron:deepsight_triad"
        assert call["metadata"]["phase"] == "structured_generation"
        assert call["metadata"]["subphase"] == "provider_generation"
        assert call["metadata"]["attempt_index"] == 1
        assert call["metadata"]["attempt_total"] == 1


def test_deepsight_triad_keeps_ai_off_rule_fallback(monkeypatch):
    from app.services.deepsight import triad

    monkeypatch.setattr(
        triad.llm_production,
        "generate_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("blocked")),
    )
    result = asyncio.run(
        triad.run_triad(
            {
                "risk_flags": ["missing_evidence"],
                "platform_breakdown": [],
                "comment_analysis": {"negative_ratio": 0.3},
                "opportunities": ["collect_actuals"],
                "evidence_confidence": {"confidence_score": 0.2},
            }
        )
    )

    assert result["claude"]["summary"] == "基于规则层的结构诊断"
    assert result["gpt"]["summary"] == "基于评论层的情绪诊断"
    assert result["gemini"]["summary"] == "基于机会层的增长诊断"
    assert result["split_vote"] is True


def test_audit_deep_score_preserves_retry_and_parsing_with_strict_attempts(
    monkeypatch,
):
    from app.services.ai import retry
    from app.services.ai.analyzers import claude_text

    class NoDirectMessages:
        class messages:
            @staticmethod
            def create(**_kwargs):  # pragma: no cover - regression tripwire
                raise AssertionError("direct Anthropic SDK call must not run")

    monkeypatch.setattr(claude_text, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(claude_text, "get_claude_client", lambda: NoDirectMessages())
    monkeypatch.setattr(retry.time, "sleep", lambda _seconds: None)
    calls = []

    def fake_generate_text(_prompt, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TimeoutError("transient")
        return {
            "status": "success",
            "provider": "anthropic",
            "model": kwargs["model"],
            "text": '{"content_genre":"review","quality_scores":{"exposure":7}}',
        }

    monkeypatch.setattr(
        claude_text.llm_production,
        "generate_text",
        fake_generate_text,
    )

    result = claude_text.analyze_text_content(
        "Lens review",
        "A detailed Viltrox lens review for creators.",
        "https://example.com/video/42",
        "youtube",
        "Independent field test.",
    )

    assert result["analyzed"] is True
    assert result["content_genre"] == "review"
    assert [call["metadata"]["attempt_index"] for call in calls] == [1, 2]
    assert all(call["metadata"]["attempt_total"] == 3 for call in calls)
    assert all(
        call["metadata"]["task_binding"] == "audit_deep_score"
        for call in calls
    )
    assert all(call["provider"] == "anthropic" for call in calls)
    assert all(call["model"] == claude_text.CLAUDE_MODEL for call in calls)
    assert all(call["cost_tag"] == "cron:audit_deep_score" for call in calls)


def test_anthropic_multimodal_boundary_preserves_payload_and_settles(monkeypatch):
    from app.platform import llm_production

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "YWJj",
                    },
                },
                {"type": "text", "text": "Inspect this frame."},
            ],
        }
    ]
    provider_kwargs = {}

    class Messages:
        @staticmethod
        def create(**kwargs):
            provider_kwargs.update(kwargs)
            return SimpleNamespace(
                model="claude-sonnet-4-6",
                usage=SimpleNamespace(input_tokens=1800, output_tokens=120),
                content=[SimpleNamespace(type="text", text="{}")],
            )

    class Reservations:
        def __init__(self):
            self.reserved = []
            self.started = []
            self.settled = []

        def reserve_llm_budget(self, **kwargs):
            self.reserved.append(kwargs)
            return SimpleNamespace(reservation_key="llmres-unit")

        def mark_llm_provider_started(self, key):
            self.started.append(key)

        def release_llm_reservation(self, _key):
            raise AssertionError("started reservation must not be released")

        def settle_llm_reservation(self, key, actual_cost):
            self.settled.append((key, actual_cost))
            return {"settled": True}

    reservations = Reservations()
    call_rows = []
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_args, **_kwargs: {
            "provider_gate_reason": "provider_calls_allowed",
            "providers": [
                {
                    "binding": "anthropic/claude-sonnet-4-6",
                    "provider_calls_allowed": True,
                }
            ],
        },
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_llm_budget_reservations",
        lambda: reservations,
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {"call_uid": "unit"}},
    )

    response = llm_production.generate_anthropic_messages(
        client=SimpleNamespace(messages=Messages()),
        messages=messages,
        model="claude-sonnet-4-6",
        purpose="audit_vision_fallback",
        max_output_tokens=2000,
        cost_tag="cron:audit_vision_fallback",
        metadata={
            "task_binding": "audit_vision_fallback",
            "attempt_index": 2,
            "attempt_total": 3,
        },
    )

    assert response.model == "claude-sonnet-4-6"
    assert provider_kwargs == {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2000,
        "messages": messages,
    }
    assert provider_kwargs["messages"] is messages
    assert reservations.started == ["llmres-unit"]
    assert reservations.settled[0][0] == "llmres-unit"
    assert reservations.settled[0][1] > 0
    assert reservations.reserved[0]["estimated_cost_usd"] > 0
    assert reservations.reserved[0]["metadata"]["attempt_index"] == 2
    assert reservations.reserved[0]["metadata"]["attempt_total"] == 3
    assert call_rows[-1]["status"] == "success"
    assert call_rows[-1]["metadata"]["task_binding"] == "audit_vision_fallback"
    assert call_rows[-1]["update_budget_scopes"] is False


def test_claude_image_batch_routes_through_reviewed_vision_binding(monkeypatch):
    from app.services.ai.analyzers import claude_vision_images

    captured = []
    monkeypatch.setattr(claude_vision_images, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(claude_vision_images, "ANTHROPIC_API_KEY", "configured")
    monkeypatch.setattr(
        claude_vision_images,
        "_build_anthropic_client",
        lambda: SimpleNamespace(messages=SimpleNamespace()),
    )

    def fake_messages(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text='{"viltrox_detected":false,"confidence":"none"}'
                )
            ]
        )

    monkeypatch.setattr(
        claude_vision_images.llm_production,
        "generate_anthropic_messages",
        fake_messages,
    )

    result = claude_vision_images._analyze_images_batch(
        ["YWJj"],
        "Field test",
        "instagram",
    )

    assert result["viltrox_detected"] is False
    assert captured[0]["purpose"] == "audit_vision_fallback"
    assert captured[0]["metadata"]["task_binding"] == "audit_vision_fallback"
    assert captured[0]["metadata"]["attempt_index"] == 1
    assert captured[0]["metadata"]["attempt_total"] == 3
