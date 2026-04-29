from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.connection import get_conn
from app.services.deepsight.comment_nlp import analyze_comments
from app.services.deepsight.competitor_radar import compute_competitor_radar
from app.services.deepsight.visual_life_scoring import compute_visual_life_score


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_json(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _parse_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        parts = value.replace("|", ",").replace("/", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    return []


def compute_campaign_like_score(item: dict) -> tuple[int, int, int]:
    detected = bool(item.get("product_label") or item.get("product_series") or item.get("viltrox_lens") or "viltrox" in (item.get("title") or "").lower())
    cts = item.get("content_types") or []
    type_bonus = min(50, len(cts) * 12)
    content_score = min(100, 20 + type_bonus + 30) if detected else 0
    weight = (float(item.get("views") or 0) / 1000) + float(item.get("likes") or 0) + float(item.get("comments") or 0) * 6 + float(item.get("shares") or 0) * 10 + float(item.get("favorites") or 0) * 8
    interaction_score = min(250, int(weight / 5))
    final_score = min(400, 50 + content_score + interaction_score) if detected else 0
    return final_score, content_score, interaction_score


def fetch_submissions_window(days: int, platforms: list[str] | None = None) -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM submissions ORDER BY id DESC").fetchall()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    platform_set = {p.lower() for p in (platforms or []) if p}
    for row in rows:
        item = dict(row)
        if platform_set and str(item.get("platform") or "").lower() not in platform_set:
            continue
        created = _parse_dt(item.get("created_at"))
        if created and created < cutoff:
            continue
        va = _safe_json(item.get("video_analysis"))
        quality_scores = va.get("quality_scores") or {}
        comments = va.get("visible_comments") or []
        content_types = _parse_list(va.get("content_types") or item.get("content_types"))
        handle = str(item.get("extracted_handle") or "").strip()
        channel = handle.lstrip("@") if handle else ""
        competitor_brands = _parse_list(va.get("competitor_brands"))
        competitor_products = _parse_list(va.get("competitor_products"))
        comment_insight = analyze_comments(comments)
        score, content_score, interaction_score = compute_campaign_like_score({
            **item,
            "product_label": item.get("product_label"),
            "product_series": item.get("product_series"),
            "viltrox_lens": va.get("viltrox_lens"),
            "content_types": content_types,
            "views": item.get("views"),
            "likes": item.get("likes"),
            "comments": item.get("comments"),
            "shares": item.get("shares"),
            "favorites": item.get("favorites"),
            "title": item.get("title"),
        })
        eng = 0.0
        views = float(item.get("views") or 0)
        if views > 0:
            eng = (float(item.get("likes") or 0) + float(item.get("comments") or 0) + float(item.get("shares") or 0) + float(item.get("favorites") or 0)) / views
        normalized = {
            "id": item.get("id"),
            "created_at": item.get("created_at"),
            "platform": item.get("platform") or "unknown",
            "handle": handle,
            "channel": channel,
            "title": item.get("title") or "Untitled",
            "url": item.get("url") or "",
            "product_series": item.get("product_series") or "",
            "product_label": item.get("product_label") or va.get("viltrox_lens") or "",
            "content_types": content_types,
            "content_topic": va.get("content_topic") or item.get("content_genre") or "",
            "content_summary": va.get("content_summary") or item.get("recommendation") or "",
            "quality_scores": quality_scores,
            "quality_overall": va.get("quality_overall") or item.get("final_score") or 0,
            "views": int(item.get("views") or 0),
            "likes": int(item.get("likes") or 0),
            "comments": int(item.get("comments") or 0),
            "shares": int(item.get("shares") or 0),
            "favorites": int(item.get("favorites") or 0),
            "engagement_rate": round(eng, 4),
            "campaign_score": score,
            "content_score": content_score,
            "interaction_score": interaction_score,
            "risk_score": int(item.get("risk_score") or 0),
            "tech_score": float(item.get("tech_score") or 0),
            "marketing_score": float(item.get("marketing_score") or 0),
            "brand_exposure_score": float(item.get("brand_exposure_score") or 0),
            "product_showcase_score": float(item.get("product_showcase_score") or 0),
            "storytelling_score": float(item.get("storytelling_score") or 0),
            "detection_status": item.get("detection_status") or "unknown",
            "competitor_brands": competitor_brands,
            "competitor_products": competitor_products,
            "comment_analysis": comment_insight,
            "visible_comments": comment_insight.get("sample_comments", []),
            "viltrox_lens": va.get("viltrox_lens"),
            "other_lens": va.get("other_lens"),
            "camera_body": va.get("camera_body"),
            "brand_elements": _parse_list(va.get("brand_elements")),
            "reference_reasons": _parse_list(va.get("reference_reasons")),
            "improvements": va.get("improvements") or [],
            "timestamps": va.get("timestamps") or [],
        }
        normalized.update(compute_visual_life_score({**normalized, **comment_insight}))
        out.append(normalized)
    return out


def fetch_previous_submissions_window(days: int, previous_days: int, platforms: list[str] | None = None) -> list[dict]:
    all_rows = fetch_submissions_window(days + previous_days + 7, platforms=platforms)
    now = datetime.now(timezone.utc)
    start_current = now - timedelta(days=days)
    start_previous = start_current - timedelta(days=previous_days)
    out = []
    for item in all_rows:
        created = _parse_dt(item.get("created_at"))
        if not created:
            continue
        if start_previous <= created < start_current:
            out.append(item)
    return out


def summary_from_items(items: list[dict]) -> dict:
    total = len(items)
    return {
        "posts_total": total,
        "views_total": sum(x.get("views", 0) for x in items),
        "likes_total": sum(x.get("likes", 0) for x in items),
        "comments_total": sum(x.get("comments", 0) for x in items),
        "shares_total": sum(x.get("shares", 0) for x in items),
        "avg_campaign_score": round(sum(x.get("campaign_score", 0) for x in items) / max(1, total), 1),
        "review_ratio": round(sum(1 for x in items if x.get("risk_score", 0) >= 30 or x.get("detection_status") == "suspected") / max(1, total), 4),
        "accounts_total": len({(x.get("platform"), x.get("handle") or x.get("channel")) for x in items}),
        "platforms_total": len({x.get("platform") for x in items}),
    }


def compute_platform_breakdown(current: list[dict], previous: list[dict]) -> list[dict]:
    cur_map: dict[str, list[dict]] = defaultdict(list)
    prev_map: dict[str, list[dict]] = defaultdict(list)
    for item in current:
        cur_map[item["platform"]].append(item)
    for item in previous:
        prev_map[item["platform"]].append(item)

    out = []
    for platform, items in cur_map.items():
        prev_items = prev_map.get(platform, [])
        posts = len(items)
        views = sum(x["views"] for x in items)
        likes = sum(x["likes"] for x in items)
        comments = sum(x["comments"] for x in items)
        avg_views = views / max(1, posts)
        prev_views = sum(x["views"] for x in prev_items)
        prev_posts = len(prev_items)
        wow_views_change = 0.0 if prev_views <= 0 else (views - prev_views) / prev_views
        wow_post_change = 0.0 if prev_posts <= 0 else (posts - prev_posts) / prev_posts
        engagement = (likes + comments) / max(1, views)
        flag = "stable"
        if avg_views >= 5000 and wow_views_change >= 0.15:
            flag = "strong"
        elif wow_views_change <= -0.2:
            flag = "weak"
        out.append({
            "platform": platform,
            "accounts": len({x.get('handle') or x.get('channel') for x in items}),
            "posts": posts,
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement_rate": round(engagement, 4),
            "wow_views_change": round(wow_views_change, 4),
            "wow_post_change": round(wow_post_change, 4),
            "avg_views_per_post": round(avg_views, 2),
            "diagnostic_flag": flag,
        })
    return sorted(out, key=lambda x: x["views"], reverse=True)


def compute_account_breakdown(current: list[dict], previous: list[dict]) -> list[dict]:
    cur_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    prev_map: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in current:
        cur_map[(item["platform"], item.get("handle") or item.get("channel") or f"sub-{item['id']}")].append(item)
    for item in previous:
        prev_map[(item["platform"], item.get("handle") or item.get("channel") or f"sub-{item['id']}")].append(item)
    out = []
    for (platform, handle), items in cur_map.items():
        prev_items = prev_map.get((platform, handle), [])
        posts = len(items)
        views = sum(x["views"] for x in items)
        interactions = sum(x["likes"] + x["comments"] + x["shares"] + x["favorites"] for x in items)
        avg_views = views / max(1, posts)
        avg_eng = interactions / max(1, posts)
        prev_avg_views = (sum(x["views"] for x in prev_items) / max(1, len(prev_items))) if prev_items else 0.0
        wow = 0.0 if prev_avg_views <= 0 else (avg_views - prev_avg_views) / prev_avg_views
        type_counter = defaultdict(int)
        for item in items:
            for ct in item.get("content_types", []):
                type_counter[ct] += 1
        neg_ratio = round(sum(x.get("comment_analysis", {}).get("negative_ratio", 0) for x in items) / max(1, posts), 4)
        weak_type = min(type_counter.items(), key=lambda kv: kv[1])[0] if type_counter else None
        top_type = max(type_counter.items(), key=lambda kv: kv[1])[0] if type_counter else None
        out.append({
            "platform": platform,
            "handle": handle,
            "posts": posts,
            "avg_views": round(avg_views, 2),
            "avg_engagement": round(avg_eng, 2),
            "wow_change": round(wow, 4),
            "top_post_type": top_type,
            "weak_post_type": weak_type,
            "negative_comment_ratio": neg_ratio,
            "efficiency_score": round((avg_views * 0.6) + (avg_eng * 0.4), 2),
        })
    return sorted(out, key=lambda x: x["efficiency_score"], reverse=True)


def compute_product_breakdown(current: list[dict], previous: list[dict]) -> list[dict]:
    cur_map: dict[str, list[dict]] = defaultdict(list)
    prev_map: dict[str, list[dict]] = defaultdict(list)
    for item in current:
        key = item.get("product_label") or item.get("viltrox_lens") or item.get("product_series") or "unknown"
        cur_map[key].append(item)
    for item in previous:
        key = item.get("product_label") or item.get("viltrox_lens") or item.get("product_series") or "unknown"
        prev_map[key].append(item)
    out = []
    for product, items in cur_map.items():
        prev_items = prev_map.get(product, [])
        mentions = len(items)
        prev_mentions = len(prev_items)
        wow = 0.0 if prev_mentions <= 0 else (mentions - prev_mentions) / prev_mentions
        pos = round(sum(x.get("comment_analysis", {}).get("positive_ratio", 0) for x in items) / max(1, mentions), 4)
        neg = round(sum(x.get("comment_analysis", {}).get("negative_ratio", 0) for x in items) / max(1, mentions), 4)
        topic_counter = defaultdict(int)
        for item in items:
            for ct in item.get("content_types", []):
                topic_counter[ct] += 1
        out.append({
            "product": product,
            "mentions": mentions,
            "official_posts": 0,
            "ugc_posts": mentions,
            "wow_mentions_change": round(wow, 4),
            "positive_ratio": pos,
            "negative_ratio": neg,
            "top_topics": [k for k, _ in sorted(topic_counter.items(), key=lambda kv: kv[1], reverse=True)[:5]],
            "avg_campaign_score": round(sum(x.get("campaign_score", 0) for x in items) / max(1, mentions), 1),
        })
    return sorted(out, key=lambda x: x["mentions"], reverse=True)


def merge_comment_analysis(items: list[dict]) -> dict:
    all_comments: list[str] = []
    for item in items:
        all_comments.extend(item.get("visible_comments", []))
    return analyze_comments(all_comments)


def platform_coverage(items: list[dict]) -> float:
    expected = {"instagram", "youtube", "tiktok", "facebook"}
    got = {str(x.get("platform") or "").lower() for x in items}
    return len(got & expected) / len(expected)


def comment_coverage(items: list[dict]) -> float:
    with_comments = sum(1 for x in items if x.get("comment_analysis", {}).get("sample_size", 0) > 0)
    return with_comments / max(1, len(items))


def confidence_score(items: list[dict]) -> float:
    base = min(1.0, len(items) / 50)
    return round((base * 0.5) + (platform_coverage(items) * 0.25) + (comment_coverage(items) * 0.25), 4)


def compute_facts(current: list[dict], previous: list[dict]) -> dict:
    return {
        "summary": summary_from_items(current),
        "platform_breakdown": compute_platform_breakdown(current, previous),
        "account_breakdown": compute_account_breakdown(current, previous),
        "product_breakdown": compute_product_breakdown(current, previous),
        "comment_analysis": merge_comment_analysis(current),
        "competitor_signals": compute_competitor_radar(current),
    }
