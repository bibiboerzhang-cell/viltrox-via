"""Rule-v0 cached comment summary for comment intelligence."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.db.connection import get_conn

POSITIVE_TERMS = (
    "love", "great", "amazing", "sharp", "beautiful", "excellent",
    "perfect", "nice", "喜欢", "好看", "优秀",
)
NEGATIVE_TERMS = (
    "bad", "issue", "problem", "broken", "fail", "slow", "noisy",
    "expensive", "bug", "问题", "失望", "太贵",
)
OPPORTUNITY_TERMS = (
    "buy", "price", "where", "when", "available", "link", "mount",
    "shipping", "discount", "购买", "价格", "哪里", "什么时候",
)
ISSUE_TERMS = (
    "autofocus", "firmware", "focus", "compatibility", "mount",
    "shipping", "warranty", "return", "af", "固件", "对焦", "兼容",
    "卡口", "售后",
)


from app.core.coerce import _text


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        row = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)


def _contains_term(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _rule_sentiment(text: str) -> str:
    positive = _contains_term(text, POSITIVE_TERMS)
    negative = _contains_term(text, NEGATIVE_TERMS)
    if positive and not negative:
        return "positive"
    if negative and not positive:
        return "negative"
    if positive and negative:
        return "mixed"
    return "neutral"


def _rule_tags(text: str) -> list[str]:
    tags: list[str] = []
    if "?" in text or "？" in text:
        tags.append("question")
    if _contains_term(text, OPPORTUNITY_TERMS):
        tags.append("opportunity")
    if _contains_term(text, ISSUE_TERMS) or _contains_term(text, NEGATIVE_TERMS):
        tags.append("issue")
    return tags


def _comment_rule_sample(row: Any) -> dict[str, Any]:
    item = dict(row)
    text = re.sub(r"\s+", " ", _text(item.get("comment_text")))[:500]
    tags = _rule_tags(text)
    return {
        "comment_id": int(item.get("id") or 0),
        "source": "vkpi_comments",
        "post_id": item.get("post_id"),
        "post_table": _text(item.get("post_table") or "industry_posts"),
        "external_post_id": _text(item.get("external_post_id")),
        "platform": _text(item.get("platform")),
        "author": _text(item.get("author_handle") or "anonymous"),
        "text_excerpt": text,
        "rule_sentiment": _rule_sentiment(text),
        "tags": tags,
        "likes": int(item.get("likes_count") or 0),
        "reply_count": int(item.get("reply_count") or 0),
        "created_at": _text(item.get("created_at")),
        "fetched_at": _text(item.get("fetched_at")),
        "rebuttal_supported": True,
    }


def rule_v0_comment_summary(*, cutoff: str, sample_limit: int) -> dict[str, Any]:
    safe_limit = max(1, min(50, int(sample_limit or 12)))
    if not _table_exists("vkpi_comments"):
        return {
            "status": "not_configured",
            "method": "rule_v0_cached_comments",
            "source": "vkpi_comments",
            "provider_calls": False,
            "llm_calls": False,
            "write_db": False,
            "contract": {"declared": None, "cached": 0, "cap": safe_limit, "status": "not_configured"},
            "counts": {},
            "samples": [],
        }
    conn = get_conn()
    cached_row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM vkpi_comments
        WHERE COALESCE(fetched_at, created_at) >= ?
          AND COALESCE(comment_text, '') <> ''
        """,
        (cutoff,),
    ).fetchone()
    cached = int((cached_row or {}).get("n") or 0)
    rows = conn.execute(
        """
        SELECT id, post_id, post_table, external_post_id, platform, external_comment_id,
               comment_text, author_handle, likes_count, reply_count, created_at, fetched_at
        FROM vkpi_comments
        WHERE COALESCE(fetched_at, created_at) >= ?
          AND COALESCE(comment_text, '') <> ''
        ORDER BY COALESCE(created_at, fetched_at) DESC, id DESC
        LIMIT ?
        """,
        (cutoff, safe_limit),
    ).fetchall()
    samples = [_comment_rule_sample(row) for row in rows]
    sentiments = Counter(sample.get("rule_sentiment") or "unknown" for sample in samples)
    tag_counts = Counter(tag for sample in samples for tag in sample.get("tags", []))
    status = "no_cached_comments" if cached == 0 else "sampled_cached" if cached > safe_limit else "cached_window"
    return {
        "status": "ready" if cached else "empty",
        "method": "rule_v0_cached_comments",
        "source": "vkpi_comments",
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "contract": {"declared": None, "cached": cached, "cap": safe_limit, "status": status},
        "counts": {
            "sampled_comments": len(samples),
            "positive": sentiments.get("positive", 0),
            "neutral": sentiments.get("neutral", 0),
            "negative": sentiments.get("negative", 0),
            "mixed": sentiments.get("mixed", 0),
            "questions": tag_counts.get("question", 0),
            "opportunities": tag_counts.get("opportunity", 0),
            "issues": tag_counts.get("issue", 0),
        },
        "samples": samples,
    }
