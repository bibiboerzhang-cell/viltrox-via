"""Gemini 视频分析 JSON 解析修复梯契约(2026-07-16 evidence 3972 实弹失败催生)。

只允许确定性语法修复(尾逗号/行尾漏逗号);内容瑕疵仍必须抛原始错误。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.ai.analyzers.gemini_video_results import (  # noqa: E402
    _parse_json_response_text,
)


def test_clean_json_still_parses():
    assert _parse_json_response_text('{"a": 1}') == {"a": 1}


def test_code_fence_stripped():
    assert _parse_json_response_text('```json\n{"a": 1}\n```') == {"a": 1}


def test_trailing_comma_repaired():
    raw = '{"a": 1, "b": [1, 2,],}'
    assert _parse_json_response_text(raw) == {"a": 1, "b": [1, 2]}


def test_missing_comma_between_lines_repaired():
    # evidence 3972 实例形态:行尾漏逗号,下一行直接开新键
    raw = '{\n  "a": "value"\n  "b": 2\n}'
    assert _parse_json_response_text(raw) == {"a": "value", "b": 2}


def test_missing_comma_after_object_repaired():
    raw = '{\n  "a": {"x": 1}\n  "b": [2]\n  "c": 3\n}'
    assert _parse_json_response_text(raw) == {"a": {"x": 1}, "b": [2], "c": 3}


def test_prose_wrapped_object_still_extracted():
    raw = 'Here is the analysis:\n{"a": 1}\nHope this helps.'
    assert _parse_json_response_text(raw) == {"a": 1}


def test_unrepairable_json_still_raises():
    with pytest.raises(Exception):
        _parse_json_response_text('{"a": "unterminated')


def test_string_content_never_mutated():
    # 字符串内部的换行+引号组合不应被修复梯改写(合法 JSON 直接首轮通过)
    raw = '{"a": "line1\\nline2 \\"quoted\\""}'
    assert _parse_json_response_text(raw) == {"a": 'line1\nline2 "quoted"'}
