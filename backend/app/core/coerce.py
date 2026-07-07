"""共享的容错强转小工具单源。

历史上 `_text` / `_loads` / `_truthy` / `_int0` 在各 domain 文件里被逐份复制,
实现完全一致却散落 100+ 处。此处收敛为唯一实现,各文件改为
`from app.core.coerce import _text` 直接复用(行为逐字节等价,AST 证过)。
只收敛与规范实现完全相同的副本;签名/行为有分歧的副本保留在原文件不动。
"""

from __future__ import annotations

import json
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


def _truthy(value: Any) -> bool:
    """compat 层 BOOLEAN 读回可能是 int 1/0 / 't' / 'true',统一容错。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "t", "true", "yes", "y")


def _int0(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0
