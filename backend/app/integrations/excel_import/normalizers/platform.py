"""Platform normalization for schedule tokens."""
from __future__ import annotations

import re


PLATFORM_ALIASES = {
    "ig": "instagram",
    "ins": "instagram",
    "instagram": "instagram",
    "facebook": "facebook",
    "fb": "facebook",
    "facebook group": "facebook_group",
    "youtube": "youtube",
    "yt": "youtube",
    "tiktok": "tiktok",
    "tik tok": "tiktok",
    "x": "x",
    "twitter": "x",
    "reddit": "reddit",
    "discord": "discord",
    "media": "media",
}


def normalize_platform(value: str) -> str:
    raw = re.sub(r"\s+", " ", str(value or "").strip().lower())
    raw = raw.strip("【】[]()（）")
    return PLATFORM_ALIASES.get(raw, "unknown")

