"""
services/intelligence/lens_monitor.py — 单镜头市场监控
==========================================================
核心: 输入一个镜头名, 抓 YouTube 所有相关视频, Claude 分类到 6 类.

分类:
  1. reviews        — 测评/评测视频
  2. use_case       — 实拍/作品/用这镜头拍东西
  3. ads            — 官方广告/品牌投放
  4. kol            — 红人带货/推广合作
  5. viral          — 爆火视频 (views 显著高于 median)
  6. mention        — 一般提及/非专门讨论

时间分布:
  - today (过去 1 天)
  - this_week (过去 7 天)
  - this_month (过去 30 天)
  - older (30 天以上)

Claude 洞察:
  - 核心总结
  - 热门话题 (大家在讨论什么)
  - 突出创作者
  - 内容机会点

成本: ~$0.10 / 次 ($0.05 Apify + $0.05 Claude)
用时: 30-60 秒
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.platform.apify_budget import call_apify_actor
from app.platform.apify_lifecycle import register_apify_client_shutdown
from app.services.ai.retry import call_ai_with_retry
from app.services.intelligence.account_scan_service import search_platform_content

logger = get_logger(__name__)

# Apify
try:
    from apify_client import ApifyClient
    _APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
    _apify: Optional[ApifyClient] = (
        register_apify_client_shutdown(ApifyClient(_APIFY_TOKEN)) if _APIFY_TOKEN else None
    )
except ImportError:
    _apify = None

# Claude
try:
    from app.services.ai.clients.claude_client import get_claude_client
    _claude_available = True
except ImportError:
    _claude_available = False
    def get_claude_client():
        return None


# ──────────────────────────────────────────────
# 平台搜索
# ──────────────────────────────────────────────

async def search_youtube(query: str, max_results: int = 50) -> list[dict]:
    """
    用 streamers/youtube-scraper 搜索 YouTube.
    
    Returns:
        list of video dicts
    """
    if _apify is None:
        logger.warning("lens_monitor.apify_unavailable")
        return []
    
    logger.info("lens_monitor.youtube_search_started", extra={"query": query, "max_results": max_results})
    t0 = time.time()
    
    try:
        def _do():
            run_input = {
                "searchQueries": [query],
                "maxResults": max_results,
                "maxResultsShorts": 0,
                "maxResultStreams": 0,
            }
            run = call_apify_actor(
                _apify,
                "streamers/youtube-scraper",
                platform="youtube",
                operation="lens_monitor_search",
                source="intelligence.lens_monitor",
                run_input=run_input,
                timeout_secs=240,
            )
            found = list(_apify.dataset(run["defaultDatasetId"]).iterate_items())
            # C5 成本记账收口:镜头监控搜索 run 统一记账(幂等 by run_id;失败绝不影响搜索)。
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    run,
                    actor_id="streamers/youtube-scraper",
                    platform="youtube",
                    operation="lens_monitor_search",
                    source="intelligence.lens_monitor",
                    dataset_item_count=len(found),
                )
            except Exception:
                logger.warning("lens_monitor.cost_record_failed", exc_info=True)
            return found
        
        items = await asyncio.to_thread(_do)
        elapsed = time.time() - t0
        logger.info("lens_monitor.youtube_search_complete", extra={"item_count": len(items), "elapsed_sec": round(elapsed, 1)})
        
        # 标准化
        videos = []
        for i, item in enumerate(items):
            videos.append({
                "idx":          i,
                "title":        str(item.get("title", "") or "")[:250],
                "views":        int(item.get("viewCount", 0) or 0),
                "likes":        int(item.get("likes", 0) or 0),
                "comments":     int(item.get("commentsCount", 0) or 0),
                "duration":     str(item.get("duration", "") or ""),
                "channel":      str(item.get("channelName", "") or "")[:100],
                "channel_url":  str(item.get("channelUrl", "") or ""),
                "url":          str(item.get("url", "") or ""),
                "published":    str(item.get("date", "") or ""),
                "description":  str(item.get("text", "") or "")[:400],
                "thumbnail":    str(item.get("thumbnailUrl", "") or ""),
            })
        return videos
    
    except Exception as e:
        logger.warning("lens_monitor.youtube_search_failed", extra={"error": str(e)})
        return []


async def search_market_videos(query: str, max_results: int = 50, platform: str = "youtube", market: str = "") -> dict:
    """Search selected platform and return normalized videos plus provider status."""
    normalized_platform = (platform or "youtube").strip().lower()
    normalized_market = (market or "").strip().upper()
    if normalized_platform == "youtube":
        search_query = f"{query} {normalized_market}".strip() if normalized_market and normalized_market not in {"ALL", "GLOBAL"} else query
        videos = await search_youtube(search_query, max_results)
        return {
            "status": "done" if videos else ("provider_unavailable" if _apify is None else "empty"),
            "platform": "youtube",
            "market": normalized_market,
            "videos": videos,
        }
    result = await search_platform_content(normalized_platform, query, market=normalized_market, max_results=max_results)
    videos = []
    for index, item in enumerate(result.get("items", [])):
        videos.append(
            {
                "idx": index,
                "title": str(item.get("sample_title", "") or "")[:250],
                "views": int(item.get("views", 0) or 0),
                "likes": int(item.get("likes", 0) or 0),
                "comments": int(item.get("comments", 0) or 0),
                "duration": "",
                "channel": str(item.get("channel_name", "") or "")[:100],
                "channel_url": str(item.get("channel_url", "") or ""),
                "url": str(item.get("source_url", "") or ""),
                "published": str(item.get("published", "") or ""),
                "description": "",
                "thumbnail": "",
                "platform": normalized_platform,
            }
        )
    return {
        "status": result.get("status", "done"),
        "platform": normalized_platform,
        "market": normalized_market,
        "videos": videos,
        "message": result.get("message", ""),
        "metadata": result.get("metadata", {}),
    }


# ──────────────────────────────────────────────
# 时间分布
# ──────────────────────────────────────────────

_RELATIVE_PATTERNS = [
    (re.compile(r"(\d+)\s*hour", re.I), "hours"),
    (re.compile(r"(\d+)\s*day", re.I), "days"),
    (re.compile(r"(\d+)\s*week", re.I), "weeks"),
    (re.compile(r"(\d+)\s*month", re.I), "months"),
    (re.compile(r"(\d+)\s*year", re.I), "years"),
]


def parse_relative_time(s: str) -> Optional[datetime]:
    """
    解析 YouTube 的时间字符串.
    支持: "3 days ago", "1 week ago", "2 months ago", ISO 格式等.
    """
    if not s:
        return None
    
    s = s.strip()
    now = datetime.now(timezone.utc)
    
    # 先试 ISO 格式
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception as exc:
        logger.debug("lens monitor date parse failed", extra={"value": s, "error": str(exc)})
    
    # 相对时间
    for pat, unit in _RELATIVE_PATTERNS:
        m = pat.search(s)
        if m:
            n = int(m.group(1))
            if unit == "hours":
                return now - timedelta(hours=n)
            elif unit == "days":
                return now - timedelta(days=n)
            elif unit == "weeks":
                return now - timedelta(weeks=n)
            elif unit == "months":
                return now - timedelta(days=n * 30)
            elif unit == "years":
                return now - timedelta(days=n * 365)
    
    return None


def compute_time_distribution(videos: list[dict]) -> dict:
    """按时间 bucket 分组"""
    now = datetime.now(timezone.utc)
    day_1 = now - timedelta(days=1)
    day_7 = now - timedelta(days=7)
    day_30 = now - timedelta(days=30)
    
    buckets = {
        "today": [],
        "this_week": [],
        "this_month": [],
        "older": [],
        "unknown": [],
    }
    
    for v in videos:
        dt = parse_relative_time(v.get("published", ""))
        if dt is None:
            buckets["unknown"].append(v["idx"])
            continue
        
        v["parsed_time"] = dt.isoformat()
        
        if dt >= day_1:
            buckets["today"].append(v["idx"])
        elif dt >= day_7:
            buckets["this_week"].append(v["idx"])
        elif dt >= day_30:
            buckets["this_month"].append(v["idx"])
        else:
            buckets["older"].append(v["idx"])
    
    return {
        "today":       len(buckets["today"]),
        "this_week":   len(buckets["this_week"]),
        "this_month":  len(buckets["this_month"]),
        "older":       len(buckets["older"]),
        "unknown":     len(buckets["unknown"]),
    }


def compute_hourly_distribution(videos: list[dict]) -> dict:
    """Hour-of-day distribution for best posting-time guidance."""
    hourly = {str(hour).zfill(2): {"count": 0, "views": 0} for hour in range(24)}
    weekday = {"weekday": {"count": 0, "views": 0}, "weekend": {"count": 0, "views": 0}}
    best_hour = {"hour": "", "views": -1, "count": 0}
    for v in videos:
        dt = parse_relative_time(v.get("published", ""))
        if dt is None:
            continue
        hour_key = str(dt.hour).zfill(2)
        views = int(v.get("views") or 0)
        hourly[hour_key]["count"] += 1
        hourly[hour_key]["views"] += views
        day_key = "weekend" if dt.weekday() >= 5 else "weekday"
        weekday[day_key]["count"] += 1
        weekday[day_key]["views"] += views
        if hourly[hour_key]["views"] > best_hour["views"]:
            best_hour = {"hour": hour_key, "views": hourly[hour_key]["views"], "count": hourly[hour_key]["count"]}
    if not best_hour["hour"]:
        recommendation = "Not enough publish-time data"
    else:
        start = int(best_hour["hour"])
        recommendation = f"Best observed posting window: {start:02d}:00-{(start + 2) % 24:02d}:00 UTC"
    return {
        "by_hour_utc": hourly,
        "weekday_vs_weekend": weekday,
        "best_window": best_hour,
        "recommendation": recommendation,
    }


def filter_videos_by_date(videos: list[dict], date_from: str = "", date_to: str = "") -> list[dict]:
    """Filter normalized videos by publish date if parseable."""
    if not date_from and not date_to:
        return videos
    lower = None
    upper = None
    try:
        if date_from:
            lower = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            if lower.tzinfo is None:
                lower = lower.replace(tzinfo=timezone.utc)
        if date_to:
            upper = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
            if upper.tzinfo is None:
                upper = upper.replace(tzinfo=timezone.utc)
            upper = upper + timedelta(days=1)
    except Exception:
        return videos
    filtered: list[dict] = []
    for video in videos:
        dt = parse_relative_time(video.get("published", ""))
        if dt is None:
            continue
        if lower and dt < lower:
            continue
        if upper and dt >= upper:
            continue
        filtered.append(video)
    return filtered


# ──────────────────────────────────────────────
# Viral 检测
# ──────────────────────────────────────────────

def detect_viral_videos(videos: list[dict], multiplier: float = 3.0) -> list[int]:
    """
    检测爆火视频: views > median * multiplier
    """
    if not videos:
        return []
    
    views = sorted(v["views"] for v in videos)
    median = views[len(views) // 2] if views else 0
    threshold = max(median * multiplier, 10000)  # 至少 10K views 才算爆火
    
    return [v["idx"] for v in videos if v["views"] >= threshold]


# ──────────────────────────────────────────────
# Claude 分类
# ──────────────────────────────────────────────

CLASSIFY_PROMPT = """You are a brand intelligence analyst for Viltrox camera lens company.

I'll give you a list of YouTube videos related to a lens query. Classify each video into ONE of these categories:

1. "reviews"   — Lens review/test/comparison videos (dedicated review content)
2. "use_case"  — Videos USING the lens to shoot real content (portraits, cinematic footage, etc.)
3. "ads"       — Official brand ads, sponsored promos, press releases
4. "kol"       — KOL/influencer paid promotion or affiliate content (clearly sponsored/gifted)
5. "mention"   — Casual mention, included in larger "best lenses" list, brief reference

(Note: "viral" is computed separately based on view counts, not by you.)

Also provide overall insights about the market.

════════════════════════════════════════
QUERY: "{query}"
════════════════════════════════════════

VIDEOS ({count} total):

{videos_text}

════════════════════════════════════════

Return ONLY valid JSON (no markdown, no prose) with this exact structure:

{{
  "classifications": [
    {{"idx": 0, "category": "reviews"}},
    {{"idx": 1, "category": "use_case"}},
    ...every video...
  ],
  "summary": "2-3 sentence market summary: how many videos, total reach, main content types",
  "trending_topics": ["top 5 discussion topics people are talking about"],
  "notable_creators": [
    {{"channel": "creator name", "reason": "why they're notable"}},
    ...up to 5...
  ],
  "opportunities": [
    "3-5 content/marketing opportunities based on gaps in the existing content"
  ],
  "sentiment": "positive|mixed|negative",
  "viltrox_angle": "If this is a Viltrox product: what's the honest picture? If it's a competitor: how does Viltrox compare?"
}}

Be specific, factual, and actionable.
"""


def classify_videos_with_claude(query: str, videos: list[dict]) -> dict:
    """调 Claude 对所有视频做分类 + 生成洞察"""
    if not _claude_available:
        return {"error": "Claude not available"}
    
    client = get_claude_client()
    if client is None:
        return {"error": "Claude client not initialized"}
    
    if not videos:
        return {"error": "No videos to classify"}
    
    # 构建视频列表文本 (短, 只给 Claude 需要的)
    lines = []
    for v in videos[:50]:
        lines.append(
            f"[{v['idx']}] {v['title'][:120]}\n"
            f"    by {v['channel'][:60]} | {v['views']:,} views | {v['published']}\n"
            f"    {v['description'][:150]}"
        )
    videos_text = "\n\n".join(lines)
    
    prompt = CLASSIFY_PROMPT.format(
        query=query,
        count=len(videos),
        videos_text=videos_text,
    )
    
    try:
        logger.info("lens_monitor.claude_started", extra={"video_count": len(videos)})
        t0 = time.time()
        resp = call_ai_with_retry(
            "lens_monitor.claude",
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        elapsed = time.time() - t0
        
        text = resp.content[0].text if resp.content else ""
        logger.info("lens_monitor.claude_complete", extra={"elapsed_sec": round(elapsed, 1), "char_count": len(text)})
        
        # 清理 markdown
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("lens_monitor.claude_json_parse_failed", extra={"error": str(e)})
            return {"error": f"Claude JSON parse failed: {e}", "raw": text[:500]}
    
    except Exception as e:
        logger.warning("lens_monitor.claude_failed", extra={"error": str(e)})
        return {"error": str(e)}


# ──────────────────────────────────────────────
# 主入口 — monitor_lens_market
# ──────────────────────────────────────────────

async def monitor_lens_market(
    query: str,
    max_videos: int = 50,
    *,
    platform: str = "youtube",
    market: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """
    完整的单镜头市场监控.
    
    Args:
        query: 搜索关键词 (例 "viltrox 16mm f1.8")
        max_videos: 最多抓多少 YouTube 视频
    
    Returns:
        完整的 monitor 数据 (见函数末尾)
    """
    t0 = time.time()
    logger.info(
        "lens_monitor.run_started",
        extra={"query": query, "max_videos": max_videos, "platform": platform, "market": market},
    )
    
    # 1. 平台搜索
    search_result = await search_market_videos(query, max_videos, platform=platform, market=market)
    videos = filter_videos_by_date(search_result.get("videos", []), date_from=date_from, date_to=date_to)
    
    if not videos:
        return {
            "query": query,
            "platform": search_result.get("platform", platform),
            "market": (market or "").strip().upper(),
            "error": search_result.get("message") or "No videos found for selected platform/market/time range",
            "overview": {"total_videos": 0},
            "time_distribution": {},
            "hourly_distribution": {},
            "categories": {},
            "claude_insights": {},
            "metadata": {
                "duration_sec": round(time.time() - t0, 1),
                "provider_status": search_result.get("status"),
                "max_videos": max_videos,
                "date_from": date_from,
                "date_to": date_to,
            },
        }
    
    # 2. 时间分布
    time_dist = compute_time_distribution(videos)
    hourly_dist = compute_hourly_distribution(videos)
    
    # 3. Viral 检测
    viral_idxs = detect_viral_videos(videos, multiplier=3.0)
    
    # 4. 聚合概览
    total_views = sum(v["views"] for v in videos)
    total_likes = sum(v["likes"] for v in videos)
    total_comments = sum(v["comments"] for v in videos)
    unique_channels = len(set(v["channel"] for v in videos if v["channel"]))
    avg_engagement = 0
    if total_views > 0:
        avg_engagement = round((total_likes + total_comments) / total_views * 100, 2)
    
    # 5. Claude 分类 + 洞察
    claude_result = await asyncio.to_thread(
        classify_videos_with_claude, query, videos
    )
    
    # 6. 按 Claude 分类 group videos
    categories: dict[str, list[dict]] = {
        "reviews":  [],
        "use_case": [],
        "ads":      [],
        "kol":      [],
        "viral":    [],
        "mention":  [],
    }
    
    # 先把 Claude 分类结果 apply 上去
    classifications = claude_result.get("classifications", []) if isinstance(claude_result, dict) else []
    idx_to_cat: dict[int, str] = {}
    for c in classifications:
        if isinstance(c, dict) and "idx" in c and "category" in c:
            idx_to_cat[c["idx"]] = c["category"]
    
    # 分组
    for v in videos:
        cat = idx_to_cat.get(v["idx"], "mention")
        if cat not in categories:
            cat = "mention"
        categories[cat].append(v)
    
    # Viral 是独立维度 (有可能 viral 的视频同时也是 review)
    # 我们让 viral 专门显示爆火的, 不 mutually exclusive
    categories["viral"] = [v for v in videos if v["idx"] in viral_idxs]
    
    # 7. 每类生成 top 3
    def make_category_summary(cat_videos: list[dict]) -> dict:
        if not cat_videos:
            return {
                "count": 0,
                "total_views": 0,
                "top_videos": [],
            }
        
        total_v = sum(v["views"] for v in cat_videos)
        top = sorted(cat_videos, key=lambda x: x["views"], reverse=True)[:5]
        
        return {
            "count": len(cat_videos),
            "total_views": total_v,
            "top_videos": [
                {
                    "title":     v["title"],
                    "channel":   v["channel"],
                    "views":     v["views"],
                    "url":       v["url"],
                    "published": v["published"],
                    "thumbnail": v["thumbnail"],
                }
                for v in top
            ],
        }
    
    categories_summary = {
        cat: make_category_summary(vids)
        for cat, vids in categories.items()
    }
    
    # 8. Claude insights (去掉 classifications 字段, 太大)
    claude_insights: dict[str, Any] = {}
    if isinstance(claude_result, dict):
        if "error" in claude_result:
            claude_insights = {"error": claude_result["error"]}
        else:
            claude_insights = {
                "summary":         claude_result.get("summary", ""),
                "trending_topics": claude_result.get("trending_topics", []),
                "notable_creators":claude_result.get("notable_creators", []),
                "opportunities":   claude_result.get("opportunities", []),
                "sentiment":       claude_result.get("sentiment", "unknown"),
                "viltrox_angle":   claude_result.get("viltrox_angle", ""),
            }
    
    elapsed = time.time() - t0
    
    return {
        "query": query,
        "platform": search_result.get("platform", platform),
        "market": (market or "").strip().upper(),
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        
        "overview": {
            "total_videos":    len(videos),
            "total_views":     total_views,
            "total_likes":     total_likes,
            "total_comments":  total_comments,
            "unique_creators": unique_channels,
            "avg_engagement_pct": avg_engagement,
        },
        
        "time_distribution": time_dist,
        "hourly_distribution": hourly_dist,
        
        "categories": categories_summary,
        
        "claude_insights": claude_insights,
        
        "metadata": {
            "duration_sec":  round(elapsed, 1),
            "cost_usd_est":  0.10,
            "max_videos":    max_videos,
            "provider_status": search_result.get("status"),
            "date_from": date_from,
            "date_to": date_to,
        },
    }


# ──────────────────────────────────────────────
# 测试入口
# python -m app.services.intelligence.lens_monitor
# ──────────────────────────────────────────────
if __name__ == "__main__":
    raise SystemExit("Direct provider demo is disabled; enqueue intel_lens_monitor through the durable job queue.")
