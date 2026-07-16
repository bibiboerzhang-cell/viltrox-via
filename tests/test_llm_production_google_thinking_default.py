"""严格 Gemini 边界的思考默认关闭契约(SDK 路径 769 事故复发修复,2026-07-16)。

- flash 系默认注入 thinking_budget=0(思考 token 吃 max_output_tokens → 正文截断);
- 调用方显式设置过 thinking_config 不动;
- pro 系不允许关思考,跳过;
- 不给 model 时保持旧行为(不注入)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.platform.llm_production_google_helpers import (  # noqa: E402
    google_config_with_output_limit,
)


def test_flash_gets_zero_thinking_budget():
    out = google_config_with_output_limit(None, 4096, model="gemini-2.5-flash")
    assert out["max_output_tokens"] == 4096
    assert out["thinking_config"] == {"thinking_budget": 0}


def test_dict_config_merges_thinking_and_limit():
    out = google_config_with_output_limit(
        {"temperature": 0.2}, 1024, model="gemini-3.5-flash"
    )
    assert out == {
        "temperature": 0.2,
        "max_output_tokens": 1024,
        "thinking_config": {"thinking_budget": 0},
    }


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


def test_pro_model_not_touched():
    out = google_config_with_output_limit(None, 2048, model="gemini-2.5-pro")
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
    assert out["thinking_config"] == {"thinking_budget": 0}
