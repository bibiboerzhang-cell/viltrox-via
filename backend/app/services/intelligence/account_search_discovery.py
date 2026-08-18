"""
services/intelligence/account_search_discovery.py — 平台内容搜索(全网新发现的 provider 层)

K2 扩量刀(2026-07-21)配套拆分:search_platform_content 及其 YT/IG/TT 检索词、
YouTube Data API 快路径(多路短词 + channels.list 富化)、IG 号主收敛 + 档案富化
全部从 account_scan_service.py 抽出(千行卫兵,新文件零豁免)。行为不变量:
- account_scan_service 原名 re-export,所有既有 import 点(profile_discovery /
  kol_ops / lens_monitor)与 monkeypatch 点保持不变;
- 共享运行时(_run_actor/provider_ready)经 _scan_service() 懒 import 宿主模块取,
  防循环 import,tests patch account_scan_service._run_actor 依旧生效。
红线:纯 provider/候选层,零触 viltrox_fit_score / rule_v0。
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from app.core.logging import get_logger
from app.services.intelligence.account_scan_helpers import *  # noqa: F403

logger = get_logger(__name__)


def _scan_service():
    """懒 import 宿主模块:取共享 _run_actor/provider_ready(防循环 import;
    保住既有 monkeypatch 点 —— tests patch account_scan_service._run_actor/provider_ready)。"""
    from app.services.intelligence import account_scan_service as _scan

    return _scan


# Instagram hashtag 召回口径:与 YouTube/TikTok 一致 —— 把多词 query 当作完整的
# 召回意图,而不是只取首词当单 hashtag(旧逻辑系统性少召回/错召回)。
# IG hashtag actor 不能整串去空格拼成一个超长无效 hashtag(搜不到 → 恒空),
# 故拆成「多个 hashtag」:每个有意义词单独成 tag,保留组合关键词语义。
_INSTAGRAM_HASHTAG_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into", "your", "you", "are",
    "this", "that", "best", "top", "new", "all", "any", "how", "why",
    "what", "who", "kol", "kols", "influencer", "influencers", "creator",
    "creators", "channel", "channels", "video", "videos", "review",
    "reviews",
})


def _instagram_hashtags(query: str, *, max_tags: int = 5) -> List[str]:
    """Split a (possibly multi-word) query into multiple Instagram hashtags.

    Aligns IG recall with YouTube/TikTok, which pass the full query. Each
    meaningful token becomes its own hashtag (alnum/underscore only, <=80
    chars), stopwords and tiny tokens are dropped, duplicates collapsed, and
    the count is capped at ``max_tags`` to preserve actor throttle/budget.

    Pure function (no Apify call) so it can be unit-tested directly.
    """
    seen: set[str] = set()
    tags: List[str] = []
    fallback: List[str] = []  # short/stopword tokens kept only if nothing else
    for word in (query or "").lower().split():
        token = "".join(ch for ch in word if ch.isalnum() or ch == "_")[:80]
        if not token or token in seen:
            continue
        seen.add(token)
        if len(token) > 2 and token not in _INSTAGRAM_HASHTAG_STOPWORDS:
            tags.append(token)
            if len(tags) >= max_tags:
                break
        elif len(token) > 1:
            fallback.append(token)
    if not tags:
        # No "meaningful" token survived (e.g. very short brand like "
        # dji" already >2, but pure-stopword/short queries) → keep what we
        # have so IG still gets a real hashtag instead of returning empty.
        tags = fallback[:max_tags]
    return tags


def _short_search_queries(query: str, *, max_queries: int = 4) -> List[str]:
    """Turn a planner's comma-separated persona list into short search intents.

    TikTok's keyword actor performs poorly when it receives the whole planner
    sentence as one exact query. The returned list is bounded and deterministic;
    callers divide the original result budget across these variants.
    """
    chunks = [" ".join(part.split()) for part in re.split(r"[,;|，；]+", query or "") if part.strip()]
    if len(chunks) <= 1:
        words = (query or "").split()
        chunks = [" ".join(words[index:index + 5]) for index in range(0, len(words), 5)] or [query]
    out: List[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        value = " ".join(chunk.split()[:8]).strip()[:100]
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max_queries:
            break
    return out or [" ".join((query or "").split())[:100]]



def _youtube_search_query_variants(search_query: str, *, max_variants: int = 3) -> List[str]:
    """K2 扩量刀·YT 多路短词:planner 的长 persona 整句在 search.list type=channel 上
    命中率极低(实测 20 词整句只回 1 条频道,funnel 997/1089 两轮坐实)。整句 ≤6 词
    直接用(与旧行为一致);更长则拆成 ≤max_variants 条短意图词(复用
    _short_search_queries 的逗号/5 词分块口径),调用方逐条搜后按 channelId 合并去重。
    纯函数零 IO,便于单测。"""
    full_q = " ".join(str(search_query or "").split())
    if not full_q:
        return []
    variants: List[str] = [full_q] if len(full_q.split()) <= 6 else []
    for candidate in _short_search_queries(full_q, max_queries=max_variants):
        if candidate and candidate.lower() not in {v.lower() for v in variants}:
            variants.append(candidate)
    return variants[:max_variants] or [full_q]


def _youtube_channel_statistics(crawler: Any, channel_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """channels.list part=snippet,statistics(≤50 id/次,1 quota unit/次)→ 发现即知真粉丝数。

    治「新面孔全折叠成分析中」根因之一:search.list snippet 不带统计,followers 未知 →
    会话读端第二道闸把全部 YT 新人折叠成「分析中」,面上只剩库内已有的人。这里花 1 个
    配额单位批量补齐 subscriberCount(隐藏订阅数诚实置 0=未知)/customUrl(真 @handle,
    治 channel_id 当 handle 的池内去重错配)/country(喂 _detect_excluded_region 真地区信号)。
    任一批失败只跳过该批;无 key → {}(诚实降级,行为退回旧版 followers 未知)。"""
    ids = [str(cid or "").strip() for cid in channel_ids if str(cid or "").strip()]
    if not ids or not getattr(crawler, "api_key", ""):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        payload = crawler._request(
            "channels",
            {"part": "snippet,statistics", "id": ",".join(batch), "maxResults": 50},
        )
        if str(payload.get("provider_status") or "") != "ok":
            continue
        for row in payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("id") or "").strip()
            if not cid:
                continue
            snippet = row.get("snippet") if isinstance(row.get("snippet"), dict) else {}
            stats = row.get("statistics") if isinstance(row.get("statistics"), dict) else {}
            hidden = bool(stats.get("hiddenSubscriberCount"))
            out[cid] = {
                # 隐藏订阅数 → 0(=未知,读端归「分析中」),绝不杜撰。
                "subscribers": 0 if hidden else _normalize_int(stats.get("subscriberCount")),
                "hidden_subscribers": hidden,
                "video_count": _normalize_int(stats.get("videoCount")),
                "view_count": _normalize_int(stats.get("viewCount")),
                "custom_url": str(snippet.get("customUrl") or "").strip(),
                "country": str(snippet.get("country") or "").strip(),
                "description": str(snippet.get("description") or "").strip(),
                "default_language": str(snippet.get("defaultLanguage") or "").strip(),
            }
    return out


def _youtube_data_api_normalize(
    items: List[Dict[str, Any]],
    query: str,
    market: str,
    actor_id: str,
    safe_limit: int,
    stats_by_id: Dict[str, Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """Map YouTube Data API search.list (type=channel) snippets to discovery candidates.

    Output shape matches the Apify-path item exactly so downstream annotate / region /
    garbage filters behave identically regardless of which provider fed the candidate.
    stats_by_id(channels.list 富化,可缺):补 followers/真 @handle/country/频道简介。
    """
    normalized: List[Dict[str, Any]] = []
    for raw in items[:safe_limit]:
        snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
        channel_id = str(((raw.get("id") or {}).get("channelId")) or "").strip()
        channel_name = str(snippet.get("channelTitle") or snippet.get("title") or "").strip()
        thumbs = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
        avatar_url = str(
            (((thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")) or {}).get("url")) or ""
        ).strip()
        channel_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
        stats = (stats_by_id or {}).get(channel_id) or {}
        # 真 @handle 优先(channels.list customUrl);缺富化时退回旧口径 channel_id 当 handle
        # (保证非空避开 _is_discovery_garbage)。channel_url 恒 /channel/UCxxx,
        # 旧轮以 UC id 入库的行仍可经 _candidate_handles(channel_url) 命中,不产重复。
        custom_handle = str(stats.get("custom_url") or "").lstrip("@").strip()
        handle = custom_handle or channel_id or channel_name
        followers = _normalize_int(stats.get("subscribers"))
        country = str(stats.get("country") or "").strip()
        bio = str(stats.get("description") or snippet.get("description") or "").strip()
        clean_channel_name = _known_text(channel_name, handle) or "Unknown creator"
        normalized.append(
            {
                "platform": "youtube",
                "channel_name": clean_channel_name,
                "handle": _known_text(handle, channel_name),
                "avatar_url": avatar_url,
                "thumbnail_url": avatar_url,
                "channel_url": channel_url,
                "source_url": channel_url,
                "sample_title": str(snippet.get("description") or "")[:300],
                "views": 0,
                "likes": 0,
                "comments": 0,
                "avg_views": 0,
                "published": str(snippet.get("publishedAt") or "").strip(),
                "market": (market or "").strip().upper(),
                "search_query": (query or "").strip(),
                "provider_actor": actor_id,
                "channel_id": channel_id,
                "fast_path": True,
                # followers 仅真有值才透出(隐藏/未知不带键 → 读端诚实归「分析中」)。
                **({"followers": followers} if followers > 0 else {}),
                **({"country": country, "country_source": "platform_profile"} if country else {}),
                **({"bio": bio[:500]} if bio else {}),
            }
        )
    return normalized


async def _youtube_data_api_search(search_query: str, *, market: str = "", safe_limit: int = 25, relevance_language: str = "en") -> Dict[str, Any] | None:
    """YouTube Data API fast path (search.list type=channel, ~1s). None => fall back to Apify.

    K2 扩量刀(2026-07-21):旧版单次整句 search.list,长 persona 句实测只回 0-1 条
    (funnel 997/1089 两轮),且 ≥1 条即短路 → YT 新发现饿死。现改:
    ① 多路短词并查:_youtube_search_query_variants 拆 ≤3 条短意图词逐条搜,按 channelId
      合并去重,合并上限 min(50, 2×safe_limit)(去重/闸门在下游还要吃掉一批,多备一倍补位);
    ② channels.list 富化:1 quota unit 批量补 subscriberCount/customUrl/country,
      新面孔发现即知粉丝数,不再全员折叠成「分析中」。
    None is returned when: no API key, quota exhausted, or every variant errored — the
    caller then runs the existing Apify youtube-scraper branch unchanged.
    Quota: search.list = 100 units/variant(≤3)+ channels.list 1 unit ≈ ≤301 units/discovery
    (daily default 10000).
    """
    from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

    crawler = YouTubeCrawler()
    if not crawler.api_key:
        return None
    variants = _youtube_search_query_variants(search_query)
    if not variants:
        return None
    merge_cap = min(50, max(1, int(safe_limit or 25)) * 2)

    def _channel_search(q: str) -> Dict[str, Any] | None:
        payload = crawler._request(
            "search",
            {
                "part": "snippet",
                "type": "channel",
                "q": q,
                "maxResults": max(1, min(25, int(safe_limit or 25))),
                "relevanceLanguage": (relevance_language or "en").strip().lower() or "en",
                "safeSearch": "none",
            },
        )
        if crawler._should_use_apify_fallback(payload) or str(payload.get("provider_status") or "") == "error":
            return None
        return payload

    def go() -> tuple[List[Dict[str, Any]], List[str], bool]:
        merged: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        used_queries: List[str] = []
        any_ok = False
        for q in variants:
            payload = _channel_search(q)
            if payload is None:
                continue  # 该变体配额/错误 → 跳过;全灭才降级 Apify
            any_ok = True
            used_queries.append(q)
            for raw in payload.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                cid = str(((raw.get("id") or {}).get("channelId")) or "").strip()
                if not cid or cid in seen_ids:
                    continue
                seen_ids.add(cid)
                merged.append(raw)
            if len(merged) >= merge_cap:
                break
        return merged[:merge_cap], used_queries, any_ok

    try:
        raw_items, used_queries, any_ok = await asyncio.to_thread(go)
    except Exception as exc:  # pragma: no cover - network only
        logger.warning("scanner.youtube_data_api_failed", extra={"error": str(exc)})
        return None
    if not any_ok:
        return None
    stats_by_id: Dict[str, Dict[str, Any]] = {}
    if raw_items:
        try:
            stats_by_id = await asyncio.to_thread(
                _youtube_channel_statistics,
                crawler,
                [str(((raw.get("id") or {}).get("channelId")) or "").strip() for raw in raw_items],
            )
        except Exception as exc:  # pragma: no cover - network only
            logger.warning("scanner.youtube_channel_stats_failed", extra={"error": str(exc)})
    items = _youtube_data_api_normalize(
        raw_items, search_query, market, "youtube-data-api/search.list",
        merge_cap, stats_by_id=stats_by_id,
    )
    return {
        "status": "done",
        "platform": "youtube",
        "query": (search_query or "").strip(),
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": {
            "actor_id": "youtube-data-api/search.list",
            "provider": "youtube_data_api",
            "fast_path": True,
            "requested": int(safe_limit or 25),
            "returned": len(items),
            "provider_queries": used_queries,
            "quota_units": 100 * max(1, len(used_queries)) + (1 if stats_by_id else 0),
            "channels_enriched": sum(1 for item in items if item.get("followers")),
        },
    }


async def _youtube_data_api_strict_video_search(
    search_query: str,
    *,
    market: str = "",
    safe_limit: int = 25,
    relevance_language: str = "en",
) -> Dict[str, Any] | None:
    """Fetch real <=45-day videos, then batch-enrich their declared channels."""
    from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

    crawler = YouTubeCrawler()
    if not crawler.api_key:
        return None
    variants = _youtube_search_query_variants(search_query)
    if not variants:
        return None
    result_limit = max(1, min(50, int(safe_limit or 25)))
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=45)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")

    def go() -> tuple[List[Dict[str, Any]], List[str], bool]:
        merged: List[Dict[str, Any]] = []
        seen_channels: set[str] = set()
        used_queries: List[str] = []
        any_ok = False
        for query_variant in variants:
            payload = crawler._request(
                "search",
                {
                    "part": "snippet",
                    "type": "video",
                    "q": query_variant,
                    "publishedAfter": published_after,
                    "maxResults": result_limit,
                    "relevanceLanguage": (relevance_language or "en").strip().lower() or "en",
                    "safeSearch": "none",
                },
            )
            if crawler._should_use_apify_fallback(payload) or str(payload.get("provider_status") or "") == "error":
                continue
            any_ok = True
            used_queries.append(query_variant)
            for raw in payload.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
                channel_id = str(snippet.get("channelId") or "").strip()
                video_id = str(((raw.get("id") or {}).get("videoId")) or "").strip()
                if not channel_id or not video_id or channel_id in seen_channels:
                    continue
                seen_channels.add(channel_id)
                merged.append(raw)
                if len(merged) >= result_limit:
                    break
            if len(merged) >= result_limit:
                break
        return merged, used_queries, any_ok

    try:
        raw_items, used_queries, any_ok = await asyncio.to_thread(go)
    except Exception as exc:  # pragma: no cover - network only
        logger.warning("scanner.youtube_strict_video_search_failed", extra={"error": str(exc)})
        return None
    if not any_ok:
        return None
    channel_ids = [
        str(((raw.get("snippet") or {}).get("channelId")) or "").strip()
        for raw in raw_items
    ]
    try:
        stats_by_id = await asyncio.to_thread(_youtube_channel_statistics, crawler, channel_ids)
    except Exception as exc:  # pragma: no cover - network only
        logger.warning("scanner.youtube_strict_channel_stats_failed", extra={"error": str(exc)})
        stats_by_id = {}

    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        snippet = raw.get("snippet") if isinstance(raw.get("snippet"), dict) else {}
        channel_id = str(snippet.get("channelId") or "").strip()
        video_id = str(((raw.get("id") or {}).get("videoId")) or "").strip()
        stats = stats_by_id.get(channel_id) or {}
        custom_handle = str(stats.get("custom_url") or "").lstrip("@").strip()
        handle = custom_handle or channel_id
        followers = _normalize_int(stats.get("subscribers"))
        country = str(stats.get("country") or "").strip()
        language = str(stats.get("default_language") or snippet.get("defaultAudioLanguage") or "").strip()
        thumbnail = (((snippet.get("thumbnails") or {}).get("high") or (snippet.get("thumbnails") or {}).get("default") or {}).get("url") or "")
        items.append({
            "platform": "youtube",
            "channel_id": channel_id,
            "channel_name": str(snippet.get("channelTitle") or "Unknown creator").strip(),
            "handle": handle,
            "channel_url": f"https://www.youtube.com/channel/{channel_id}",
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
            "content_url": f"https://www.youtube.com/watch?v={video_id}",
            "video_id": video_id,
            "sample_title": str(snippet.get("title") or "")[:300],
            "published": str(snippet.get("publishedAt") or "").strip(),
            "avatar_url": str(thumbnail).strip(),
            "bio": str(stats.get("description") or "")[:500],
            "provider_actor": "youtube-data-api/search.list:video",
            **({"followers": followers} if followers > 0 else {}),
            **({"country": country, "country_source": "platform_profile"} if country else {}),
            **({"language": language, "language_source": "platform_profile"} if language else {}),
        })
    return {
        "status": "done",
        "platform": "youtube",
        "query": (search_query or "").strip(),
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": {
            "actor_id": "youtube-data-api/search.list:video",
            "provider": "youtube_data_api",
            "strict_video_evidence": True,
            "requested": result_limit,
            "returned": len(items),
            "provider_queries": used_queries,
            "published_after": published_after,
            "quota_units": 100 * max(1, len(used_queries)) + (1 if stats_by_id else 0),
            "channels_enriched": sum(1 for item in items if item.get("followers")),
        },
    }


def _tiktok_collapse_author_videos(raw_items: List[Dict[str, Any]], safe_limit: int) -> List[Dict[str, Any]]:
    """重复卡修(2026-07-21 sky_vanya 案)·TT 号主收敛:关键词搜出的视频流按 authorMeta.name
    收敛成「每号主一条」(保 actor 相关度序首条)。多路短词变体 + 同号主多视频会让同一人
    吃掉多个候选槽位并重复上墙——YT 快路径按 channelId 合并、IG 有 owner 收敛,TT 此前缺
    这道 (platform, handle 小写) 去重。无 author 的条目保序排尾兜底。纯函数零 IO。"""
    by_author: Dict[str, Dict[str, Any]] = {}
    authorless: List[Dict[str, Any]] = []
    for item in raw_items:
        row = item if isinstance(item, dict) else {}
        author = row.get("authorMeta") if isinstance(row.get("authorMeta"), dict) else {}
        handle = str(author.get("name") or "").strip().lstrip("@").lower()
        if not handle:
            raw_author = row.get("author")
            handle = str(raw_author or "").strip().lstrip("@").lower() if isinstance(raw_author, str) else ""
        if not handle:
            authorless.append(row)
            continue
        if handle not in by_author:
            by_author[handle] = row
    merged = list(by_author.values()) + authorless
    return merged[: max(1, int(safe_limit or 1))]


def _candidate_identity_key(item: Dict[str, Any]) -> str:
    """归一候选身份键 (platform, handle 小写去 @);无 handle 退 channel_url/source_url 小写。
    键为空 → 调用方放行(不误杀)。供聚合层「每号主一条」兜底去重,纯函数零 IO。"""
    platform = str(item.get("platform") or "").strip().lower()
    handle = str(item.get("handle") or "").strip().lstrip("@").lower()
    if handle:
        return f"{platform}:{handle}"
    for key in ("channel_url", "source_url"):
        url = str(item.get(key) or "").strip().lower()
        if url:
            return f"{platform}:{url}"
    return ""


def _instagram_collapse_owner_posts(raw_items: List[Dict[str, Any]], safe_limit: int) -> List[Dict[str, Any]]:
    """K2 扩量刀·IG 号主收敛:hashtag 帖子流按 ownerUsername 收敛成「每号主一帖」
    (保 hashtag 排序首帖),再截 safe_limit。治「20 帖只剩 13 号主、槽位被同号主
    多帖吃掉」(funnel 实测收敛比 ~3:2)。无 owner 的帖保序排尾兜底。纯函数零 IO。"""
    by_owner: Dict[str, Dict[str, Any]] = {}
    ownerless: List[Dict[str, Any]] = []
    for item in raw_items:
        row = item if isinstance(item, dict) else {}
        owner = str(row.get("ownerUsername") or row.get("username") or "").strip().lower()
        if not owner:
            ownerless.append(row)
            continue
        if owner not in by_owner:
            by_owner[owner] = row
    merged = list(by_owner.values()) + ownerless
    return merged[: max(1, int(safe_limit or 1))]


async def _instagram_owner_profiles(usernames: List[str]) -> Dict[str, Dict[str, Any]]:
    """K2 扩量刀·IG 档案富化:apify/instagram-profile-scraper 批量拉号主档案
    (followersCount/biography/fullName/头像)。治「IG 新面孔恒无 followers →
    会话读端全折叠成分析中,面上永远见不到 IG 新人」。一次 actor run 喂全部
    usernames(≤50 封顶);actor 失败/空 → {}(诚实降级,行为退回旧版 followers 未知)。
    env APIFY_INSTAGRAM_PROFILE_ACTOR_ID 可换 actor(与 douyin/facebook 分支同模式)。"""
    names: List[str] = []
    seen: set[str] = set()
    for username in usernames:
        name = str(username or "").strip().lstrip("@")
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    names = names[:50]
    if not names:
        return {}
    actor_id = (os.getenv("APIFY_INSTAGRAM_PROFILE_ACTOR_ID") or "apify/instagram-profile-scraper").strip()
    rows = await _scan_service()._run_actor(actor_id, {"usernames": names}, timeout=300)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        uname = str(row.get("username") or "").strip().lower()
        if uname:
            out[uname] = row
    return out


async def search_platform_content(
    platform: str,
    query: str,
    *,
    market: str = "",
    max_results: int = 25,
    relevance_language: str = "en",
    strict_evidence: bool = False,
) -> Dict[str, Any]:
    """Search public platform content and normalize it into KOL candidates.

    This returns real provider results only. If the Apify provider is not
    configured or a platform search actor is unavailable, the status says so
    explicitly instead of fabricating rows.
    """
    normalized_platform = (platform or "youtube").strip().lower()
    normalized_query = (query or "").strip()
    safe_limit = max(1, min(int(max_results or 25), 100))
    if not normalized_query:
        return {"status": "invalid_query", "items": [], "message": "query is required"}
    search_query = _market_query(normalized_query, market)
    provider_queries = [search_query]

    # YouTube fast path: YouTube Data API search.list (~1s) before the slow Apify actor
    # (10-60s cold start). 命中即返回归一化候选;无 key / 配额耗尽 / 错误 → None,落回下方
    # 原 Apify youtube-scraper 分支(逻辑零改动)。
    # K2 修:≥1 条即短路会让 1-2 条的贫瘠结果饿死 YT 新发现(997 案 requested 20 → 1),
    # 快路径多路合并后仍 <3 条 → 一样落回 Apify 视频搜索补深。
    if normalized_platform == "youtube":
        fast = await (
            _youtube_data_api_strict_video_search(
                search_query,
                market=market,
                safe_limit=safe_limit,
                relevance_language=relevance_language,
            )
            if strict_evidence
            else _youtube_data_api_search(
                search_query,
                market=market,
                safe_limit=safe_limit,
                relevance_language=relevance_language,
            )
        )
        if fast is not None and (
            strict_evidence or len(fast.get("items") or []) >= min(3, safe_limit)
        ):
            return fast

    if not _scan_service().provider_ready():
        return {"status": "provider_unavailable", "items": [], "message": "APIFY_TOKEN is not configured"}

    actor_id = ""
    payload: Dict[str, Any] = {}
    timeout = 240
    if normalized_platform == "youtube":
        actor_id = "streamers/youtube-scraper"
        payload = {
            "searchQueries": [search_query],
            "maxResults": safe_limit,
            "maxResultsShorts": 0,
            "maxResultStreams": 0,
        }
    elif normalized_platform == "tiktok":
        actor_id = "clockworks/free-tiktok-scraper"
        provider_queries = _short_search_queries(search_query)
        payload = {
            "searchQueries": provider_queries,
            "resultsPerPage": max(3, (safe_limit + len(provider_queries) - 1) // len(provider_queries)),
            "shouldDownloadVideos": False,
            "shouldDownloadCovers": False,
        }
    elif normalized_platform == "instagram":
        actor_id = "apify/instagram-hashtag-scraper"
        # 多词 query 拆成「多个 hashtag」(与 YouTube/TikTok 召回口径对齐),
        # 而非旧逻辑只取首词当单 hashtag → 系统性少召回/错召回。
        # 整串去空格会拼成超长无效 hashtag(IG actor 搜不到 → 恒空),故分词。
        hashtags = _instagram_hashtags(search_query)
        if not hashtags:
            return {"status": "invalid_query", "items": [], "message": "instagram hashtag query is empty after normalization"}
        # resultsLimit 是「总条数(帖)」上限。K2 扩量刀:帖→号主收敛比实测 ~3:2
        # (20 帖 → 13 号主,top 帖常被同号主屠占),抓帖量放到 3×safe_limit(≤100 帽)
        # 再由 _instagram_collapse_owner_posts 收敛回 safe_limit 个「独立号主」——
        # 最终候选量口径不变,只治槽位浪费。
        payload = {
            "hashtags": hashtags,
            "resultsLimit": min(max(1, safe_limit) * 3, 100),
            "resultsType": "posts",
        }
        timeout = 300
    elif normalized_platform == "facebook":
        # Facebook 发现(用户口径:FB 流量一般般,做够用的就行,别过度设计)。
        # actor 选择说明:仓库既有 apify/facebook-posts-scraper 只吃 startUrls(页面 URL)+
        # resultsLimit(见本文件 scan_facebook_account 与 facebook_crawler.py),不支持关键词
        # 搜索,做不了「检索词 → 发现新创作者」。故此分支换官方 apify/facebook-search-scraper:
        # 关键词搜公开帖文,帖文项自带发帖主页名/URL,可映射成统一候选结构。
        # env 可换 actor(与 douyin 分支同模式);resultsLimit 沿用仓库 FB 调用的封顶口径。
        actor_id = (os.getenv("APIFY_FACEBOOK_SEARCH_ACTOR_ID") or "apify/facebook-search-scraper").strip()
        payload = {
            "searchQueries": [search_query],
            "searchType": "posts",
            "resultsLimit": safe_limit,
        }
        timeout = 300
    elif normalized_platform == "douyin":
        actor_id = _douyin_actor_id("search")
        if not actor_id:
            return {
                "status": "actor_not_configured",
                "platform": "douyin",
                "items": [],
                "message": "APIFY_DOUYIN_SEARCH_ACTOR_ID or APIFY_DOUYIN_ACTOR_ID is not configured",
            }
        payload = _douyin_search_payload(search_query, safe_limit)
        timeout = 360
    else:
        return {
            "status": "unsupported_platform",
            "items": [],
            "message": f"{normalized_platform} platform search is not configured",
        }

    started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_items = await _scan_service()._run_actor(actor_id, payload, timeout=timeout)
    ig_profiles: Dict[str, Dict[str, Any]] = {}
    ig_raw_posts = len(raw_items)
    if normalized_platform == "tiktok" and raw_items:
        # 重复卡修:视频流先按号主收敛(每号主一条),再截 safe_limit——否则同号主多视频/
        # 多路检索变体重复项既吃槽位又重复上墙(sky_vanya 案)。
        raw_items = _tiktok_collapse_author_videos(raw_items, safe_limit)
    if normalized_platform == "instagram" and raw_items:
        # K2:帖→号主收敛(每号主一帖)+ 批量档案富化(followers/bio/真名/头像)。
        # 富化失败诚实降级:候选照常返回,只是 followers 未知(读端归「分析中」)。
        raw_items = _instagram_collapse_owner_posts(raw_items, safe_limit)
        try:
            ig_profiles = await _instagram_owner_profiles(
                [str((row or {}).get("ownerUsername") or (row or {}).get("username") or "") for row in raw_items]
            )
        except Exception as exc:
            logger.warning("scanner.instagram_profile_enrich_failed", extra={"error": str(exc)[:300]})
            ig_profiles = {}
    items: List[Dict[str, Any]] = []
    seen_identities: set[str] = set()  # 聚合层兜底去重:(platform, handle 小写) 每号主一条
    for item in raw_items[:safe_limit]:
        handle = ""
        avatar_url = ""
        thumbnail_url = ""
        bio = ""  # instagram(档案富化)/其余平台留空;有值才随 item 透出
        followers = 0  # facebook/tiktok/instagram(富化后)可能填上;>0 才随 item 透出,其余输出不变
        channel_id = ""
        content_language = ""
        if normalized_platform == "youtube":
            channel_name = _source_key(item, "channelName", "channelTitle", "author")
            channel_url = _source_key(item, "channelUrl", "channelURL")
            channel_id = _source_key(item, "channelId", "channel.id")
            if not channel_id:
                match = re.search(r"(?:youtube\.com)/(?:channel)/([^/?#]+)", channel_url, re.IGNORECASE)
                channel_id = str(match.group(1) if match else "").strip()
            # Never use display-name/author text as a strict account handle.
            handle = _source_key(item, "channelHandle", "channelUsername", "handle") or channel_id
            avatar_url = _clean_url(_source_key(item, "channelAvatar", "channelThumbnail", "channelImage", "avatarUrl", "authorThumbnail"))
            thumbnail_url = _clean_url(_source_key(item, "thumbnailUrl", "thumbnail", "image", "cover"))
            source_url = _source_key(item, "url", "link")
            title = _source_key(item, "title", "text")
            views = _normalize_int(item.get("viewCount") or item.get("views"))
            likes = _normalize_int(item.get("likes"))
            comments = _normalize_int(item.get("commentsCount") or item.get("comments"))
            followers = _normalize_int(
                item.get("numberOfSubscribers")
                or item.get("subscriberCount")
                or item.get("subscribers")
                or 0
            )
            content_language = _source_key(item, "videoLanguage", "defaultAudioLanguage", "language")
        elif normalized_platform == "tiktok":
            author = item.get("authorMeta") if isinstance(item.get("authorMeta"), dict) else {}
            channel_name = _source_key(author, "nickName", "name") or _source_key(item, "authorName", "author")
            handle = _source_key(author, "name") or _source_key(item, "author")
            channel_url = f"https://www.tiktok.com/@{handle}" if handle else ""
            avatar_url = _clean_url(_source_key(author, "avatar", "avatarThumb", "avatarMedium", "avatarLarger", "profilePicture"))
            thumbnail_url = _clean_url(_source_key(item, "videoMeta.coverUrl", "cover", "coverUrl", "thumbnail"))
            source_url = _source_key(item, "webVideoUrl", "url")
            title = _source_key(item, "text", "desc", "title")
            stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
            views = _normalize_int(item.get("playCount") or stats.get("playCount"))
            likes = _normalize_int(item.get("diggCount") or stats.get("diggCount"))
            comments = _normalize_int(item.get("commentCount") or stats.get("commentCount"))
            # K2:actor 的 authorMeta 本就带粉丝数(fans),旧版白白丢弃 → TT 新面孔
            # followers 未知全折叠成「分析中」。字段多版本防御,没有保持 0(不杜撰)。
            followers = _normalize_int(
                author.get("fans") or author.get("followers") or author.get("followerCount") or 0
            )
        elif normalized_platform == "facebook":
            # facebook-search-scraper 帖文项映射:发帖主页名→channel_name/display_name,
            # 主页 URL→channel_url,帖文文本→sample_title。字段名多版本防御,
            # 数字口径与本文件 scan_facebook_account 对齐(text/message、likesCount/reactionsCount)。
            channel_name = _source_key(item, "pageName", "user.name", "authorName", "pageTitle", "name")
            channel_url = _clean_url(_source_key(item, "pageUrl", "user.profileUrl", "facebookUrl", "topLevelUrl"))
            handle = _source_key(item, "pageHandle", "username", "user.username", "pageUsername")
            if not handle and "facebook.com/" in channel_url:
                # 主页 URL 的 vanity slug 当 handle(facebook.com/<slug>);系统路径不算 handle。
                _slug = channel_url.split("facebook.com/", 1)[1].strip("/").split("/", 1)[0].split("?", 1)[0]
                if _slug and _slug.lower() not in {"profile.php", "people", "pages", "groups", "watch", "reel", "reels", "search"}:
                    handle = _slug
            if not channel_url and handle:
                channel_url = f"https://www.facebook.com/{handle}"
            avatar_url = _clean_url(_source_key(item, "user.profilePic", "pageProfilePic", "profilePic", "avatar"))
            thumbnail_url = _clean_url(_source_key(item, "thumbnail", "imageUrl", "image"))
            source_url = _source_key(item, "url", "postUrl", "topLevelUrl")
            title = _source_key(item, "text", "message", "title")
            views = _normalize_int(item.get("viewsCount") or item.get("views"))
            likes = _normalize_int(item.get("likesCount") or item.get("reactionsCount") or item.get("likes"))
            comments = _normalize_int(item.get("commentsCount") or item.get("comments"))
            # 粉丝数:posts 搜索项通常不带;字段有就带上,没有保持 0(不杜撰)。
            followers = _normalize_int(_source_key(item, "followers", "followersCount", "pageFollowers", "user.followers") or 0)
        elif normalized_platform == "douyin":
            post = _normalize_douyin_item(item)
            channel_name = str(post.get("channel") or "Unknown creator")
            handle = str(post.get("handle") or channel_name)
            channel_url = str(post.get("channel_url") or "")
            avatar_url = str(post.get("avatar_url") or "")
            thumbnail_url = str(post.get("thumbnail") or "")
            source_url = str(post.get("url") or "")
            title = str(post.get("title") or "")
            views = _normalize_int(post.get("views"))
            likes = _normalize_int(post.get("likes"))
            comments = _normalize_int(post.get("comments"))
        else:
            channel_name = _source_key(item, "ownerUsername", "username", "ownerFullName")
            handle = _source_key(item, "ownerUsername", "username")
            channel_url = f"https://www.instagram.com/{channel_name}/" if channel_name else _source_key(item, "ownerProfileUrl")
            avatar_url = _clean_url(_source_key(item, "ownerProfilePicUrl", "profilePicUrl", "profilePictureUrl", "displayProfilePicUrl", "avatarUrl"))
            thumbnail_url = _clean_url(_source_key(item, "displayUrl", "imageUrl", "thumbnailUrl", "thumbnail", "image"))
            source_url = _source_key(item, "url", "shortCode")
            title = _source_key(item, "caption", "title", "text")
            views = _normalize_int(item.get("videoViewCount") or item.get("videoPlayCount"))
            likes = _normalize_int(item.get("likesCount"))
            comments = _normalize_int(item.get("commentsCount"))
            # K2:档案富化合入(真粉丝数/真名/bio/高清头像)。富化缺席时全部保持旧值。
            profile = ig_profiles.get(str(handle or channel_name or "").strip().lower())
            if isinstance(profile, dict):
                followers = _normalize_int(profile.get("followersCount") or profile.get("followers") or 0)
                channel_name = _source_key(profile, "fullName") or channel_name
                avatar_url = avatar_url or _clean_url(_source_key(profile, "profilePicUrlHD", "profilePicUrl"))
                bio = str(profile.get("biography") or "").strip()[:500]

        # 修 query-as-handle bug:去掉 normalized_query 兜底——无真 handle/name 时不再把整句查询
        # 当成创作者(此前造出 youtube.com/@整句 的假号混入发现结果)。
        clean_channel_name = _known_text(channel_name, handle) or "Unknown creator"
        candidate = {
                "platform": normalized_platform,
                "channel_name": clean_channel_name,
                "handle": _known_text(handle, channel_name),
                "avatar_url": avatar_url or thumbnail_url,
                "thumbnail_url": thumbnail_url,
                "channel_url": channel_url,
                "source_url": source_url,
                "sample_title": title[:300],
                "views": views,
                "likes": likes,
                "comments": comments,
                "avg_views": views,
                "published": _published_value(item),
                "market": (market or "").strip().upper(),
                "search_query": normalized_query,
                "provider_actor": actor_id,
                # followers/bio 仅在真有值时透出(facebook/tiktok/instagram 富化),其余平台 item 结构不变。
                **({"followers": followers} if followers > 0 else {}),
                **({"bio": bio} if bio else {}),
                **({"channel_id": channel_id} if channel_id else {}),
                **({
                    "language": content_language,
                    "language_source": "platform_content_metadata",
                } if content_language else {}),
            }
        # 重复卡修·聚合层兜底去重:同平台同 handle(小写去 @)只出一条(多路检索变体/
        # 同号主多帖会产重复候选);键为空放行(不误杀)。首条保留=保 actor 相关度序。
        identity_key = _candidate_identity_key(candidate)
        if identity_key and identity_key in seen_identities:
            continue
        if identity_key:
            seen_identities.add(identity_key)
        items.append(candidate)

    return {
        "status": "done",
        "platform": normalized_platform,
        "query": normalized_query,
        "market": (market or "").strip().upper(),
        "items": items,
        "metadata": {
            "actor_id": actor_id,
            "requested": safe_limit,
            "returned": len(items),
            "provider_queries": provider_queries,
            "searched_at": started_at,
            # K2 可观测(仅 instagram 有意义):原始帖量/号主收敛后量/档案富化命中数。
            **({
                "raw_posts": ig_raw_posts,
                "unique_owners": len(items),
                "profile_enriched": len(ig_profiles),
            } if normalized_platform == "instagram" else {}),
        },
    }
