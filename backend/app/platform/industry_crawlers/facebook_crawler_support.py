"""Non-network support pieces for ``FacebookCrawler``.

Extracted verbatim from ``facebook_crawler.py`` (class-LOC 418→≤400 ratchet
wave): the P1.2 placeholder responses (brand-mention search, Meta Graph path)
and the URL normalization helpers. Both Apify execution paths stay in
``facebook_crawler.py`` on purpose — the client-lifecycle ownership test audits
that file's source for ``ApifyClient(`` + ``close_apify_client``.
"""

from __future__ import annotations

from typing import Any


def brand_mentions_not_supported(query: str, limit: int) -> dict[str, Any]:
    """
    Apify path: brand mention search.

    Note: Facebook search via Apify is limited. Most actors don't support
    full-text search across all of Facebook (privacy/ToS). This method
    attempts a best-effort approach using a search-targeted actor if
    configured, otherwise returns 'not_supported'.
    """
    # Facebook search is not well-supported by public Apify actors
    # P1.2 returns optimistic placeholder - team can extend if needed
    return {
        "items": [],
        "provider_status": "not_supported",
        "sync_status": "skip",
        "provider": "apify",
        "error": (
            "Facebook brand mention search not supported in P1.2. "
            "Use Page profile monitoring instead. "
            "Team: consider Meta Graph API for proper search (long-term)."
        ),
        "query": query,
    }


def page_profile_via_meta_graph(page_url: str, max_posts: int) -> dict[str, Any]:
    """
    Meta Graph API path - reserved for long-term migration.
    Not implemented in P1.2.
    """
    return {
        "items": [],
        "provider_status": "not_implemented",
        "sync_status": "skip",
        "provider": "meta_graph",
        "error": (
            "Meta Graph API not implemented in P1.2. "
            "Reserved for long-term migration after Viltrox Meta App Review."
        ),
    }


def normalize_page_url(url_or_handle: str) -> str:
    """Convert handle/page name/URL to canonical Facebook Page URL."""
    s = url_or_handle.strip().rstrip("/")
    if not s:
        return ""
    if s.startswith("https://") or s.startswith("http://"):
        return s
    if s.startswith("facebook.com/"):
        return f"https://www.{s}"
    if s.startswith("www.facebook.com/"):
        return f"https://{s}"
    # Assume it's a Page handle/name
    return f"https://www.facebook.com/{s.lstrip('@/')}"


def handle_to_page_url(handle: str, channel_id: str = "") -> str:
    """V-KPI handle → Facebook Page URL."""
    if channel_id and channel_id.strip():
        # Channel ID treated as Page handle
        return normalize_page_url(channel_id)
    return normalize_page_url(handle)


def normalize_post_url(url_or_id: str) -> str:
    """Normalize Facebook post URL/id for comment actor input."""
    raw = str(url_or_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return f"https://www.facebook.com/posts/{raw.strip('/')}"


__all__ = [
    "brand_mentions_not_supported",
    "handle_to_page_url",
    "normalize_page_url",
    "normalize_post_url",
    "page_profile_via_meta_graph",
]
