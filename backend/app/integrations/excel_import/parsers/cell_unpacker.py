"""Unpack comma/newline-separated schedule cells into tokens."""
from __future__ import annotations

import re
from typing import Any


TOKEN_SPLIT_RE = re.compile(r"[\n\r;,，；]+")


def unpack_cell(value: Any) -> list[str]:
    raw = str(value or "").replace("\u3000", " ").strip()
    if not raw:
        return []
    tokens = [re.sub(r"\s+", " ", token).strip() for token in TOKEN_SPLIT_RE.split(raw)]
    return [token for token in tokens if token]

