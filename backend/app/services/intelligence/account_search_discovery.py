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
import time
from datetime import datetime
from typing import Any, Dict, List

from app.core.logging import get_logger
from app.services.intelligence.account_scan_helpers import *  # noqa: F403
from app.services.intelligence.account_search_content_runtime import (
    ContentRuntimeDependencies,
    build_actor_plan,
    build_actor_result,
    normalize_actor_items,
    prepare_actor_items,
)
from app.services.intelligence.account_search_youtube_strict_runtime import (
    build_strict_search_plan,
    build_strict_search_result,
    enrich_strict_search_rows,
    merge_strict_page_state,
    normalize_strict_search_rows,
    run_strict_search_pages,
)
from app.services.intelligence.account_search_youtube_metrics import _youtube_channel_statistics, youtube_activation_coverage, youtube_channel_activation_summary, youtube_exact_query_failure, youtube_quota_metadata, youtube_sample_video_ids, youtube_video_statistics

# IG 腿的检索词/收敛/富化已拆到 account_search_instagram.py(800 软棘轮:本文件在
# 快照里锁死 843 行)。原名 re-export —— account_scan_service 的 re-export 链与
# 既有 monkeypatch 点(account_scan_service._instagram_hashtags 等)全部不变。
from app.services.intelligence.account_search_instagram import (  # noqa: F401
    _instagram_collapse_owner_posts,
    _instagram_hashtags,
    _instagram_owner_profiles,
    instagram_enrich_min_budget_seconds,
    instagram_enrich_targets,
)

# 检索词整形与候选收敛的纯函数已拆到 account_search_terms.py(同 843 行棘轮理由)。
# 原名 re-export —— account_scan_service 的 re-export 链与既有 monkeypatch 点全部不变。
from app.services.intelligence.account_search_terms import (  # noqa: F401
    PRECISION_TERMS_DEFAULT,
    _youtube_data_api_normalize,
    TERM_EXHAUSTED_TOKEN,
    _candidate_identity_key,
    _short_search_queries,
    _tiktok_collapse_author_videos,
    _youtube_search_query_variants,
    annotate_market_verification,
    market_verification_summary,
    prefer_market_items,
    query_anchor_signals,
    raw_country_hints,
    term_anchor_index,
    term_ledger_row,
    youtube_precision_terms,
)

logger = get_logger(__name__)


def _scan_service():
    """懒 import 宿主模块:取共享 _run_actor/provider_ready(防循环 import;
    保住既有 monkeypatch 点 —— tests patch account_scan_service._run_actor/provider_ready)。"""
    from app.services.intelligence import account_scan_service as _scan

    return _scan


def _content_runtime_dependencies() -> ContentRuntimeDependencies:
    """Bind compatibility helpers at call time so monkeypatch points survive."""

    return ContentRuntimeDependencies(
        avatar_url_policy=_avatar_url_policy,
        candidate_identity_key=_candidate_identity_key,
        clean_url=_clean_url,
        douyin_actor_id=_douyin_actor_id,
        douyin_search_payload=_douyin_search_payload,
        known_text=_known_text,
        normalize_douyin_item=_normalize_douyin_item,
        normalize_int=_normalize_int,
        published_value=_published_value,
        source_key=_source_key,
        instagram_collapse_owner_posts=_instagram_collapse_owner_posts,
        instagram_hashtags=_instagram_hashtags,
        instagram_enrich_min_budget_seconds=instagram_enrich_min_budget_seconds,
        instagram_enrich_targets=instagram_enrich_targets,
        short_search_queries=_short_search_queries,
        tiktok_collapse_author_videos=_tiktok_collapse_author_videos,
    )


def _verify_market(
    items: List[Dict[str, Any]],
    market: str,
    *,
    country_hints: Dict[str, str] | None = None,
) -> List[Dict[str, Any]]:
    """三条 provider 路共用的市场核实(2026-09-02 T 车道实测:market 盖成查询里的 US 未核实):
    country 只在平台自报可得时补、market 配 market_status 说清核实状态、有 market 时同市场优先
    (verified → unverified → mismatch,核实档之后的行打 market_backfill)。缺 market = 只补 country。"""
    return prefer_market_items(
        annotate_market_verification(items, market, country_hints=country_hints),
        market,
    )


async def _youtube_data_api_search(
    search_query: str, *, market: str = "", safe_limit: int = 25,
    relevance_language: str = "en", exact_query: bool = False,
) -> Dict[str, Any] | None:
    """YouTube Data API fast path (search.list type=channel, ~1s). None => fall back to Apify.

    K2 扩量刀(2026-07-21):旧版单次整句 search.list,长 persona 句实测只回 0-1 条
    (funnel 997/1089 两轮),且 ≥1 条即短路 → YT 新发现饿死。现改:
    ① 多路短词并查:_youtube_search_query_variants 拆 ≤3 条短意图词逐条搜,按 channelId
      合并去重,合并上限 min(50, 2×safe_limit)(去重/闸门在下游还要吃掉一批,多备一倍补位);
    ② channels.list 富化:1 quota unit 批量补 subscriberCount/customUrl/country,
      新面孔发现即知粉丝数,不再全员折叠成「分析中」。
    None is returned when: no API key, quota exhausted, or every variant errored — the
    caller then runs the existing Apify youtube-scraper branch unchanged.
    Quota accounting v2 keeps two official buckets separate: one Search Query
    call per issued ``search.list`` and one combined unit for ``channels.list``.
    """
    from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

    crawler = YouTubeCrawler()
    if not crawler.api_key:
        return None
    # Targeted Search V2 owns its QueryCell expression.  Expanding that exact
    # expression back into the legacy brand/category ladder would silently
    # undo the operator's segment (for example motorsport -> Viltrox flash
    # review), so an exact cell is sent once, byte-for-byte after whitespace
    # normalization.  Legacy callers retain the existing bounded variants.
    normalized_exact = " ".join(str(search_query or "").split())
    variants = [normalized_exact] if exact_query and normalized_exact else _youtube_search_query_variants(search_query)
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

    def go() -> tuple[List[Dict[str, Any]], List[str], bool, int]:
        merged: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        used_queries: List[str] = []
        any_ok = False
        search_calls = 0
        for q in variants:
            search_calls += 1
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
        return merged[:merge_cap], used_queries, any_ok, search_calls

    try:
        raw_items, used_queries, any_ok, search_calls = await asyncio.to_thread(go)
    except Exception as exc:  # pragma: no cover - network only
        logger.warning("scanner.youtube_data_api_failed", extra={"error": str(exc)})
        return None
    if not any_ok:
        return youtube_exact_query_failure(query=search_query, market=market, actor_id="youtube-data-api/search.list", search_calls=search_calls) if exact_query else None
    stats_by_id: Dict[str, Dict[str, Any]] = {}
    channels_list_calls = 1 if raw_items else 0
    if raw_items:
        try:
            stats_by_id = await asyncio.to_thread(
                _youtube_channel_statistics,
                crawler,
                [str(((raw.get("id") or {}).get("channelId")) or "").strip() for raw in raw_items],
            )
        except Exception as exc:  # pragma: no cover - network only
            logger.warning("scanner.youtube_channel_stats_failed", extra={"error": str(exc)})
    items = _verify_market(
        _youtube_data_api_normalize(
            raw_items, search_query, market, "youtube-data-api/search.list",
            merge_cap, stats_by_id=stats_by_id,
        ),
        market,
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
            "query_mode": "exact_query_cell" if exact_query else "expanded_ladder",
            **youtube_quota_metadata(
                search_calls=search_calls,
                channels_list_calls=channels_list_calls,
            ),
            "channels_enriched": sum(1 for item in items if item.get("followers")),
            "market_verification": market_verification_summary(items),
            # 车道 2:非严格频道搜索路只服务 legacy 单轮发现,刻意没接分页(接了也没人翻)。
            "pagination_supported": False,
            "pagination_unsupported_reason": "legacy_channel_search_not_wired",
            "has_more": False,
        },
    }


async def _youtube_data_api_strict_video_search(
    search_query: str,
    *,
    market: str = "",
    safe_limit: int = 25,
    relevance_language: str = "en",
    page_cursor: Any = None,
    exact_query: bool = False,
) -> Dict[str, Any] | None:
    """Fetch real <=45-day videos, then batch-enrich their declared channels.

    车道 2·分页:``page_cursor`` = {检索词变体: 上一轮该变体的 nextPageToken}。
    search.list 官方支持 pageToken/nextPageToken(每个变体各有一条页链),所以游标
    必须按变体存。返回的 metadata 带 ``next_page_cursor`` 与 ``has_more``——
    ``has_more`` 只在「真有 nextPageToken」或「本轮提前 break、还有变体没查过」时为真,
    绝不因为「跑过一轮了」就声称还有下一页。
    """
    from app.platform.industry_crawlers.youtube_crawler import YouTubeCrawler

    crawler = YouTubeCrawler()
    if not crawler.api_key:
        return None
    plan = build_strict_search_plan(
        search_query,
        safe_limit=safe_limit,
        relevance_language=relevance_language,
        page_cursor=page_cursor,
        exact_query=exact_query,
        query_variants=_youtube_search_query_variants,
        anchor_index=term_anchor_index,
        precision_terms_default=PRECISION_TERMS_DEFAULT,
        exhausted_token=TERM_EXHAUSTED_TOKEN,
    )
    if plan is None:
        return None

    try:
        page_result = await asyncio.to_thread(
            run_strict_search_pages,
            crawler,
            plan,
            ledger_row=term_ledger_row,
        )
    except Exception as exc:  # pragma: no cover - network only
        logger.warning("scanner.youtube_strict_video_search_failed", extra={"error": str(exc)})
        return None
    if not page_result.any_ok:
        return (
            youtube_exact_query_failure(
                query=search_query,
                market=market,
                actor_id="youtube-data-api/search.list:video",
                search_calls=page_result.search_calls,
            )
            if exact_query
            else None
        )
    page_state = merge_strict_page_state(plan, page_result)
    enrichment = await enrich_strict_search_rows(
        crawler,
        page_result.raw_items,
        channel_statistics=_youtube_channel_statistics,
        sample_video_ids=youtube_sample_video_ids,
        video_statistics=youtube_video_statistics,
        logger=logger,
    )

    items = _verify_market(
        normalize_strict_search_rows(
            page_result.raw_items,
            enrichment,
            exact_query=exact_query,
            activation_summary_fn=youtube_channel_activation_summary,
            normalize_int=_normalize_int,
        ),
        market,
    )
    result = build_strict_search_result(
        plan=plan,
        page_result=page_result,
        page_state=page_state,
        enrichment=enrichment,
        items=items,
        market=market,
        quota_metadata=youtube_quota_metadata,
        activation_coverage=youtube_activation_coverage,
        query_anchor_signals=query_anchor_signals,
    )
    result["metadata"]["market_verification"] = market_verification_summary(items)
    return result


async def _youtube_fast_result(
    normalized_platform: str,
    search_query: str,
    *,
    market: str,
    safe_limit: int,
    relevance_language: str,
    strict_evidence: bool,
    page_cursor: Any,
    exact_query: bool,
) -> tuple[bool, Dict[str, Any] | None]:
    """Run the existing YouTube fast path and report whether it is terminal."""

    if normalized_platform != "youtube":
        return False, None
    fast = await (
        _youtube_data_api_strict_video_search(
            search_query,
            market=market,
            safe_limit=safe_limit,
            relevance_language=relevance_language,
            page_cursor=page_cursor,
            exact_query=exact_query,
        )
        if strict_evidence
        else _youtube_data_api_search(
            search_query,
            market=market,
            safe_limit=safe_limit,
            relevance_language=relevance_language,
            exact_query=exact_query,
        )
    )
    if fast is not None and (
        exact_query or strict_evidence or len(fast.get("items") or []) >= min(3, safe_limit)
    ):
        return True, fast
    if not exact_query:
        return False, None
    return True, {
        "status": "provider_unavailable",
        "platform": "youtube",
        "items": [],
        "message": "YouTube Data API unavailable; exact-query fallback is disabled",
        "metadata": {
            "query_mode": "exact_query_cell",
            "fallback_policy": "disabled_unforecast_provider_switch",
            "provider_calls": 0,
        },
    }


async def search_platform_content(
    platform: str,
    query: str,
    *,
    market: str = "",
    max_results: int = 25,
    relevance_language: str = "en",
    strict_evidence: bool = False,
    enrich_prefilter: Any = None,
    deadline_seconds: float | None = None,
    page_cursor: Any = None,
    exact_query: bool = False,
) -> Dict[str, Any]:
    """Search public platform content and normalize it into KOL candidates.

    This returns real provider results only. If the Apify provider is not
    configured or a platform search actor is unavailable, the status says so
    explicitly instead of fabricating rows.

    车道 2·A2 新增两个**可选**参数(缺省 = 旧行为逐字不变):
    - ``enrich_prefilter(probe) -> bool``:IG 富化前的单调闸,True=这条候选无论
      富化与否都会被下游丢弃 → 不为它烧 profile-scraper 配额。口径见
      ``account_search_instagram.instagram_enrich_targets`` 的文档(只许传单调闸)。
    - ``deadline_seconds``:本条腿的总时间预算。hashtag 阶段吃完预算后,富化阶段
      诚实跳过(候选照常返回、followers 未知 → 读端归「分析中」),而不是把整条腿
      拖过 deadline 变成「本轮该平台无供给」——**少个 followers 好过整腿归零**。

    车道 2·分页(可选 ``page_cursor``,缺省 = 第一页 = 旧行为逐字不变):
    只有 YouTube 严格视频路是**真分页**(Data API search.list 的 pageToken/nextPageToken)。
    IG hashtag actor 与 clockworks TikTok actor 的输入 schema 里**没有** offset/cursor/
    page/skip 任何一个字段(2026-08-25 逐个核对),所以这两条腿的 metadata 一律
    ``pagination_supported=False`` + ``has_more=False``,绝不伪造游标假装还能翻页。
    """
    leg_started_monotonic = time.monotonic()
    normalized_platform = (platform or "youtube").strip().lower()
    normalized_query = (query or "").strip()
    safe_limit = max(1, min(int(max_results or 25), 100))
    if not normalized_query:
        return {"status": "invalid_query", "items": [], "message": "query is required"}
    if normalized_platform in {"x", "twitter", "reddit"}:
        from app.services.intelligence.account_search_secondary import search_secondary_people
        result = await search_secondary_people(
            normalized_platform, normalized_query, market=market, max_results=safe_limit,
            deadline_seconds=deadline_seconds, page_cursor=page_cursor,
        )
        result["items"] = _verify_market(result.get("items", []), market)
        return result
    search_query = _market_query(normalized_query, market)

    # YouTube Data API 快路不足三条时继续走 Apify 补深。
    fast_is_terminal, fast_result = await _youtube_fast_result(
        normalized_platform,
        search_query,
        market=market,
        safe_limit=safe_limit,
        relevance_language=relevance_language,
        strict_evidence=strict_evidence,
        page_cursor=page_cursor,
        exact_query=exact_query,
    )
    if fast_is_terminal:
        return fast_result or {}

    if not _scan_service().provider_ready():
        return {"status": "provider_unavailable", "items": [], "message": "APIFY_TOKEN is not configured"}

    runtime_deps = _content_runtime_dependencies()
    plan, plan_error = build_actor_plan(
        normalized_platform,
        search_query,
        safe_limit,
        exact_query=exact_query,
        deps=runtime_deps,
    )
    if plan_error is not None:
        return plan_error
    if plan is None:  # defensive only; build_actor_plan pairs every miss with an error
        return {"status": "unsupported_platform", "items": []}

    started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    from app.services.intelligence.account_scan_outcome import ActorRunError
    actor_failure = None
    try:
        raw_items = await _scan_service()._run_actor(
            plan.actor_id, plan.payload, timeout=plan.timeout,
        )
    except ActorRunError as exc:
        actor_failure = exc.as_result(normalized_platform, query=normalized_query, market=market)
        raw_items = exc.partial_items
        if not raw_items:
            return actor_failure
    prepared = await prepare_actor_items(
        normalized_platform,
        raw_items,
        safe_limit=safe_limit,
        enrich_prefilter=enrich_prefilter,
        deadline_seconds=deadline_seconds,
        leg_started_monotonic=leg_started_monotonic,
        # A partially downloaded/unknown paid run must not trigger another
        # paid enrichment request. Keep its usable rows, with partial status.
        owner_profiles=_instagram_owner_profiles if actor_failure is None else _no_owner_profiles,
        logger=logger,
        deps=runtime_deps,
    )
    # TT authorMeta.region / IG 商家地址只在 actor 原始行 / 档案上,归一后的候选不再带,
    # 所以线索按 handle 从原始行取回(raw_country_hints),再走同一道市场核实。
    items = _verify_market(
        normalize_actor_items(
            normalized_platform,
            prepared.raw_items,
            safe_limit=safe_limit,
            market=market,
            normalized_query=normalized_query,
            actor_id=plan.actor_id,
            instagram_profiles=prepared.instagram_profiles,
            deps=runtime_deps,
        ),
        market,
        country_hints=raw_country_hints(prepared.raw_items, prepared.instagram_profiles),
    )

    result = build_actor_result(
        normalized_platform=normalized_platform,
        normalized_query=normalized_query,
        market=market,
        safe_limit=safe_limit,
        items=items,
        plan=plan,
        searched_at=started_at,
        prepared=prepared,
    )
    if actor_failure:
        result["status"] = actor_failure["status"]
        result["metadata"].update(actor_failure["metadata"])
    return result


async def _no_owner_profiles(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return {}
