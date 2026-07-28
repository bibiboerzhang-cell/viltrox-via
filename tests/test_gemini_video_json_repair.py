"""Gemini 视频分析 JSON 解析修复梯契约(2026-07-16 evidence 3972 实弹失败催生)。

只允许确定性语法修复(尾逗号/行尾漏逗号);内容瑕疵仍必须抛原始错误。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.ai.analyzers.gemini_video_results import (  # noqa: E402
    InvalidFinalV1ResultError,
    _normalise_final_v1_result,
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


def test_concatenated_objects_use_first_balanced_top_level_object():
    raw = '{"a": 1, "text": "literal } { stays inside"}\n{"b": 2}'
    assert _parse_json_response_text(raw) == {
        "a": 1,
        "text": "literal } { stays inside",
    }


def test_fenced_prose_and_concatenated_object_still_extract_first_object():
    raw = (
        "Here is the analysis:\n"
        "```json\n"
        '{"a": {"nested": true}}\n'
        "```\n"
        'Follow-up metadata: {"ignored": true}'
    )
    assert _parse_json_response_text(raw) == {"a": {"nested": True}}


def test_concatenated_json_does_not_bypass_final_v1_schema_validation():
    parsed = _parse_json_response_text(
        '{"note": "not a final-v1 payload"}\n'
        '{"layer1_visual_content": {"content_summary": "ignored second object"}}'
    )

    with pytest.raises(InvalidFinalV1ResultError, match="missing_core_content"):
        _normalise_final_v1_result(parsed, subtitle_used=False)


def test_unrepairable_json_still_raises():
    with pytest.raises(Exception):
        _parse_json_response_text('{"a": "unterminated')


def test_string_content_never_mutated():
    # 字符串内部的换行+引号组合不应被修复梯改写(合法 JSON 直接首轮通过)
    raw = '{"a": "line1\\nline2 \\"quoted\\""}'
    assert _parse_json_response_text(raw) == {"a": 'line1\nline2 "quoted"'}


def test_trailing_comma_inside_string_preserved_and_outside_repaired():
    # 2026-07-16 红队实证案例:字符串内的 ",]" 必须原样保留,字符串外的尾逗号修掉
    raw = '{"note": "hooks: [a,], done", "list": [1, 2,]}'
    assert _parse_json_response_text(raw) == {
        "note": "hooks: [a,], done",
        "list": [1, 2],
    }


def test_candidate_rejected_if_string_tokens_would_change():
    # 无法在不动字符串内容的前提下修好的输入,必须抛原始错误而不是有损修复
    raw = '{"a": "unclosed [1,2,] '
    import pytest as _pytest

    with _pytest.raises(Exception):
        _parse_json_response_text(raw)
