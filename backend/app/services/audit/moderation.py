"""
services/audit/moderation.py — 垃圾评论检测
"""
from __future__ import annotations

from app.core.constants import SPAM_COMMENT_KEYWORDS


def check_comment_spam(comments: list) -> dict:
    spam_count = 0
    for c in comments:
        t = (c if isinstance(c, str) else str(c)).lower()
        if any(kw in t for kw in SPAM_COMMENT_KEYWORDS):
            spam_count += 1
    return {"spam_count": spam_count, "total": len(comments)}
