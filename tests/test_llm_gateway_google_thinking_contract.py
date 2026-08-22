"""LLM 网关 Google 精确模型思考参数契约(HIGH)。

真实生产事故回归(769 全灭):gemini-2.5 系默认动态思考,思考 token 计入
maxOutputTokens——status=success 但正文全是思维链碎片 / 直接截断为空。
后续精确模型 canary 又证明 2.5 Pro 禁止 thinkingBudget=0,
Gemini 3 则使用 thinkingLevel(3.x 家族 thinkingBudget=0 会 400;3.7 /
*-latest 没有 minimal 档,直接拒绝)。本测试 monkeypatch HTTP 缝捕获真实
请求体,锁住显式映射表,并锁死 generationConfig 永不带
temperature/topP/topK(Gemini 3.x 已弃用采样参数,2026-08-22 摘除)。

零网络零真 key(_get_api_key 一并 patch);不触真库,不触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.platform.llm_gateway  # noqa: F401,E402 — 先初始化主模块(providers 与其互为底部循环 import,直接先 import providers 会撞半初始化)
from app.platform import llm_gateway_providers as providers  # noqa: E402


def _fake_google_response() -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }


def _capture_call(
    monkeypatch,
    max_output_tokens: int,
    *,
    model_override: str | None = "gemini-2.5-flash",
) -> dict:
    """跑一次 _call_google,返回被捕获的请求体 payload(零真实 HTTP)。"""
    captured: dict = {}

    def fake_request_json(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return _fake_google_response()

    monkeypatch.setattr(providers, "_request_json", fake_request_json)
    monkeypatch.setattr(providers, "_get_api_key", lambda provider: "test-key")
    result = providers._call_google(
        "hello world",
        max_output_tokens,
        model_override=model_override,
    )
    assert result["status"] == "success", result  # 缝生效,没走真网络失败分支
    captured["result"] = result
    return captured


def test_call_google_generation_config_locks_thinking_off(monkeypatch):
    """回归锁:generationConfig 恰好 {maxOutputTokens, thinkingConfig},
    thinkingConfig.thinkingBudget==0(缺一即历史事故复活);永不带采样参数。"""
    captured = _capture_call(monkeypatch, max_output_tokens=800)
    gen = captured["payload"]["generationConfig"]
    assert gen["maxOutputTokens"] == 800
    assert gen["thinkingConfig"] == {"thinkingBudget": 0}
    # 两键齐、无多余漂移键(请求体契约整体锁死)
    assert set(gen.keys()) == {"maxOutputTokens", "thinkingConfig"}
    for forbidden in ("temperature", "topP", "topK", "top_p", "top_k"):
        assert forbidden not in gen
    # prompt 走 contents/parts 标准形状
    assert captured["payload"]["contents"] == [{"parts": [{"text": "hello world"}]}]


def _thinking_of(monkeypatch, model: str):
    captured = _capture_call(monkeypatch, max_output_tokens=128, model_override=model)
    gen = captured["payload"]["generationConfig"]
    assert "temperature" not in gen and "topP" not in gen and "topK" not in gen
    return gen.get("thinkingConfig")


def test_call_google_uses_supported_thinking_controls_per_exact_model(monkeypatch):
    """显式映射表(实测 2026-08-22):3.x 非 pro → thinkingLevel minimal;
    3.x pro → 不注入;2.5-pro → budget 128;其余 2.5 → budget 0。"""
    assert _thinking_of(monkeypatch, "gemini-2.5-pro") == {"thinkingBudget": 128}
    assert _thinking_of(monkeypatch, "gemini-2.5-flash") == {"thinkingBudget": 0}
    assert _thinking_of(monkeypatch, "gemini-2.5-flash-lite") == {"thinkingBudget": 0}
    assert _thinking_of(monkeypatch, "gemini-3.6-flash") == {"thinkingLevel": "minimal"}
    assert _thinking_of(monkeypatch, "gemini-3.5-flash") == {"thinkingLevel": "minimal"}
    assert _thinking_of(monkeypatch, "gemini-3.5-flash-lite") == {"thinkingLevel": "minimal"}
    assert _thinking_of(monkeypatch, "gemini-3.5-pro") is None
    # 非 gemini-2.5/3 家族(如测试用假 id)不注入任何 thinkingConfig
    assert _thinking_of(monkeypatch, "gemini-exact") is None


def test_call_google_rejects_models_without_minimal_thinking_level(monkeypatch):
    """gemini-3.7* 与 *-latest 没有 minimal 档(默认每次烧 ~60 思考 token 并吃掉
    maxOutputTokens):零网络直接 provider_config_unsupported,让 fallback 链接管。"""
    calls: list[dict] = []

    def fake_request_json(url, payload, headers, timeout):
        calls.append(payload)
        return _fake_google_response()

    monkeypatch.setattr(providers, "_request_json", fake_request_json)
    monkeypatch.setattr(providers, "_get_api_key", lambda provider: "test-key")
    for model in ("gemini-3.7-flash", "gemini-flash-latest", "gemini-3.7-pro"):
        result = providers._call_google("hello", 128, model_override=model)
        assert result["status"] == "provider_config_unsupported", result
        assert result["provider"] == "google"
        assert "no_minimal_thinking_level" in result["error"]
    assert calls == []  # 拒绝发生在任何 HTTP 之前


def test_call_google_token_clamp_keeps_thinking_off(monkeypatch):
    """token 越界被钳(1..4000)时思考仍关死(钳制路径不绕开 thinkingConfig)。"""
    over = _capture_call(monkeypatch, max_output_tokens=99999)
    gen_over = over["payload"]["generationConfig"]
    assert gen_over["maxOutputTokens"] == 4000
    assert gen_over["thinkingConfig"] == {"thinkingBudget": 0}

    zero = _capture_call(monkeypatch, max_output_tokens=0)
    gen_zero = zero["payload"]["generationConfig"]
    assert gen_zero["maxOutputTokens"] == 800  # 0/缺省 → 默认 800
    assert gen_zero["thinkingConfig"] == {"thinkingBudget": 0}


def test_call_google_parses_text_and_usage_through_seam(monkeypatch):
    """缝内响应解析契约:正文/usage token 数原样透出(事故排查靠 output_tokens)。"""
    captured = _capture_call(monkeypatch, max_output_tokens=800)
    result = captured["result"]
    assert result["provider"] == "google"
    assert result["text"] == "ok"
    assert result["input_tokens"] == 10
    assert result["output_tokens"] == 5
    assert result["visible_output_tokens"] == 5
    assert result["thinking_tokens"] == 0


def test_call_google_bills_thinking_and_visible_output_tokens(monkeypatch):
    def fake_request_json(_url, _payload, _headers, _timeout):
        return {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "thoughtsTokenCount": 7,
            },
        }

    monkeypatch.setattr(providers, "_request_json", fake_request_json)
    monkeypatch.setattr(providers, "_get_api_key", lambda _provider: "test-key")
    result = providers._call_google(
        "hello world",
        256,
        model_override="gemini-2.5-pro",
    )

    assert result["status"] == "success"
    assert result["visible_output_tokens"] == 5
    assert result["thinking_tokens"] == 7
    assert result["output_tokens"] == 12
