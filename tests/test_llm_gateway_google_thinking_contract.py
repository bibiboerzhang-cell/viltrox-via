"""LLM 网关 google(gemini)请求体契约 —— 关思考回归锁(HIGH)。

真实生产事故回归(769 全灭):gemini-2.5 系默认动态思考,思考 token 计入
maxOutputTokens——status=success 但正文全是思维链碎片 / 直接截断为空。
网关修复(90f837739)在 generationConfig 里钉死 thinkingConfig.thinkingBudget=0;
本测试 monkeypatch HTTP 缝(llm_gateway_providers._request_json)捕获
_call_google 的真实请求体,把三件套(maxOutputTokens / temperature /
thinkingBudget==0)锁进契约——谁改掉关思考,这里立刻红。

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


def _capture_call(monkeypatch, max_output_tokens: int) -> dict:
    """跑一次 _call_google,返回被捕获的请求体 payload(零真实 HTTP)。"""
    captured: dict = {}

    def fake_request_json(url, payload, headers, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return _fake_google_response()

    monkeypatch.setattr(providers, "_request_json", fake_request_json)
    monkeypatch.setattr(providers, "_get_api_key", lambda provider: "test-key")
    result = providers._call_google("hello world", max_output_tokens)
    assert result["status"] == "success", result  # 缝生效,没走真网络失败分支
    captured["result"] = result
    return captured


def test_call_google_generation_config_locks_thinking_off(monkeypatch):
    """回归锁:generationConfig 必须同时含 maxOutputTokens、temperature、
    thinkingConfig.thinkingBudget==0(缺一即历史事故复活)。"""
    captured = _capture_call(monkeypatch, max_output_tokens=800)
    gen = captured["payload"]["generationConfig"]
    assert gen["maxOutputTokens"] == 800
    assert gen["temperature"] == 0.2
    assert gen["thinkingConfig"] == {"thinkingBudget": 0}
    # 三键齐、无多余漂移键(请求体契约整体锁死)
    assert set(gen.keys()) == {"maxOutputTokens", "temperature", "thinkingConfig"}
    # prompt 走 contents/parts 标准形状
    assert captured["payload"]["contents"] == [{"parts": [{"text": "hello world"}]}]


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
