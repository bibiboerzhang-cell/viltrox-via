"""
services/ingestion/research.py — autonomous web / reddit learning intake
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlparse

import httpx

from app.core.constants import USER_AGENT
from app.services.audit.similarity import classify_product, detect_gear_mentions
from app.services.scraping.platform_router import detect_platform_from_url, scrape_url
from app.utils.handles import extract_handle_from_url
from app.utils.text import detect_content_types


def detect_learning_source(url: str, preferred_source: str = "") -> str:
    preferred = (preferred_source or "").strip().lower()
    if preferred:
        return preferred
    platform = detect_platform_from_url(url)
    if platform == "Unknown":
        return "web"
    return platform.lower()


def _meta(html: str, key: str) -> str:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return unescape(match.group(1).strip())
    return ""


def _strip_html(html: str) -> str:
    cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return unescape(cleaned).strip()


async def _fetch_generic_web_page(url: str) -> dict:
    timeout = httpx.Timeout(30.0, connect=10.0, read=30.0)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        response = await client.get(url, headers=headers)
    html = response.text[:500_000]
    title_match = re.search(r"(?is)<title>(.*?)</title>", html)
    title = unescape(title_match.group(1).strip()) if title_match else ""
    description = _meta(html, "description") or _meta(html, "og:description")
    og_image = _meta(html, "og:image")
    body_text = _strip_html(html)[:5000]
    return {
        "scraped_ok": response.status_code < 400,
        "title": title[:300],
        "caption": description[:1000],
        "scraped_text": body_text,
        "og_image": og_image,
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {"views": False, "likes": False, "comments": False, "shares": False, "favorites": False},
        "visible_comments": [],
        "published_at": "",
        "error": None if response.status_code < 400 else f"HTTP {response.status_code}",
        "scraper": "httpx_web",
        "source_url": str(response.url),
        "host": urlparse(str(response.url)).netloc.lower(),
    }


async def collect_learning_payload(url: str, preferred_source: str = "") -> dict:
    source = detect_learning_source(url, preferred_source)
    if source == "web":
        scraped = await _fetch_generic_web_page(url)
    else:
        scraped = await scrape_url(url)

    text = " ".join(
        filter(
            None,
            [
                scraped.get("title", ""),
                scraped.get("caption", ""),
                scraped.get("scraped_text", ""),
            ],
        )
    ).strip()
    product = classify_product(text)
    gear = detect_gear_mentions(text)
    scene_tags = detect_content_types(text.lower()) if text else []
    creator_handle = (
        scraped.get("owner_username")
        or scraped.get("channel_name")
        or extract_handle_from_url(url)
        or ""
    )
    product_label = product.get("label", "")
    product_key = re.sub(r"[^a-z0-9]+", "-", product_label.lower()).strip("-") if product_label else ""
    aliases = [product_label] if product_label else []
    aliases.extend(product.get("evidence", []) or [])

    summary = scraped.get("title") or scraped.get("caption") or scraped.get("scraped_text", "")[:240]
    event_type = "community_signal" if source == "reddit" else "market_scan"
    entity_type = "discussion" if source == "reddit" else ("page" if source == "web" else "content")

    return {
        "source_platform": source,
        "event_type": event_type,
        "entity_type": entity_type,
        "external_id": url,
        "creator_handle": creator_handle,
        "source_url": scraped.get("source_url") or url,
        "summary": summary[:500],
        "metrics": scraped.get("metrics") or {},
        "observed_at": scraped.get("published_at") or "",
        "product_key": product_key,
        "product_label": product_label,
        "product_family": product.get("series", ""),
        "alias_terms": aliases,
        "feature_tags": (gear.get("lens_mentions") or [])[:8],
        "scene_tags": scene_tags[:8],
        "payload": {
            "source_url": scraped.get("source_url") or url,
            "scraper": scraped.get("scraper", ""),
            "title": scraped.get("title", ""),
            "caption": scraped.get("caption", ""),
            "scraped_text": scraped.get("scraped_text", ""),
            "og_image": scraped.get("og_image", ""),
            "visible_comments": scraped.get("visible_comments", []),
            "metrics": scraped.get("metrics", {}),
            "product_match": product,
            "gear_mentions": gear,
        },
    }
