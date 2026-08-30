"""Dependency-free product-alias normalization shared across domains."""

from __future__ import annotations

import re
from typing import Any

from app.core.coerce import _text


APERTURE_REPLACEMENTS = (
    ("f1.0", "f10"),
    ("f1.2", "f12"),
    ("f1.4", "f14"),
    ("f1.7", "f17"),
    ("f1.8", "f18"),
    ("f2.0", "f20"),
    ("f2.5", "f25"),
    ("f2.8", "f28"),
    ("f3.5", "f35"),
    ("f4.0", "f40"),
)


def normalize_product_alias(value: Any) -> str:
    """Normalize product aliases for equality checks without losing SKU tokens."""
    text = _text(value).lower()
    text = text.replace("full- frame", "full frame")
    text = re.sub(r"\bf\s*(\d)\s*[-_]\s*(\d)\b", r"f\1\2", text)
    text = re.sub(r"\bf\s*=\s*(\d{1,3})\s*mm\b", r"\1mm", text)
    text = re.sub(r"\bf\s*/?\s*(\d)\s*\.\s*(\d)\b", r"f\1\2", text)
    for before, after in APERTURE_REPLACEMENTS:
        text = text.replace(before, after)
    text = text.replace("aps-c", "apsc")
    text = re.sub(r"(\d{1,3})\s*mm\b", r"\1mm", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " ".join(text.split())
    return re.sub(r"\bf([1-9])\b", r"f\g<1>0", text)
