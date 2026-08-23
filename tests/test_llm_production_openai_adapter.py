"""严格 OpenAI Responses(文本+图)适配器 + C6 拆分门面契约(2026-08-23 优化波 B·A 车道)。

- generate_openai_responses:任务绑定必须精确命中(keyframe_openai_judge → openai/gpt-5.5),
  走预算预留 → provider → 台账 → 结算;gpt-5 系请求不带 temperature;
- 门面:``llm_production.generate_*`` 由 provider 子模块 re-export,打在门面上的
  ``current_task_model_binding`` 补丁对 provider 子模块生效(expected_task_binding 经门面解析);
- 关键帧 OpenAI 裁判:client 取代理感知的 services.ai.clients.openai_client,不再裸建
  OpenAI(api_key=...)。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.platform import llm_production
from app.platform.llm_production_common import ProductionLlmUnavailable
from app.platform.llm_production_openai import (
    OPENAI_IMAGE_TOKENS,
    openai_input_token_estimate,
    openai_response_text,
    openai_responses_create_kwargs,
)


def _input_items(image_count: int = 1) -> list[dict]:
    content = [{"type": "input_text", "text": "judge these frames"}]
    for _ in range(image_count):
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA", "detail": "high"})
    return [{"role": "user", "content": content}]


def test_openai_input_estimate_counts_images_at_tile_cap_and_rejects_unknown_parts():
    estimate = openai_input_token_estimate(_input_items(3))
    assert estimate >= 3 * OPENAI_IMAGE_TOKENS
    with pytest.raises(ValueError, match="unsupported_openai_part_type:input_file"):
        openai_input_token_estimate([{"role": "user", "content": [{"type": "input_file"}]}])


def test_openai_create_kwargs_never_send_temperature_and_keep_effort_table(monkeypatch):
    monkeypatch.delenv("VKPI_OPENAI_REASONING_EFFORT_JSON", raising=False)
    items = _input_items()
    kwargs = openai_responses_create_kwargs("gpt-5.5", 4000, items)
    assert kwargs == {"model": "gpt-5.5", "input": items, "max_output_tokens": 4000}
    assert kwargs["input"] is items
    luna = openai_responses_create_kwargs("gpt-5.6-luna", 100, items)
    assert luna["reasoning"] == {"effort": "none"}
    for forbidden in ("temperature", "top_p"):
        assert forbidden not in kwargs and forbidden not in luna


def test_openai_response_text_prefers_output_text_then_walks_output():
    assert openai_response_text(SimpleNamespace(output_text=" {\"a\":1} ")) == '{"a":1}'
    walked = SimpleNamespace(
        output_text="",
        output=[SimpleNamespace(content=[SimpleNamespace(type="output_text", text="x"), SimpleNamespace(type="reasoning", text="no")])],
    )
    assert openai_response_text(walked) == "x"


def test_strict_openai_boundary_rejects_binding_mismatch_before_any_io(monkeypatch):
    monkeypatch.setattr(llm_production, "current_task_model_binding", lambda: {"keyframe_openai_judge": "openai/gpt-5.5"})
    monkeypatch.setattr(llm_production.llm_gateway, "budget_preflight", lambda *_a, **_k: pytest.fail("no preflight on mismatch"))

    with pytest.raises(ProductionLlmUnavailable) as info:
        llm_production.generate_openai_responses(
            client=SimpleNamespace(responses=SimpleNamespace(create=lambda **_k: pytest.fail("no provider I/O"))),
            input_items=_input_items(),
            model="gpt-5.4",
            purpose="keyframe_openai_judge",
            max_output_tokens=400,
            metadata={"task_binding": "keyframe_openai_judge"},
        )
    assert info.value.code == "task_binding_model_mismatch"
    assert info.value.result["expected_binding"] == "openai/gpt-5.5"

    with pytest.raises(ProductionLlmUnavailable) as missing:
        llm_production.generate_openai_responses(
            client=SimpleNamespace(),
            input_items=_input_items(),
            model="gpt-5.5",
            purpose="keyframe_openai_judge",
            max_output_tokens=400,
            metadata={"task_binding": ""},
        )
    assert missing.value.code == "task_binding_model_mismatch"


def test_strict_openai_boundary_reserves_calls_ledgers_and_settles(monkeypatch):
    """门面补丁 current_task_model_binding 经 expected_task_binding 透到 provider 子模块。"""
    monkeypatch.delenv("VKPI_OPENAI_REASONING_EFFORT_JSON", raising=False)
    monkeypatch.setattr(llm_production, "current_task_model_binding", lambda: {"keyframe_openai_judge": "openai/gpt-5.5"})
    provider_kwargs: dict = {}
    events: list = []
    call_rows: list[dict] = []

    class Responses:
        @staticmethod
        def create(**kwargs):
            provider_kwargs.update(kwargs)
            events.append("provider")
            return SimpleNamespace(
                model="gpt-5.5",
                output_text='{"layer2": {}}',
                usage=SimpleNamespace(input_tokens=1800, output_tokens=120),
            )

    class Reservations:
        def reserve_llm_budget(self, **kwargs):
            events.append(("reserve", kwargs["provider"], kwargs["model"], kwargs["cost_scope"]))
            assert kwargs["estimated_cost_usd"] > 0
            assert kwargs["metadata"]["estimated_input_tokens"] >= OPENAI_IMAGE_TOKENS
            return SimpleNamespace(reservation_key="llmres-openai")

        def mark_llm_provider_started(self, key):
            events.append("started")

        def release_llm_reservation(self, _key):
            raise AssertionError("started reservation must not be released")

        def settle_llm_reservation(self, key, actual_cost):
            events.append(("settled", key, actual_cost > 0))
            return {"settled": True}

        def mark_llm_provider_unknown(self, key):
            raise AssertionError("success path must not mark unknown")

    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_a, **_k: {"providers": [{"binding": "openai/gpt-5.5", "provider_calls_allowed": True}]},
    )
    monkeypatch.setattr(llm_production.llm_gateway, "_llm_budget_reservations", lambda: Reservations())
    monkeypatch.setattr(llm_production.llm_gateway, "record_call", lambda **kwargs: call_rows.append(kwargs) or {"call": {}})
    monkeypatch.setattr(llm_production.llm_gateway, "_acquire_strict_fleet_breaker", lambda **_k: object())
    monkeypatch.setattr(llm_production.llm_gateway, "_complete_strict_fleet_breaker", lambda guard, outcome: events.append(("breaker", outcome)))
    monkeypatch.setattr(llm_production.llm_gateway, "_mark_reserved_attempt_unknown", lambda key: pytest.fail("must not mark unknown"))

    items = _input_items(2)
    response = llm_production.generate_openai_responses(
        client=SimpleNamespace(responses=Responses()),
        input_items=items,
        model="gpt-5.5",
        purpose="keyframe_openai_judge",
        max_output_tokens=4000,
        cost_tag="cron:vkpi_analysis_worker",
        metadata={"task_binding": "keyframe_openai_judge", "surface": "apify_jobs_worker"},
    )

    assert response.output_text == '{"layer2": {}}'
    assert provider_kwargs["input"] is items
    assert provider_kwargs["model"] == "gpt-5.5" and provider_kwargs["max_output_tokens"] == 4000
    assert "temperature" not in provider_kwargs
    assert events[0] == ("reserve", "openai", "gpt-5.5", "cron:vkpi_analysis_worker")
    assert events[1] == "started" and events[2] == "provider"
    assert ("breaker", {"status": "success"}) in events
    assert events[-1][0] == "settled" and events[-1][2] is True
    assert len(call_rows) == 1
    row = call_rows[0]
    assert row["status"] == "success" and row["input_tokens"] == 1800 and row["output_tokens"] == 120
    assert row["metadata"]["entrypoint"] == "llm_production_openai_responses_v1"
    assert row["metadata"]["task_binding"] == "keyframe_openai_judge"
    assert row["metadata"]["request_content_recorded"] is False


def test_facade_reexports_provider_adapters_and_keeps_public_symbols():
    from app.platform import llm_production_anthropic, llm_production_google, llm_production_openai

    assert llm_production.generate_anthropic_messages is llm_production_anthropic.generate_anthropic_messages
    assert llm_production.generate_google_content is llm_production_google.generate_google_content
    assert llm_production.generate_openai_responses is llm_production_openai.generate_openai_responses
    for name in ("generate_text", "generate_json", "ProductionLlmUnavailable", "llm_gateway", "current_task_model_binding"):
        assert hasattr(llm_production, name), name
    assert llm_production.GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP == llm_production_google.GOOGLE_GENERATE_MAX_OUTPUT_TOKENS_HARD_CAP


def test_keyframe_openai_judge_uses_proxy_aware_client_and_strict_boundary(monkeypatch):
    from app.services.ai.analyzers import gemini_video_keyframes as kf
    from app.services.ai.clients import openai_client as openai_module

    captured: dict = {}
    sentinel_client = SimpleNamespace(responses=SimpleNamespace())
    monkeypatch.setattr(openai_module, "OPENAI_AVAILABLE", True)
    monkeypatch.setattr(openai_module, "openai_client", sentinel_client)
    monkeypatch.setattr(kf, "build_openai_multimodal_content", lambda text, frames: [{"type": "input_text", "text": text}])

    def fake_generate_openai_responses(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model="gpt-5.5", output_text='{"layer2": {}, "layer3": {}}', usage=None)

    monkeypatch.setattr(kf.llm_production, "generate_openai_responses", fake_generate_openai_responses)

    result = asyncio.run(
        kf.analyze_v2_judgment_with_openai_keyframes(
            layer1_visual_content={},
            keyframes=[],
            title="demo",
            llm_context={"cost_tag": "cron:vkpi_analysis_worker", "triggered_by": 40, "metadata": {"parent_job_id": 7}},
        )
    )

    assert captured["client"] is sentinel_client
    assert captured["model"] == "gpt-5.5"
    assert captured["purpose"] == "keyframe_openai_judge"
    assert captured["cost_tag"] == "cron:vkpi_analysis_worker" and captured["triggered_by"] == 40
    assert captured["metadata"]["task_binding"] == "keyframe_openai_judge"
    assert captured["metadata"]["parent_job_id"] == 7
    assert captured["max_output_tokens"] == 4000
    assert result["method"] == "openai_keyframe_judgment_gpt-5.5"

    monkeypatch.setattr(openai_module, "OPENAI_AVAILABLE", False)
    blocked = asyncio.run(kf.analyze_v2_judgment_with_openai_keyframes(layer1_visual_content={}, keyframes=[], title="demo"))
    assert blocked["analyzed"] is False and blocked["error"] == "OpenAI not available"


def test_c3_task_bindings_are_registered_exact_models():
    from app.core import model_registry

    expected = {
        "lens_monitor": "anthropic/claude-sonnet-5",
        "lens_compare": "anthropic/claude-sonnet-5",
        "local_file_video": "google/gemini-3.6-flash",
        "audience_avatar": "google/gemini-3.6-flash",
        "keyframe_qa": "google/gemini-3.5-flash-lite",
        "keyframe_claude_judge": f"anthropic/{model_registry.CLAUDE_OPUS_EXACT_MODEL}",
        "keyframe_openai_judge": "openai/gpt-5.5",
    }
    for task, binding in expected.items():
        assert model_registry.TASK_MODEL_BINDING[task] == binding, task
        assert model_registry.validate_task_model(task, binding), task
    assert model_registry.TASK_MODEL_ENV_KEYS["keyframe_qa"] == ("GEMINI_FINAL_V1_QA_MODEL", None)
