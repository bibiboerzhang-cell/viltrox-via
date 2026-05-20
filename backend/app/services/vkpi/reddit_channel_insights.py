"""Reddit-specific account assessment for employee channels."""
from __future__ import annotations

from typing import Any

from app.services.vkpi import channels


def _reddit_score(post: dict[str, Any]) -> tuple[int, str, str]:
    likes = channels._int(post.get("score"), channels._int(post.get("likes")))
    comments = channels._int(post.get("comments"))
    ratio = channels._float(post.get("upvote_ratio"))
    title = channels._text(post.get("title")).lower()
    score = likes + comments * 2
    if ratio >= 0.85:
        score += 50
    if any(term in title for term in ["viltrox", "35mm", "27mm", "56mm", "z1", "lab", "lens", "af "]):
        score += 100
    if any(term in title for term in ["great", "love", "best", "recommend", "sharp", "good"]):
        score += 30
    if channels._bool(post.get("is_removed")) or any(term in title for term in ["deleted", "removed"]):
        score -= 9999
    if ratio and ratio < 0.4:
        return score, "negative", "负面"
    if score > 500:
        return score, "excellent", "优质"
    if score >= 200:
        return score, "good", "良好"
    if score >= 50:
        return score, "neutral", "平庸"
    return score, "cold", "冷淡"


def reddit_channel_assessment(channel_id: int, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    row = channels._latest_channel_row(channel_id, staff=staff)
    platform = str(row.get("platform") or "").lower()
    if platform != "reddit":
        raise ValueError("reddit assessment only supports reddit channels")
    record_posts, source, package_dir = channels._all_posts_for_channel(row)
    record_posts = channels._sort_posts(record_posts, "latest", "desc")
    posts = channels._filter_posts_by_window(record_posts, "year")
    analysis_window = "year"
    if not posts:
        posts = record_posts
        analysis_window = "all_records_fallback"
    distribution = {
        "excellent": {"key": "excellent", "label": "优质", "count": 0},
        "good": {"key": "good", "label": "良好", "count": 0},
        "neutral": {"key": "neutral", "label": "平庸", "count": 0},
        "cold": {"key": "cold", "label": "冷淡", "count": 0},
        "negative": {"key": "negative", "label": "负面", "count": 0},
    }
    items: list[dict[str, Any]] = []
    for post in posts:
        score, category, label = _reddit_score(post)
        distribution[category]["count"] += 1
        items.append(
            {
                **post,
                "assessment_score": score,
                "assessment_category": category,
                "assessment_label": label,
                "upvote_ratio": channels._float(post.get("upvote_ratio")),
                "score": channels._int(post.get("score"), channels._int(post.get("likes"))),
            }
        )
    quality = sorted(
        [item for item in items if item["assessment_category"] in {"excellent", "good"}],
        key=lambda item: (channels._int(item.get("assessment_score")), channels._int(item.get("comments")), channels._int(item.get("likes"))),
        reverse=True,
    )
    attention = sorted(
        [item for item in items if item["assessment_category"] in {"negative", "cold"}],
        key=lambda item: (channels._int(item.get("comments")), channels._int(item.get("likes"))),
        reverse=True,
    )
    return {
        "channel_id": int(channel_id),
        "source": source,
        "package_dir": package_dir,
        "account": {
            "id": int(row.get("id") or 0),
            "platform": platform,
            "handle": channels._text(row.get("account_handle")),
            "display_name": channels._account_name(row),
            "account_url": channels._account_url(row),
            "subscribers": channels._int(row.get("metric_followers")),
            "posts_count": channels._int(row.get("metric_posts"), len(posts)),
            "last_sync_at": channels._text(row.get("metric_captured_at"), row.get("last_sync_at")),
        },
        "summary": {
            "posts": len(posts),
            "records_total": len(record_posts),
            "analysis_window": analysis_window,
            "comments": sum(channels._int(item.get("comments")) for item in posts),
            "record_comments": sum(channels._int(item.get("comments")) for item in record_posts),
            "score": sum(channels._int(item.get("score"), channels._int(item.get("likes"))) for item in posts),
            "quality_count": len(quality),
            "attention_count": len(attention),
        },
        "distribution": list(distribution.values()),
        "latest_quality": quality[:5],
        "needs_attention": attention[:5],
        "items": items[:100],
    }
