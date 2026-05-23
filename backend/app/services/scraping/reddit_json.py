"""
services/scraping/reddit_json.py — Reddit JSON API 抓取
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from app.core.constants import USER_AGENT
from app.core.logging import get_logger

logger = get_logger(__name__)


async def scrape_reddit_json(url: str) -> Dict[str, Any]:
    """Use Reddit's public JSON API — much more reliable than Playwright."""
    import urllib.request as ureq

    empty = {
        "scraped_ok": False,
        "title": "", "caption": "", "scraped_text": "",
        "metrics": {"views": 0, "likes": 0, "comments": 0, "shares": 0, "favorites": 0},
        "metrics_available": {"views": False, "likes": False, "comments": False, "shares": False, "favorites": False},
        "visible_comments": [],
        "error": "Reddit JSON fetch failed",
    }

    try:
        # Strip query params and add .json
        clean = re.sub(r"\?.*$", "", url.rstrip("/"))
        json_url = clean + ".json"

        def _fetch():
            req = ureq.Request(json_url, headers={
                "User-Agent": "ViltroxKOLAudit/3.0 (internal tool)",
                "Accept": "application/json",
            })
            with ureq.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))

        data = await asyncio.to_thread(_fetch)
        post = data[0]["data"]["children"][0]["data"]

        title      = post.get("title", "")
        selftext   = post.get("selftext", "")
        score      = int(post.get("score", 0))
        n_comments = int(post.get("num_comments", 0))
        subreddit  = post.get("subreddit_name_prefixed", "")

        body_text = f"{title} {selftext} {subreddit}"

        # Grab top-level comments from listing [1]
        visible_comments: List[str] = []
        try:
            for child in data[1]["data"]["children"][:8]:
                d = child.get("data", {})
                body = (d.get("body") or "").strip()
                if body and body != "[deleted]":
                    visible_comments.append(body)
        except Exception as exc:
            logger.debug("reddit visible comment parse failed for %s: %s", json_url, exc)

        return {
            "scraped_ok": True,
            "title": title,
            "caption": selftext[:500],
            "scraped_text": body_text,
            "metrics": {
                "views": 0,
                "likes": score,
                "comments": n_comments,
                "shares": 0,
                "favorites": 0,
            },
            "metrics_available": {
                "views": False,
                "likes": True,
                "comments": True,
                "shares": False,
                "favorites": False,
            },
            "visible_comments": visible_comments,
            "error": None,
        }
    except Exception as e:
        empty["error"] = str(e)
        return empty
