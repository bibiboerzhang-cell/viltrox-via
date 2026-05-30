"""Handle normalization for owned accounts and KOL names."""
from __future__ import annotations

import re
from urllib.parse import urlparse


def normalize_handle(value: str) -> str:
    raw = str(value or "").strip().replace("\u3000", " ")
    if not raw:
        return ""
    if re.match(r"^https?://", raw, re.I) or raw.lower().startswith("www."):
        try:
            parsed = urlparse(raw if re.match(r"^https?://", raw, re.I) else f"https://{raw}")
            parts = [part for part in parsed.path.split("/") if part]
            if parts:
                raw = next((part for part in parts if part.startswith("@")), parts[-1])
        except ValueError:
            pass
    raw = raw.strip().lstrip("@")
    raw = re.sub(r"\s+", " ", raw)
    raw = re.sub(r"\s*([._-])\s*", r"\1", raw)
    raw = raw.strip(" \t\r\n-_/：:，,;；")
    return raw.lower()
