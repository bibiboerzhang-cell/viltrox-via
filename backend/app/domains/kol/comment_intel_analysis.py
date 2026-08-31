"""Pure reducers for the provider-free comment intelligence facade."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
import statistics
from typing import Any


def _purchase_intent(
    rows: list[dict[str, Any]],
    *,
    terms: tuple[str, ...],
    matches_any: Callable[[str, tuple[str, ...]], bool],
    truncate: Callable[[str, int], str],
) -> dict[str, Any]:
    matched = [
        row
        for row in rows
        if matches_any(str(row.get("text") or "").lower(), terms)
    ]
    matched.sort(key=lambda row: int(row.get("like_count") or 0), reverse=True)
    return {
        "count": len(matched),
        "pct": round(100.0 * len(matched) / len(rows), 1),
        "samples": [
            {
                "author_handle": str(row.get("author") or ""),
                "text": truncate(row.get("text"), 120),
                "created_at": str(row.get("created_at") or "")[:19],
            }
            for row in matched[:5]
        ],
    }


def _brand_mentions(
    rows: list[dict[str, Any]],
    *,
    brand_terms: Mapping[str, tuple[str, ...]],
    matches_any: Callable[[str, tuple[str, ...]], bool],
    truncate: Callable[[str, int], str],
) -> list[dict[str, Any]]:
    counts: list[dict[str, Any]] = []
    for brand, aliases in brand_terms.items():
        matched = [
            row
            for row in rows
            if matches_any(str(row.get("text") or "").lower(), aliases)
        ]
        if matched:
            matched.sort(key=lambda row: int(row.get("like_count") or 0), reverse=True)
            counts.append(
                {
                    "brand": brand,
                    "count": len(matched),
                    "samples": [
                        {
                            "author_handle": str(row.get("author") or ""),
                            "text": truncate(row.get("text"), 120),
                        }
                        for row in matched[:2]
                    ],
                }
            )
    counts.sort(key=lambda item: (-item["count"], item["brand"]))
    return counts


def _active_hours(
    rows: list[dict[str, Any]],
    *,
    parse_hour: Callable[[Any], int | None],
) -> dict[str, Any]:
    hist = [0] * 24
    timed = 0
    for row in rows:
        hour = parse_hour(row.get("created_at"))
        if hour is not None:
            hist[hour] += 1
            timed += 1
    top_hours = [
        hour
        for hour, _count in sorted(enumerate(hist), key=lambda pair: (-pair[1], pair[0]))[:3]
        if hist[hour] > 0
    ]
    suggestion = ""
    if top_hours:
        lead = top_hours[0]
        suggestion = f"UTC {lead:02d}-{(lead + 2) % 24:02d}时"
    return {
        "hist": hist,
        "timed_n": timed,
        "top_hours": top_hours,
        "peak_hour_comment_count": hist[top_hours[0]] if top_hours else 0,
        "suggestion": suggestion,
    }


def _engagement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    video_keys = {
        str(row.get("video_key"))
        for row in rows
        if row.get("video_key") not in (None, "")
    }
    replies = sum(1 for row in rows if bool(row.get("is_reply")))
    likes = [int(row.get("like_count") or 0) for row in rows]
    return {
        "comments_per_video": round(len(rows) / len(video_keys), 1) if video_keys else None,
        "video_n": len(video_keys),
        "reply_pct": round(100.0 * replies / len(rows), 1),
        "likes_median": int(statistics.median(likes)) if likes else 0,
    }


def _superfans(
    rows: list[dict[str, Any]],
    *,
    truncate: Callable[[str, int], str],
) -> list[dict[str, Any]]:
    author_counter: Counter[str] = Counter(
        str(row.get("author") or "").strip()
        for row in rows
        if str(row.get("author") or "").strip()
    )
    sample_by_author: dict[str, str] = {}
    for row in rows:
        author = str(row.get("author") or "").strip()
        if author and author not in sample_by_author:
            sample_by_author[author] = truncate(row.get("text"), 60)
    return [
        {"handle": author, "count": count, "sample": sample_by_author.get(author, "")}
        for author, count in author_counter.most_common(10)
        if count >= 2
    ]


def analyze_comments(
    comments: list[dict[str, Any]],
    *,
    purchase_terms: tuple[str, ...],
    brand_terms: Mapping[str, tuple[str, ...]],
    matches_any: Callable[[str, tuple[str, ...]], bool],
    truncate: Callable[[str, int], str],
    parse_hour: Callable[[Any], int | None],
) -> dict[str, Any]:
    rows = [
        comment
        for comment in (comments or [])
        if str((comment or {}).get("text") or "").strip()
    ]
    if not rows:
        return {"sample_size": 0, "note": "无评论可分析(诚实空,不编造)"}
    return {
        "sample_size": len(rows),
        "purchase_intent": _purchase_intent(
            rows, terms=purchase_terms, matches_any=matches_any, truncate=truncate
        ),
        "brand_mentions": _brand_mentions(
            rows, brand_terms=brand_terms, matches_any=matches_any, truncate=truncate
        ),
        "active_hours": _active_hours(rows, parse_hour=parse_hour),
        "engagement": _engagement(rows),
        "superfans": _superfans(rows, truncate=truncate),
        "method": "comment_intel_v1",
        "note": "评论集统计(词表/直方),零 LLM 零外调",
    }
