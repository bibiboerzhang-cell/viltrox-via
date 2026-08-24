"""Conservative regional-brand official-account evidence.

This is a pure helper for discovery filtering: no I/O, provider calls, scoring
or persistence.  It is intentionally narrower than generic account-shape
guessing so creator/reviewer profiles keep failing open.
"""
from __future__ import annotations

import re
from typing import Any

from app.domains.kol.identity import (
    canonical_creator_aliases,
    canonical_identity_platform,
)


_BRAND_REGIONAL_ACCOUNT_SUFFIXES = frozenset(
    {"global", "usa", "us", "uk", "eu", "europe", "asia", "japan", "india", "hq"}
)


def regional_brand_profile_self_attributed(item: dict[str, Any], brand: str) -> bool:
    """Confirm a known brand's regional profile without broad shape guessing.

    The proof deliberately requires all three independent legs seen on the
    production ``tamron_europe`` row: a brand+region handle whose social
    profile URL resolves to that same handle, an exact brand/account display
    identity, and first-party ``by <brand>`` + ``our`` language in the bio.
    Missing any leg fails open so reviewers and personal ``official`` creators
    are not removed from discovery.
    """

    brand_norm = re.sub(r"[^a-z0-9]", "", str(brand or "").lower())
    raw_handle = str(item.get("handle") or item.get("username") or "").strip()
    handle_norm = re.sub(r"[^a-z0-9]", "", raw_handle.lower())
    if not brand_norm or not handle_norm.startswith(brand_norm):
        return False
    suffix = handle_norm[len(brand_norm) :]
    if suffix not in _BRAND_REGIONAL_ACCOUNT_SUFFIXES:
        return False

    identity_norms = {
        re.sub(r"[^a-z0-9]", "", str(item.get(field) or "").lower())
        for field in ("display_name", "channel_name", "username")
        if str(item.get(field) or "").strip()
    }
    if not identity_norms.intersection({brand_norm, handle_norm}):
        return False

    platform = canonical_identity_platform(item.get("platform"))
    handle_aliases = {
        alias
        for alias in canonical_creator_aliases(
            {"platform": platform, "handle": raw_handle}
        )
        if ":handle:" in alias
    }
    profile_aliases = {
        alias
        for alias in canonical_creator_aliases(
            {
                "platform": platform,
                "profile_url": item.get("profile_url") or item.get("channel_url"),
            }
        )
        if ":handle:" in alias
    }
    if not platform or not handle_aliases.intersection(profile_aliases):
        return False

    bio = re.sub(
        r"[^a-z0-9]+",
        " ",
        str(item.get("bio") or item.get("description") or "").lower(),
    ).strip()
    return bool(
        re.search(rf"\bby\s+{re.escape(brand_norm)}\b", bio)
        and re.search(r"\bour\b", bio)
    )
