"""
services/ai/analyzers/anthropic_response_text.py — Anthropic 回包文本块拼接
===========================================================================
Sonnet 5 / Opus 5 在思考开启时,``response.content[0]`` 可能是 ThinkingBlock
(没有 ``.text``),直接索引会 AttributeError 或拿到空串。这里只收 ``type=="text"``
的块并按顺序拼起来,调用方一律用它代替 ``content[0].text``。

合并说明:B 车道会在 platform/llm_production_anthropic_helpers.py 里提供同名
helper;合并时把各调用点的 import 切过去即可,本模块保持单函数、零依赖。
"""
from __future__ import annotations

from typing import Any


def text_blocks_joined(response: Any, *, separator: str = "\n") -> str:
    """Return the concatenated text of all ``type=="text"`` blocks in *response*.

    - 接受 SDK Message 对象或 dict(``{"content": [...]}``),块可以是对象或 dict。
    - 思考块 / 工具块等带 ``type`` 且非 text 的块一律跳过;没有 ``type`` 但带 ``text``
      的块(测试桩 / 旧 SDK)视为文本块;没有文本块时返回空串,绝不抛索引错误。
    """
    if response is None:
        return ""
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            text = block.get("text")
        else:
            block_type = getattr(block, "type", None)
            text = getattr(block, "text", None)
        if text is None or (block_type is not None and block_type != "text"):
            continue
        parts.append(str(text))
    return separator.join(parts)
