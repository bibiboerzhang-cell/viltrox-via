from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from app.services.deepsight.constants import (
    CRISIS_LEXICON,
    NEGATIVE_LEXICON,
    POSITIVE_LEXICON,
    PURCHASE_LEXICON,
)

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]+|[\u4e00-\u9fff]{2,}")


def _normalize_comments(items: Iterable[str | dict]) -> list[str]:
    out: list[str] = []
    for item in items or []:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("comment") or item.get("body") or "")
        else:
            text = str(item or "")
        text = text.strip()
        if text:
            out.append(text[:500])
    return out


def _extract_keywords(comments: list[str]) -> list[str]:
    stop = {"viltrox", "lens", "camera", "video", "content", "这是", "这个", "感觉", "我们", "他们", "一个", "还是"}
    tokens = []
    for c in comments:
        tokens.extend([t.lower() for t in TOKEN_RE.findall(c) if len(t) >= 2])
    counter = Counter(t for t in tokens if t not in stop)
    return [w for w, _ in counter.most_common(12)]


def analyze_comments(raw_comments: Iterable[str | dict]) -> dict:
    comments = _normalize_comments(raw_comments)
    if not comments:
        return {
            "sample_size": 0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "purchase_intent_ratio": 0.0,
            "crisis_ratio": 0.0,
            "positive_keywords": [],
            "negative_keywords": [],
            "purchase_keywords": [],
            "top_keywords": [],
            "sample_comments": [],
        }

    pos_hits = Counter()
    neg_hits = Counter()
    buy_hits = Counter()
    crisis_hits = Counter()

    pos_count = neg_count = buy_count = crisis_count = 0
    for c in comments:
        low = c.lower()
        found_pos = [w for w in POSITIVE_LEXICON if w in low]
        found_neg = [w for w in NEGATIVE_LEXICON if w in low]
        found_buy = [w for w in PURCHASE_LEXICON if w in low]
        found_crisis = [w for w in CRISIS_LEXICON if w in low]
        if found_pos:
            pos_count += 1
            pos_hits.update(found_pos)
        if found_neg:
            neg_count += 1
            neg_hits.update(found_neg)
        if found_buy:
            buy_count += 1
            buy_hits.update(found_buy)
        if found_crisis:
            crisis_count += 1
            crisis_hits.update(found_crisis)

    total = max(1, len(comments))
    return {
        "sample_size": len(comments),
        "positive_ratio": round(pos_count / total, 4),
        "negative_ratio": round(neg_count / total, 4),
        "purchase_intent_ratio": round(buy_count / total, 4),
        "crisis_ratio": round(crisis_count / total, 4),
        "positive_keywords": [k for k, _ in pos_hits.most_common(8)],
        "negative_keywords": [k for k, _ in neg_hits.most_common(8)],
        "purchase_keywords": [k for k, _ in buy_hits.most_common(8)],
        "crisis_keywords": [k for k, _ in crisis_hits.most_common(8)],
        "top_keywords": _extract_keywords(comments),
        "sample_comments": comments[:12],
    }
