"""LLM 网关 Anthropic 传输层契约(Sonnet 5 / Opus 5,2026-08-22 模型升级刀)。

- 请求体默认带 thinking={'type':'disabled'}(成本中性,沿用今日行为);
  永不发 temperature / top_p / top_k / budget_tokens(Claude 5 系一律 400);
- env VKPI_ANTHROPIC_THINKING=adaptive(+VKPI_ANTHROPIC_EFFORT)切自适应思考并
  带 output_config.effort,VKPI_ANTHROPIC_MAX_TOKENS 抬高 4000 上限;
- stop_reason=refusal(HTTP 200、正文空)→ provider_error(usage 保留入账),
  不再被记成「成功的空响应」,候选链继续向下一个 provider/模型推进;
- stop_reason=max_tokens → 结果标 truncated=True 给调用方。

零网络零真 key(_request_json / _get_api_key 一并 patch);不触真库。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.platform.llm_gateway  # noqa: F401,E402 — providers 与主模块互为底部循环 import
from app.platform import llm_gateway_providers as providers  # noqa: E402


def _anthropic_body(
    *,
    text: str = "ok",
    stop_reason: str = "end_turn",
    model: str = "claude-sonnet-5",
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    return {
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 120, "output_tokens": 7},
    }


def _capture(monkeypatch, response: dict[str, Any], max_output_tokens: int = 800) -> tuple[dict, dict]:
    captured: dict[str, Any] = {}

    def fake_request_json(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        captured["headers"] = headers
        return response

    monkeypatch.setattr(providers, "_request_json", fake_request_json)
    monkeypatch.setattr(providers, "_get_api_key", lambda provider: "test-key")
    result = providers._call_anthropic(
        "hello", max_output_tokens, model_override="claude-sonnet-5"
    )
    return captured, result


def _clear_policy_env(monkeypatch) -> None:
    for name in ("VKPI_ANTHROPIC_THINKING", "VKPI_ANTHROPIC_EFFORT", "VKPI_ANTHROPIC_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)


def test_default_body_disables_thinking_and_sends_no_sampling_params(monkeypatch):
    _clear_policy_env(monkeypatch)
    captured, result = _capture(monkeypatch, _anthropic_body())
    payload = captured["payload"]
    assert payload["model"] == "claude-sonnet-5"
    assert payload["max_tokens"] == 800
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["thinking"] == {"type": "disabled"}
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens", "output_config"):
        assert forbidden not in payload, forbidden
    assert "budget_tokens" not in payload["thinking"]
    assert set(payload.keys()) == {"model", "max_tokens", "messages", "thinking"}
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert result["status"] == "success"
    assert result["text"] == "ok"
    assert result["stop_reason"] == "end_turn"
    assert "truncated" not in result


def test_default_cap_stays_4000_when_thinking_disabled(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_MAX_TOKENS", "16000")  # 只在 adaptive 生效
    captured, _ = _capture(monkeypatch, _anthropic_body(), max_output_tokens=99_999)
    assert captured["payload"]["max_tokens"] == 4000
    assert captured["payload"]["thinking"] == {"type": "disabled"}


def test_adaptive_env_switches_thinking_effort_and_lifts_cap(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_THINKING", "adaptive")
    monkeypatch.setenv("VKPI_ANTHROPIC_EFFORT", "medium")
    monkeypatch.setenv("VKPI_ANTHROPIC_MAX_TOKENS", "16000")
    captured, result = _capture(monkeypatch, _anthropic_body(), max_output_tokens=12_000)
    payload = captured["payload"]
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "medium"}
    assert payload["max_tokens"] == 12_000
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert forbidden not in payload, forbidden
    assert result["status"] == "success"


def test_adaptive_without_effort_sends_no_output_config(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_THINKING", "adaptive")
    captured, _ = _capture(monkeypatch, _anthropic_body(), max_output_tokens=9_000)
    assert captured["payload"]["thinking"] == {"type": "adaptive"}
    assert "output_config" not in captured["payload"]
    assert captured["payload"]["max_tokens"] == 4000  # 未配 MAX_TOKENS → 仍 4000


def test_unknown_policy_values_fall_back_to_disabled(monkeypatch):
    _clear_policy_env(monkeypatch)
    monkeypatch.setenv("VKPI_ANTHROPIC_THINKING", "enabled")  # 旧 API 形态,不是合法档
    monkeypatch.setenv("VKPI_ANTHROPIC_EFFORT", "ultra")
    captured, _ = _capture(monkeypatch, _anthropic_body())
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert "output_config" not in captured["payload"]


def test_refusal_maps_to_provider_error_and_keeps_usage(monkeypatch):
    _clear_policy_env(monkeypatch)
    _, result = _capture(monkeypatch, _anthropic_body(text="", stop_reason="refusal"))
    assert result["status"] == "provider_error"
    assert result["error"] == "anthropic_refusal"
    assert result["stop_reason"] == "refusal"
    assert result["provider"] == "anthropic"
    # 拒答也消耗了输入 token:usage / 成本保留给台账,不丢
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 7
    assert result["cost_micro_usd"] > 0
    assert result["text"] == ""


def test_empty_text_with_abnormal_stop_is_provider_error(monkeypatch):
    _clear_policy_env(monkeypatch)
    _, result = _capture(monkeypatch, _anthropic_body(text="", stop_reason="max_tokens"))
    assert result["status"] == "provider_error"
    assert result["error"] == "anthropic_empty_max_tokens"


def test_max_tokens_stop_marks_result_truncated(monkeypatch):
    _clear_policy_env(monkeypatch)
    _, result = _capture(monkeypatch, _anthropic_body(text='{"a": 1', stop_reason="max_tokens"))
    assert result["status"] == "success"
    assert result["truncated"] is True
    assert result["stop_reason"] == "max_tokens"


def test_text_block_filter_skips_thinking_blocks(monkeypatch):
    _clear_policy_env(monkeypatch)
    body = _anthropic_body()
    body["content"] = [
        {"type": "thinking", "thinking": "internal"},
        {"type": "text", "text": "visible"},
    ]
    _, result = _capture(monkeypatch, body)
    assert result["text"] == "visible"


class _Reservations:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def reserve_llm_budget(self, **kwargs: Any) -> Any:
        self.events.append(("reserve", kwargs))
        return SimpleNamespace(reservation_key="llmres-refusal")

    def mark_llm_provider_started(self, key: str) -> None:
        self.events.append(("started", key))

    def settle_llm_reservation(self, key: str, cost_usd: float) -> dict[str, Any]:
        self.events.append(("settled", (key, cost_usd)))
        return {"settled": True}

    def mark_llm_provider_unknown(self, key: str) -> bool:
        self.events.append(("unknown", key))
        return True

    def release_llm_reservation(self, key: str) -> bool:
        self.events.append(("released", key))
        return True


def test_refusal_advances_candidate_chain_and_settles_known_usage(monkeypatch):
    """Anthropic 拒答 → 链推进到 fallback provider;拒答行以 provider_error 入台账,
    usage 非零则按实际成本结算预留(而非 unknown 挂账)。"""
    from app.platform import llm_gateway as gateway

    _clear_policy_env(monkeypatch)
    reservations = _Reservations()
    ledgers: list[dict[str, Any]] = []
    bindings = {"anthropic/claude-sonnet-5", "openai/gpt-5.6-luna"}
    monkeypatch.setattr(
        gateway,
        "exact_binding_readiness_from_environment",
        lambda binding: (
            {"binding": binding, "production_ready": binding in bindings},
            {"source": "test_signed_fixture"},
        ),
    )
    monkeypatch.setenv(
        "VKPI_LLM_RUNTIME_VERIFIED_MODELS",
        "anthropic/claude-sonnet-5,openai/gpt-5.6-luna",
    )
    monkeypatch.setattr(gateway, "_is_provider_configured", lambda provider: provider in {"anthropic", "openai"})
    monkeypatch.setattr(gateway, "_budget_allows_provider", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(gateway, "_estimated_cost_usd", lambda *_a, **_k: 0.001)
    monkeypatch.setattr(gateway, "_llm_budget_reservations", lambda: reservations)
    monkeypatch.setattr(gateway, "record_call", lambda **kwargs: ledgers.append(kwargs) or {})
    monkeypatch.setattr(gateway, "_acquire_strict_fleet_breaker", lambda **_k: object())
    monkeypatch.setattr(gateway, "_complete_strict_fleet_breaker", lambda *_a, **_k: None)

    calls: list[tuple[str, str | None]] = []

    def anthropic(_prompt: str, _tokens: int, *, model_override: str | None = None) -> dict[str, Any]:
        calls.append(("anthropic", model_override))
        return {
            "status": "provider_error",
            "error": "anthropic_refusal",
            "stop_reason": "refusal",
            "provider": "anthropic",
            "model": model_override,
            "text": "",
            "input_tokens": 120,
            "output_tokens": 0,
        }

    def openai(_prompt: str, _tokens: int, *, model_override: str | None = None) -> dict[str, Any]:
        calls.append(("openai", model_override))
        return {
            "status": "success",
            "provider": "openai",
            "model": model_override,
            "text": "fallback ok",
            "input_tokens": 10,
            "output_tokens": 5,
        }

    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "anthropic", anthropic)
    monkeypatch.setitem(gateway._PROVIDER_CALLERS, "openai", openai)

    result = gateway.invoke(
        "hello",
        purpose="anthropic-refusal-chain-test",
        preferred_provider="anthropic",
        model_override="claude-sonnet-5",
        model_fallbacks=(("openai", "gpt-5.6-luna"),),
        skip_budget_check=True,
        enforce_atomic_reservation=True,
    )

    assert calls == [("anthropic", "claude-sonnet-5"), ("openai", "gpt-5.6-luna")]
    assert result["status"] == "success"
    assert result["provider"] == "openai"
    assert result["text"] == "fallback ok"
    refusal_rows = [row for row in ledgers if row["status"] == "provider_error"]
    assert refusal_rows, [row["status"] for row in ledgers]
    assert refusal_rows[0]["input_tokens"] == 120
    # 拒答 usage 已知 → 结算而非 unknown;第二候选正常结算
    kinds = [event[0] for event in reservations.events]
    assert kinds == ["reserve", "started", "settled", "reserve", "started", "settled"], kinds
    assert "unknown" not in kinds
