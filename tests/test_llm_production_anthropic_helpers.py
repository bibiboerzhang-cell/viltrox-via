"""严格 Anthropic 边界的请求策略 / 拒答检查 / 输入估算契约(2026-08-22 模型升级刀)。

- anthropic_create_kwargs:精确模型 + max_tokens + 原 messages 对象 + thinking 策略
  (默认 disabled;env adaptive + effort → output_config);永不带 temperature/top_p/
  budget_tokens;
- anthropic_checked_response:stop_reason=refusal / 空正文异常停 → AnthropicRefusal
  (携带 usage);正常 / max_tokens 截断原样返回;
- anthropic_input_token_estimate:document 块(合同/发票 PDF 直传真实路径)不再 raise,
  按页数保守估;Claude 5 系文本除数 2.3(4.7+ tokenizer 多 ~30% token);图片统一 4784。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.platform.llm_production_anthropic_helpers import (  # noqa: E402
    AnthropicRefusal,
    anthropic_checked_response,
    anthropic_create_kwargs,
    anthropic_input_token_estimate,
)


def _clear_policy_env(monkeypatch) -> None:
    for name in ("VKPI_ANTHROPIC_THINKING", "VKPI_ANTHROPIC_EFFORT", "VKPI_ANTHROPIC_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def test_create_kwargs_default_disables_thinking_and_keeps_messages_identity(monkeypatch):
    _clear_policy_env(monkeypatch)
    messages = [{"role": "user", "content": "hi"}]
    kwargs = anthropic_create_kwargs("claude-sonnet-5", 600, messages)
    assert kwargs == {
        "model": "claude-sonnet-5",
        "max_tokens": 600,
        "messages": messages,
        "thinking": {"type": "disabled"},
    }
    assert kwargs["messages"] is messages  # 调用方 payload 原样透传(边界契约)
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens", "output_config"):
        assert forbidden not in kwargs


def test_create_kwargs_adaptive_env_adds_output_config_effort(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_THINKING", "adaptive")
    monkeypatch.setenv("VKPI_ANTHROPIC_EFFORT", "low")
    kwargs = anthropic_create_kwargs("claude-opus-5", 4000, [{"role": "user", "content": "x"}])
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "low"}
    assert "budget_tokens" not in kwargs["thinking"]


def test_create_kwargs_rejects_unknown_policy_values_to_disabled(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_THINKING", "enabled")
    monkeypatch.setenv("VKPI_ANTHROPIC_EFFORT", "bogus")
    kwargs = anthropic_create_kwargs("claude-sonnet-5", 100, [{"role": "user", "content": "x"}])
    assert kwargs["thinking"] == {"type": "disabled"}
    assert "output_config" not in kwargs


def _response(*, text: str | None, stop_reason: str):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=321, output_tokens=4),
        model="claude-sonnet-5",
    )


def test_checked_response_passes_normal_and_truncated_responses():
    ok = _response(text="{}", stop_reason="end_turn")
    assert anthropic_checked_response(ok) is ok
    truncated = _response(text='{"a":', stop_reason="max_tokens")
    assert anthropic_checked_response(truncated) is truncated  # 截断留给调用方处理
    legacy = SimpleNamespace(content=[SimpleNamespace(type="text", text="x")])  # 无 stop_reason 字段
    assert anthropic_checked_response(legacy) is legacy


def test_checked_response_raises_typed_refusal_with_usage():
    with pytest.raises(AnthropicRefusal) as info:
        anthropic_checked_response(_response(text=None, stop_reason="refusal"))
    exc = info.value
    assert exc.stop_reason == "refusal"
    assert exc.input_tokens == 321
    assert exc.output_tokens == 4
    assert exc.reason == "anthropic_refusal"
    assert "anthropic_refusal:refusal" in str(exc)


def test_checked_response_treats_empty_abnormal_stop_as_refusal():
    with pytest.raises(AnthropicRefusal) as info:
        anthropic_checked_response(_response(text="", stop_reason="max_tokens"))
    assert info.value.stop_reason == "max_tokens"
    # 空正文但自然结束不算拒答(调用方按空响应处理)
    empty_ok = _response(text="", stop_reason="end_turn")
    assert anthropic_checked_response(empty_ok) is empty_ok


def _fake_pdf(pages: int) -> str:
    body = b"%PDF-1.4\n" + b"".join(
        f"{i} 0 obj << /Type /Page /Parent 1 0 R >> endobj\n".encode() for i in range(2, pages + 2)
    ) + b"1 0 obj << /Type /Pages /Count 1 >> endobj\n%%EOF"
    return base64.b64encode(body).decode("ascii")


def test_estimate_accepts_document_blocks_from_contract_and_invoice_paths():
    """claude_contract_extract.py / contract_assist.py 真传 document 块;
    此前直接 raise unsupported_anthropic_block_type:document → 合同/发票提取在
    预留前就炸(真缺陷)。现按页数 3000 token/页 保守估,至少 3000。"""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": _fake_pdf(4)}},
                {"type": "text", "text": "extract"},
            ],
        }
    ]
    estimate = anthropic_input_token_estimate(messages, model="claude-sonnet-5")
    assert estimate >= 256 + 4 * 3000
    assert estimate < 256 + 5 * 3000 + 100

    one_page = [{"role": "user", "content": [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": _fake_pdf(1)}}]}]
    assert anthropic_input_token_estimate(one_page, model="claude-sonnet-5") >= 256 + 3000

    # 非 PDF 字节(解不出页数)→ 按 base64 体积兜底且不低于 floor
    blob = base64.b64encode(b"\x00" * 50_000).decode("ascii")
    opaque = [{"role": "user", "content": [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": blob}}]}]
    assert anthropic_input_token_estimate(opaque, model="claude-sonnet-5") >= 256 + 3000

    bad = [{"role": "user", "content": [{"type": "document", "source": {"type": "ftp"}}]}]
    with pytest.raises(ValueError, match="unsupported_anthropic_document_source"):
        anthropic_input_token_estimate(bad, model="claude-sonnet-5")


def test_estimate_image_tokens_no_longer_special_cases_sonnet_4_6():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "YWJj"}},
            ],
        }
    ]
    for model in ("claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5"):
        assert anthropic_input_token_estimate(messages, model=model) == 256 + 4784, model


def test_estimate_text_divisor_is_lower_for_claude_5_family():
    text = "x" * 3000
    messages = [{"role": "user", "content": text}]
    legacy = anthropic_input_token_estimate(messages, model="claude-sonnet-4-6")
    haiku = anthropic_input_token_estimate(messages, model="claude-haiku-4-5")
    sonnet5 = anthropic_input_token_estimate(messages, model="claude-sonnet-5")
    opus5 = anthropic_input_token_estimate(messages, model="claude-opus-5")
    assert legacy == haiku == 256 + 1000
    assert sonnet5 == opus5 == 256 + int(3000 / 2.3)
    assert (sonnet5 - 256) > (legacy - 256) * 1.25  # 文本部分 ~30% 更多 token 的预留口径


def test_estimate_still_rejects_unknown_block_types():
    messages = [{"role": "user", "content": [{"type": "tool_result", "content": "x"}]}]
    with pytest.raises(ValueError, match="unsupported_anthropic_block_type:tool_result"):
        anthropic_input_token_estimate(messages, model="claude-sonnet-5")


def test_strict_boundary_settles_refusal_as_provider_exception(monkeypatch):
    """generate_anthropic_messages:SDK 返回 stop_reason=refusal → AnthropicRefusal
    走既有 except 分支(台账 provider_exception、预留 unknown、breaker 记异常),
    不再当成功空响应结算;请求体带 thinking disabled。"""
    from app.platform import llm_production

    _clear_policy_env(monkeypatch)
    messages = [{"role": "user", "content": [{"type": "text", "text": "Inspect."}]}]
    provider_kwargs: dict = {}

    class Messages:
        @staticmethod
        def create(**kwargs):
            provider_kwargs.update(kwargs)
            return SimpleNamespace(
                model="claude-sonnet-5",
                stop_reason="refusal",
                usage=SimpleNamespace(input_tokens=900, output_tokens=0),
                content=[],
            )

    class Reservations:
        def __init__(self):
            self.events = []

        def reserve_llm_budget(self, **kwargs):
            self.events.append("reserve")
            return SimpleNamespace(reservation_key="llmres-refusal")

        def mark_llm_provider_started(self, key):
            self.events.append("started")

        def release_llm_reservation(self, _key):
            raise AssertionError("started reservation must not be released")

        def settle_llm_reservation(self, key, actual_cost):
            raise AssertionError("a refusal must not settle as success")

        def mark_llm_provider_unknown(self, key):
            self.events.append("unknown")
            return True

    reservations = Reservations()
    call_rows: list[dict] = []
    breaker_events: list = []
    monkeypatch.setattr(
        llm_production,
        "current_task_model_binding",
        lambda: {"audit_vision_fallback": "anthropic/claude-sonnet-5"},
    )
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "budget_preflight",
        lambda *_a, **_k: {
            "provider_gate_reason": "provider_calls_allowed",
            "providers": [{"binding": "anthropic/claude-sonnet-5", "provider_calls_allowed": True}],
        },
    )
    monkeypatch.setattr(llm_production.llm_gateway, "_llm_budget_reservations", lambda: reservations)
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "record_call",
        lambda **kwargs: call_rows.append(kwargs) or {"call": {"call_uid": "unit"}},
    )
    monkeypatch.setattr(llm_production.llm_gateway, "_acquire_strict_fleet_breaker", lambda **_k: object())
    monkeypatch.setattr(
        llm_production.llm_gateway,
        "_complete_strict_fleet_breaker",
        lambda guard, outcome: breaker_events.append(outcome),
    )

    with pytest.raises(AnthropicRefusal) as info:
        llm_production.generate_anthropic_messages(
            client=SimpleNamespace(messages=Messages()),
            messages=messages,
            model="claude-sonnet-5",
            purpose="audit_vision_fallback",
            max_output_tokens=600,
            cost_tag="cron:audit_vision_fallback",
            metadata={"task_binding": "audit_vision_fallback"},
        )

    assert info.value.input_tokens == 900
    assert provider_kwargs["thinking"] == {"type": "disabled"}
    assert provider_kwargs["messages"] is messages
    assert "temperature" not in provider_kwargs
    assert reservations.events == ["reserve", "started", "unknown"]
    assert call_rows[-1]["status"] == "provider_exception"
    assert isinstance(breaker_events[-1], AnthropicRefusal)
