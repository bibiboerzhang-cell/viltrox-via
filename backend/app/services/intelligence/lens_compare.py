"""
services/intelligence/lens_compare.py — 镜头对比情报
========================================================
核心功能:
  用户输入 2 个镜头名字 (例 "viltrox 50mm" vs "sigma 50mm 1.4")
  系统:
    1. 跨 YouTube 搜索 top 视频
    2. 提取 views / likes / comments 总量
    3. 计算市场关注度对比
    4. Claude 分析视频标题 + 描述 → 提取讨论话题
    5. Claude 生成推广建议

成本: ~$0.05 / 次对比 (Apify) + $0.02 (Claude) ≈ $0.07
用时: 30-60 秒
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional

from app.core.config import CLAUDE_MODEL
from app.core.logging import get_logger
from app.platform.apify_budget import call_apify_actor
from app.platform.apify_lifecycle import register_apify_client_shutdown
from app.services.ai.retry import call_ai_with_retry
from app.services.ai.analyzers.anthropic_response_text import text_blocks_joined
from app.services.intelligence.lens_monitor import filter_videos_by_date, search_market_videos

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
# YouTube 搜索 (用 streamers/youtube-scraper 的 search mode)
# ──────────────────────────────────────────────

async def search_youtube_videos(query: str, max_results: int = 20) -> list[dict]:
    """
    在 YouTube 搜索关键词, 拿 top videos 的元数据.
    
    用 streamers/youtube-scraper + searchQueries 模式.
    
    Returns:
        list of video dicts (已标准化):
          {
            "title":    str,
            "views":    int,
            "likes":    int,
            "comments": int,
            "duration": str,
            "channel":  str,
            "url":      str,
            "published":str,
          }
    """
    if _apify is None:
        logger.warning("lens_compare.apify_unavailable")
        return []
    
    logger.info("lens_compare.youtube_search_started", extra={"query": query, "max_results": max_results})
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
                operation="lens_compare_search",
                source="intelligence.lens_compare",
                run_input=run_input,
                timeout_secs=180,
            )
            found = list(_apify.dataset(run["defaultDatasetId"]).iterate_items())
            # C5 成本记账收口:镜头对比搜索 run 统一记账(幂等 by run_id;失败绝不影响搜索)。
            try:
                from app.domains.costs.budget_guard import record_apify_run

                record_apify_run(
                    run,
                    actor_id="streamers/youtube-scraper",
                    platform="youtube",
                    operation="lens_compare_search",
                    source="intelligence.lens_compare",
                    dataset_item_count=len(found),
                )
            except Exception:
                logger.warning("lens_compare.cost_record_failed", exc_info=True)
            return found
        
        items = await asyncio.to_thread(_do)
        elapsed = time.time() - t0
        logger.info("lens_compare.youtube_search_complete", extra={"item_count": len(items), "elapsed_sec": round(elapsed, 1)})
        
        # 标准化
        videos = []
        for item in items:
            videos.append({
                "title":     str(item.get("title", "") or "")[:200],
                "views":     int(item.get("viewCount", 0) or 0),
                "likes":     int(item.get("likes", 0) or 0),
                "comments":  int(item.get("commentsCount", 0) or 0),
                "duration":  str(item.get("duration", "") or ""),
                "channel":   str(item.get("channelName", "") or "")[:100],
                "url":       str(item.get("url", "") or ""),
                "published": str(item.get("date", "") or ""),
                "description": str(item.get("text", "") or "")[:500],
            })
        
        return videos
    
    except Exception as e:
        logger.warning("lens_compare.youtube_search_failed", extra={"error": str(e)})
        return []


# ──────────────────────────────────────────────
# 统计聚合
# ──────────────────────────────────────────────

def compute_lens_stats(videos: list[dict]) -> dict:
    """基于 video list 算一个镜头的统计"""
    if not videos:
        return {
            "video_count": 0,
            "total_views": 0,
            "total_likes": 0,
            "total_comments": 0,
            "avg_views": 0,
            "avg_engagement_pct": 0,
            "top_channels": [],
            "top_videos": [],
        }
    
    total_views = sum(v["views"] for v in videos)
    total_likes = sum(v["likes"] for v in videos)
    total_comments = sum(v["comments"] for v in videos)
    
    engagement_pct = 0
    if total_views > 0:
        engagement_pct = round((total_likes + total_comments) / total_views * 100, 2)
    
    avg_views = total_views // len(videos) if videos else 0
    
    # Top channels
    channel_counts: dict[str, int] = {}
    for v in videos:
        ch = v["channel"]
        if ch:
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
    top_channels = sorted(channel_counts.items(), key=lambda x: -x[1])[:5]
    
    # Top videos (by views)
    top_videos = sorted(videos, key=lambda v: v["views"], reverse=True)[:5]
    
    return {
        "video_count": len(videos),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "avg_views": avg_views,
        "avg_engagement_pct": engagement_pct,
        "top_channels": [{"name": n, "videos": c} for n, c in top_channels],
        "top_videos": [
            {
                "title": v["title"],
                "views": v["views"],
                "channel": v["channel"],
                "url": v["url"],
            }
            for v in top_videos
        ],
    }


# ──────────────────────────────────────────────
# Claude 分析 (提取话题 + 生成推广建议)
# ──────────────────────────────────────────────

def build_compare_prompt(lens_a: str, lens_b: str, stats_a: dict, stats_b: dict, videos_a: list, videos_b: list) -> str:
    """构建 Claude 对比分析 prompt"""
    
    def summarize_videos(videos: list, lens_name: str) -> str:
        lines = [f"Lens: {lens_name}"]
        for v in videos[:10]:
            lines.append(f"  • [{v['views']:,} views] {v['title'][:100]}")
            if v.get("description"):
                lines.append(f"    {v['description'][:150]}")
        return "\n".join(lines)
    
    videos_text_a = summarize_videos(videos_a, lens_a)
    videos_text_b = summarize_videos(videos_b, lens_b)
    
    return f"""You are a brand intelligence analyst for Viltrox camera lens company.

I'm comparing two camera lenses on YouTube. Analyze the data and give actionable insights.

════════════════════════════════════════
LENS A: {lens_a}
────────────────────────────────────────
Video count: {stats_a['video_count']}
Total views: {stats_a['total_views']:,}
Avg views/video: {stats_a['avg_views']:,}
Engagement rate: {stats_a['avg_engagement_pct']}%

Top videos:
{videos_text_a}

════════════════════════════════════════
LENS B: {lens_b}
────────────────────────────────────────
Video count: {stats_b['video_count']}
Total views: {stats_b['total_views']:,}
Avg views/video: {stats_b['avg_views']:,}
Engagement rate: {stats_b['avg_engagement_pct']}%

Top videos:
{videos_text_b}

════════════════════════════════════════

Return ONLY valid JSON (no markdown, no prose) with this exact structure:

{{
  "market_gap": "One sentence describing the attention gap between A and B",
  "lens_a_topics": ["top 5 discussion topics for lens A, e.g. 'autofocus', 'price', 'bokeh'"],
  "lens_b_topics": ["top 5 discussion topics for lens B"],
  "lens_a_strengths": ["3 things people praise about A"],
  "lens_a_weaknesses": ["3 things people complain about A"],
  "lens_b_strengths": ["3 things people praise about B"],
  "lens_b_weaknesses": ["3 things people complain about B"],
  "sentiment_a": "positive|mixed|negative",
  "sentiment_b": "positive|mixed|negative",
  "content_gaps": ["3 topics B covers well but A doesn't"],
  "promotion_recommendations": [
    {{"angle": "...", "reason": "...", "creator_type": "..."}},
    {{"angle": "...", "reason": "...", "creator_type": "..."}},
    {{"angle": "...", "reason": "...", "creator_type": "..."}}
  ],
  "key_insight": "One paragraph: what does Viltrox need to do to close the gap?"
}}

Be specific, actionable, and honest. If A has LESS attention than B, say so directly.
"""


def analyze_with_claude(lens_a: str, lens_b: str, stats_a: dict, stats_b: dict, videos_a: list, videos_b: list) -> dict:
    """调 Claude(CLAUDE_MODEL 注册表绑定)生成对比洞察"""
    if not _claude_available:
        return {"error": "Claude not available"}
    
    client = get_claude_client()
    if client is None:
        return {"error": "Claude client not initialized"}
    
    prompt = build_compare_prompt(lens_a, lens_b, stats_a, stats_b, videos_a, videos_b)
    
    try:
        logger.info("lens_compare.claude_started", extra={"lens_a": lens_a, "lens_b": lens_b})
        t0 = time.time()
        resp = call_ai_with_retry(
            "lens_compare.claude",
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=2000,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        elapsed = time.time() - t0
        
        text = text_blocks_joined(resp)
        logger.info("lens_compare.claude_complete", extra={"elapsed_sec": round(elapsed, 1), "char_count": len(text)})
        
        # 清理 markdown code fence
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("lens_compare.claude_json_parse_failed", extra={"error": str(e)})
            return {"error": "Failed to parse Claude JSON", "raw": text[:500]}
    
    except Exception as e:
        logger.warning("lens_compare.claude_failed", extra={"error": str(e)})
        return {"error": str(e)}


# ──────────────────────────────────────────────
# 主入口 — 两个镜头完整对比
# ──────────────────────────────────────────────

async def compare_two_lenses(
    lens_a: str,
    lens_b: str,
    max_videos: int = 15,
    *,
    platform: str = "youtube",
    market: str = "",
    date_from: str = "",
    date_to: str = "",
) -> dict:
    """
    完整对比两个镜头.
    
    Args:
        lens_a: 镜头 A 名字 (例 "viltrox 50mm f1.8")
        lens_b: 镜头 B 名字 (例 "sigma 50mm f1.4")
        max_videos: 每个镜头抓多少 YouTube 视频
    
    Returns:
        {
          "lens_a": {"name", "stats", "top_videos"},
          "lens_b": {"name", "stats", "top_videos"},
          "comparison": {
            "winner_views": "a"|"b"|"tie",
            "attention_multiplier": 3.2,  # B 比 A 多 3.2x
            "engagement_delta": 0.5,       # A 比 B 高 0.5%
          },
          "claude_analysis": { ... Claude 返回的 JSON ... },
          "metadata": {"duration_sec", "cost_usd_est"}
        }
    """
    t0 = time.time()
    
    # 并行抓 2 个镜头
    logger.info(
        "lens_compare.run_started",
        extra={"lens_a": lens_a, "lens_b": lens_b, "max_videos": max_videos, "platform": platform, "market": market},
    )
    videos_a_task = asyncio.create_task(search_market_videos(lens_a, max_videos, platform=platform, market=market))
    videos_b_task = asyncio.create_task(search_market_videos(lens_b, max_videos, platform=platform, market=market))
    search_a = await videos_a_task
    search_b = await videos_b_task
    videos_a = filter_videos_by_date(search_a.get("videos", []), date_from=date_from, date_to=date_to)
    videos_b = filter_videos_by_date(search_b.get("videos", []), date_from=date_from, date_to=date_to)
    
    # 统计
    stats_a = compute_lens_stats(videos_a)
    stats_b = compute_lens_stats(videos_b)
    
    # 对比
    if stats_a["total_views"] > stats_b["total_views"]:
        winner = "a"
        multiplier = stats_a["total_views"] / max(stats_b["total_views"], 1)
    elif stats_b["total_views"] > stats_a["total_views"]:
        winner = "b"
        multiplier = stats_b["total_views"] / max(stats_a["total_views"], 1)
    else:
        winner = "tie"
        multiplier = 1.0
    
    engagement_delta = stats_a["avg_engagement_pct"] - stats_b["avg_engagement_pct"]
    
    comparison = {
        "winner_views": winner,
        "attention_multiplier": round(multiplier, 2),
        "engagement_delta": round(engagement_delta, 2),
        "lens_a_total_views": stats_a["total_views"],
        "lens_b_total_views": stats_b["total_views"],
    }
    
    # Claude 分析 (同步调用, 跑在 to_thread)
    claude_analysis = await asyncio.to_thread(
        analyze_with_claude, lens_a, lens_b, stats_a, stats_b, videos_a, videos_b
    )
    
    elapsed = time.time() - t0
    
    return {
        "lens_a": {
            "name": lens_a,
            "stats": stats_a,
            "provider_status": search_a.get("status"),
        },
        "lens_b": {
            "name": lens_b,
            "stats": stats_b,
            "provider_status": search_b.get("status"),
        },
        "comparison": comparison,
        "claude_analysis": claude_analysis,
        "metadata": {
            "duration_sec": round(elapsed, 1),
            "cost_usd_est": 0.07,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platform": (platform or "youtube").strip().lower(),
            "market": (market or "").strip().upper(),
            "date_from": date_from,
            "date_to": date_to,
            "provider_status_a": search_a.get("status"),
            "provider_status_b": search_b.get("status"),
        },
    }


# ──────────────────────────────────────────────
# 测试入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    raise SystemExit("Direct provider demo is disabled; enqueue intel_lens_compare through the durable job queue.")
