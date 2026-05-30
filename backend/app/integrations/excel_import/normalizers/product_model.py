"""Product model cleanup for promotion-plan imports."""
from __future__ import annotations

import re


def normalize_product_model(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
    raw = raw.replace("宣发推广", "").replace("推广", "").strip(" -")
    raw = raw.replace("/1.", " F1.").replace("/4.", " F4.")
    return raw

