"""Owned/KOL classifier with strict owned account matching."""
from __future__ import annotations

from typing import Literal

from app.integrations.excel_import.normalizers.handle import normalize_handle
from app.integrations.excel_import.normalizers.platform import normalize_platform


Classification = Literal["owned", "kol", "unknown"]

STATIC_OWNED_HANDLES = {
    ("viltrox.official", "instagram"),
    ("viltroxofficial", "instagram"),
    ("viltrox.us", "instagram"),
    ("viltrox usa", "instagram"),
    ("viltrox.usa", "tiktok"),
    ("viltrox.global", "tiktok"),
    ("viltrox official", "youtube"),
    ("viltroxofficial", "youtube"),
    ("viltrox", "facebook"),
    ("viltrox official", "x"),
    ("viltroxofficial", "x"),
    ("viltrox_global", "reddit"),
    ("viltrox", "discord"),
    ("viltrox user group", "facebook_group"),
}


def classify_owned_vs_kol(
    handle: str,
    platform: str,
    *,
    owned_whitelist: set[tuple[str, str]] | None = None,
    source_kind: str = "",
) -> Classification:
    norm = normalize_handle(handle)
    platform_norm = normalize_platform(platform)
    if not norm:
        return "unknown"

    whitelist = set(owned_whitelist or set()) | STATIC_OWNED_HANDLES
    if (norm, platform_norm) in whitelist or (norm.replace(" ", ""), platform_norm) in whitelist:
        return "owned"

    if source_kind == "kol_assignment":
        if "viltrox" in norm:
            return "unknown"
        return "kol" if len(norm) > 1 else "unknown"

    if " " in str(handle).strip() and any(ch.isupper() for ch in str(handle)):
        return "kol"
    return "unknown"

