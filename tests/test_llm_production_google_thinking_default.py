"""严格 Gemini 边界的思考默认配置契约(SDK 路径 769 事故复发修复,2026-07-16)。

- flash 系默认注入有界思考 budget=0(思考 token 吃 max_output_tokens → 正文截断);
- gemini-2.5-pro 不允许关思考但可有界:budget=128(对齐 canary 实弹矩阵);
- 其余 pro 系证据不足不动;
- 调用方显式设置过 thinking_config 一律不动;
- 不给 model 时保持旧行为(不注入);
- 注入优先 google.genai ThinkingConfig 实例(消 pydantic 序列化警告),环境缺库退 dict。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.platform.llm_production_google_helpers import (  # noqa: E402
    google_config_with_output_limit,
)


def _budget_of(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get("thinking_budget")
    return getattr(value, "thinking_budget", None)


def test_flash_gets_zero_thinking_budget():
    out = google_config_with_output_limit(None, 4096, model="gemini-2.5-flash")
    assert out["max_output_tokens"] == 4096
    assert _budget_of(out["thinking_config"]) == 0


def test_dict_config_merges_thinking_and_limit():
    out = google_config_with_output_limit(
        {"temperature": 0.2}, 1024, model="gemini-3.5-flash"
    )
    assert out["temperature"] == 0.2
    assert out["max_output_tokens"] == 1024
    assert _budget_of(out["thinking_config"]) == 0


def test_explicit_thinking_config_preserved():
    out = google_config_with_output_limit(
        {"thinking_config": {"thinking_budget": 512}}, 1024, model="gemini-2.5-flash"
    )
    assert out["thinking_config"] == {"thinking_budget": 512}


def test_camel_case_thinking_config_preserved():
    out = google_config_with_output_limit(
        {"thinkingConfig": {"thinkingBudget": 256}}, 1024, model="gemini-2.5-flash"
    )
    assert "thinking_config" not in out
    assert out["thinkingConfig"] == {"thinkingBudget": 256}


def test_pro_25_gets_bounded_thinking_budget_128():
    out = google_config_with_output_limit(None, 2048, model="gemini-2.5-pro")
    assert out["max_output_tokens"] == 2048
    assert _budget_of(out["thinking_config"]) == 128


def test_other_pro_models_not_touched():
    out = google_config_with_output_limit(None, 2048, model="gemini-3.5-pro")
    assert out == {"max_output_tokens": 2048}


def test_no_model_keeps_legacy_behavior():
    out = google_config_with_output_limit(None, 2048)
    assert out == {"max_output_tokens": 2048}


def test_pydantic_like_config_gets_update():
    class FakeConfig:
        def __init__(self):
            self.thinking_config = None

        def model_copy(self, update):
            merged = {"thinking_config": self.thinking_config}
            merged.update(update)
            return merged

    out = google_config_with_output_limit(FakeConfig(), 4096, model="gemini-2.5-flash")
    assert out["max_output_tokens"] == 4096
    assert _budget_of(out["thinking_config"]) == 0


def test_tool_carrying_config_not_bounded():
    # 接地搜索是模型在思考期间自主调用的——带 tools 的调用不注入思考上限
    out = google_config_with_output_limit(
        {"tools": [{"google_search": {}}]}, 8192, model="gemini-2.5-pro"
    )
    assert "thinking_config" not in out
    assert out["max_output_tokens"] == 8192


def test_tool_carrying_pydantic_config_not_bounded():
    class FakeConfig:
        def __init__(self):
            self.thinking_config = None
            self.tools = [object()]

        def model_copy(self, update):
            merged = {"thinking_config": self.thinking_config, "tools": self.tools}
            merged.update(update)
            return merged

    out = google_config_with_output_limit(FakeConfig(), 8192, model="gemini-2.5-flash")
    assert out["thinking_config"] is None
    assert out["max_output_tokens"] == 8192
