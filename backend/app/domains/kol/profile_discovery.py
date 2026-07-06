"""New-creator discovery helpers for KOL Pool smart search.

This module reuses the existing platform-search provider from old Discover.
It does not create KOL Pool rows and never touches V6 Fit scoring fields.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from app.db.connection import get_conn
from app.domains.kol import history_match
from app.domains.kol import profile_recall
from app.domains.kol import search_sessions
from app.domains.kol import url_deep_crawl
from app.services.intelligence.account_scan_service import search_platform_content


# P0-6:douyin 代码层硬移除出发现支持平台(不靠 env)。抖音号天然落 {中国大陆} 排除域,
# 且无稳定海外召回价值,故从发现入口剔除;_platforms() 命中 douyin 时直接被过滤掉。


# P0-6 地区口径:排除 {中国大陆 CN / 香港 HK / 台湾 TW},匹配中文地名 + ISO 码;
# 其余国家(含海外中文博主)放行;空地区放行。发现侧/写入前过滤共用。
# 发现过滤器已抽到 discovery_filters.py(行为不变,re-export 兜调用点)。
from app.domains.kol.discovery_filters import (  # noqa: E402,F401
    SUPPORTED_DISCOVERY_PLATFORMS,
    _annotate_new_priority,
    _candidate_key,
    _discovery_brand_collab_count,
    _int,
    _is_discovery_garbage,
    _new_priority_signal,
    _platforms,
    _staff_user_id,
    _text,
    _CAMERA_SIGNAL_TERMS, _EXCLUDED_REGION_CITY_RE, _EXCLUDED_REGION_CODES,
    _EXCLUDED_REGION_RE, _HARD_AVOID_TERMS, _PERSONA_GENERIC_TERMS,
    _candidate_blob, _country_in_excluded_region, _detect_excluded_region,
    _has_camera_signal, _is_hard_avoid, _persona_avoid_terms,
    _persona_positive_terms, _persona_relevance, _persona_term_list,
)


# ── 区域语言本地化(用户令)抽到 profile_discovery_localize.py(行为不变 move + re-export,兜调用点)。
# value/缓存/翻译 helper 整簇搬出;含下划线私有名 re-export 保留外部引用不破坏。
from app.domains.kol.profile_discovery_localize import (  # noqa: E402,F401
    MARKET_LANGUAGE,
    _LANG_DISPLAY,
    _LOCALIZE_CACHE,
    _has_cjk,
    _localize_search_terms,
    _market_to_language,
)


def _profile_url_from_kol_pool_id(kol_pool_id: Any) -> str:
    parsed = _int(kol_pool_id)
    if parsed <= 0:
        return ""
    try:
        row = get_conn().execute(
            """
            SELECT profile_url, platform, handle
            FROM vkpi_kol_pool
            WHERE id=?
            """,
            (parsed,),
        ).fetchone()
    except Exception:
        return ""
    if not row:
        return ""
    data = dict(row)
    profile_url = _text(data.get("profile_url"))
    if profile_url:
        return profile_url
    platform = _text(data.get("platform")).lower()
    handle = _text(data.get("handle")).lstrip("@")
    if not platform or not handle:
        return ""
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    return ""


def _profile_url_from_item(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    for key in ("profile_url", "channel_url", "source_url"):
        value = _text(payload.get(key) or item.get(key))
        if value:
            return value
    platform = _text(payload.get("platform") or item.get("platform")).lower()
    handle = _text(payload.get("handle") or payload.get("channel_name") or item.get("handle"))
    if not platform or not handle:
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    handle = handle.lstrip("@")
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}"
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    if platform == "facebook":
        return f"https://www.facebook.com/{handle}"
    if platform == "douyin":
        return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))
    return _profile_url_from_kol_pool_id(item.get("kol_pool_id"))


def discovery_plan(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    safe_limit = max(1, min(_int(limit, 15), 50))
    resolved_platforms = _platforms(platforms, fallback=platform_hint)
    return {
        "status": "planned",
        "query": _text(query_text),
        "platforms": resolved_platforms,
        "limit": safe_limit,
        "provider_calls": False,
        "message": "new discovery is planned only; set execute_new_discovery=true to call platform providers",
    }


def profile_crawl_plan_for_session_item(
    *,
    session_id: int,
    item_id: int,
    max_posts: int = 12,
    mode: str = "profile_only",
) -> dict[str, Any]:
    item = search_sessions.get_session_item(int(session_id), int(item_id))
    item_type = _text(item.get("item_type"))
    if item_type not in {"new_creator", "existing_kol", "recall_candidate"}:
        raise ValueError("profile crawl can only run for new_creator, existing_kol, or recall_candidate items")
    profile_url = _profile_url_from_item(item)
    if not profile_url:
        raise ValueError("discovery item does not contain a usable profile URL")
    return {
        "status": "planned",
        "session_id": int(session_id),
        "item_id": int(item_id),
        "item_type": item_type,
        "profile_url": profile_url,
        "mode": mode if mode in {"profile_only", "auto", "profile_with_video", "account_deep"} else "profile_only",
        "max_posts": max(1, min(_int(max_posts, 12), 12)),
        "message": "set execute=true to crawl profile basics through the safe writer",
        "viltrox_fit_score_untouched": True,
    }


def execute_profile_crawl_for_session_item(
    *,
    session_id: int,
    item_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = body or {}
    execute = bool(body.get("execute"))
    mode = _text(body.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    max_posts = max(1, min(_int(body.get("max_posts"), 12), 12))
    plan = profile_crawl_plan_for_session_item(
        session_id=int(session_id),
        item_id=int(item_id),
        max_posts=max_posts,
        mode=mode,
    )
    if not execute:
        return {
            **plan,
            "execute": False,
            "profile_result": url_deep_crawl.dry_run_url_deep_crawl(
                {
                    "url": plan["profile_url"],
                    "execute": False,
                    "mode": mode,
                    "max_posts": max_posts,
                    "representative_video_limit": body.get("representative_video_limit") or 1,
                }
            ),
        }

    profile_result = url_deep_crawl.dry_run_url_deep_crawl(
        {
            "url": plan["profile_url"],
            "execute": True,
            "mode": mode,
            "max_posts": max_posts,
            "representative_video_limit": body.get("representative_video_limit") or 1,
        }
    )
    updated_item = search_sessions.update_item_profile_execution(
        int(session_id),
        int(item_id),
        profile_result=profile_result,
    )
    profile_flow = profile_result.get("profile_flow") if isinstance(profile_result.get("profile_flow"), dict) else {}
    return {
        **plan,
        "execute": True,
        "status": profile_flow.get("status") or profile_result.get("status") or "unknown",
        "kol_pool_id": profile_flow.get("kol_pool_id") or profile_result.get("matched_kol_pool_id"),
        "profile_result": profile_result,
        "updated_item": updated_item,
        "viltrox_fit_score_changed_ids": profile_flow.get("viltrox_fit_score_changed_ids") or profile_result.get("viltrox_fit_score_changed_ids") or [],
        "viltrox_fit_score_untouched": profile_flow.get("viltrox_fit_score_untouched") if "viltrox_fit_score_untouched" in profile_flow else profile_result.get("viltrox_fit_score_untouched"),
    }


def advance_search_session_items(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan or execute ordered profile crawl for discovery items in a session.

    This is an orchestration helper for the unified KOL input. It advances
    session items one by one through the already-safe profile flow and never
    writes V6 Fit fields directly.
    """

    body = body or {}
    execute = bool(body.get("execute"))
    limit = max(1, min(_int(body.get("limit"), 5), 15))
    max_posts = max(1, min(_int(body.get("max_posts"), 12), 12))
    mode = _text(body.get("mode") or "profile_only")
    if mode not in {"profile_only", "auto", "profile_with_video", "account_deep"}:
        mode = "profile_only"
    include_completed = bool(body.get("include_completed"))
    item_ids_raw = body.get("item_ids")
    item_ids = {
        _int(value)
        for value in (item_ids_raw if isinstance(item_ids_raw, list) else [])
        if _int(value) > 0
    }
    allowed_types_raw = body.get("item_types")
    allowed_types = {
        _text(value)
        for value in (allowed_types_raw if isinstance(allowed_types_raw, list) else [])
        if _text(value)
    } or {"new_creator", "existing_kol", "recall_candidate"}

    session = search_sessions.get_session(int(session_id))
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    terminal_statuses = {"ready", "queued", "running", "already_queued", "already_analyzed"}
    for item in session.get("items") or []:
        item_id = _int(item.get("id"))
        item_type = _text(item.get("item_type"))
        item_status = _text(item.get("status"))
        if item_ids and item_id not in item_ids:
            continue
        if item_type not in allowed_types:
            continue
        if item_type not in {"new_creator", "existing_kol", "recall_candidate"}:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "unsupported_item_type", "item_type": item_type})
            continue
        if not include_completed and item_status in terminal_statuses:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "already_terminal", "item_status": item_status})
            continue
        profile_url = _profile_url_from_item(item)
        if not profile_url:
            skipped.append({"item_id": item_id, "status": "skipped", "reason": "missing_profile_url", "item_status": item_status})
            continue
        candidates.append(item)

    selected = candidates[:limit]
    overflow = max(0, len(candidates) - len(selected))
    items: list[dict[str, Any]] = []
    counts: dict[str, int] = {"planned": 0, "executed": 0, "ready": 0, "partial": 0, "failed": 0, "skipped": len(skipped), "errors": 0}
    changed_ids: list[int] = []

    for item in selected:
        item_id = _int(item.get("id"))
        try:
            if not execute:
                plan = profile_crawl_plan_for_session_item(
                    session_id=int(session_id),
                    item_id=item_id,
                    max_posts=max_posts,
                    mode=mode,
                )
                counts["planned"] += 1
                items.append({"item_id": item_id, "status": "planned", "plan": plan})
                continue

            result = execute_profile_crawl_for_session_item(
                session_id=int(session_id),
                item_id=item_id,
                body={**body, "execute": True, "max_posts": max_posts, "mode": mode},
            )
            counts["executed"] += 1
            status = _text(result.get("status")).lower() or "unknown"
            if status == "ready":
                counts["ready"] += 1
            elif status in {"failed", "crawl_failed", "profile_crawl_failed"} or "failed" in status:
                counts["failed"] += 1
            else:
                counts["partial"] += 1
            for changed_id in result.get("viltrox_fit_score_changed_ids") or []:
                parsed = _int(changed_id)
                if parsed > 0 and parsed not in changed_ids:
                    changed_ids.append(parsed)
            items.append({"item_id": item_id, "status": status, "result": result})
        except Exception as exc:
            counts["errors"] += 1
            items.append({"item_id": item_id, "status": "error", "reason": str(exc)[:500]})

    skipped.extend(
        {
            "item_id": _int(item.get("id")),
            "status": "skipped",
            "reason": "over_limit",
            "item_status": _text(item.get("status")),
        }
        for item in candidates[limit:]
    )
    counts["skipped"] = len(skipped)

    batch_status = "planned"
    if execute:
        if counts["failed"] or counts["errors"]:
            batch_status = "partial" if counts["ready"] or counts["partial"] else "failed"
        else:
            batch_status = "ready"
        search_sessions.update_session_result_summary(
            int(session_id),
            status=batch_status,
            summary_patch={
                "profile_batch_advance": {
                    "status": batch_status,
                    "mode": mode,
                    "limit": limit,
                    "selected": len(selected),
                    "overflow": overflow,
                    "counts": counts,
                    "viltrox_fit_score_changed_ids": changed_ids,
                    "viltrox_fit_score_untouched": not changed_ids,
                }
            },
        )

    return {
        "status": batch_status,
        "execute": execute,
        "session_id": int(session_id),
        "mode": mode,
        "limit": limit,
        "selected": len(selected),
        "eligible": len(candidates),
        "overflow": overflow,
        "counts": counts,
        "items": items,
        "skipped": skipped[: max(0, 50 - len(items))],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
        "provider_calls_performed": execute and bool(selected),
        "write_db": execute and bool(selected),
        "writes": ["vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs", "vkpi_kol_search_sessions", "vkpi_kol_search_session_items"] if execute and selected else [],
    }


# Apify-job queue orchestration abstracted to profile_discovery_queue.py
# (behaviour-preserving move + re-export; tracks all call points unchanged).
from app.domains.kol.profile_discovery_queue import (  # noqa: E402,F401
    cancel_search_session_advance,
    enqueue_search_session_advance,
    enqueue_smart_search_profile_advance,
)


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""

    query = _text(payload.get("query_text") or payload.get("input") or payload.get("query"))
    if not query:
        raise ValueError("smart profile advance payload missing query_text")
    # P0-1:LLM planner 改在 worker 跑(请求侧已去同步 LLM,见 vkpi_kol_pool smart-search 端点)。
    # payload 未带 plan 时,worker 侧补 planner:拿英文 search_query(治中文 query 捞中文圈)+ persona。
    # 失效则退原 query(管线既有 rule_v0 英文兜底)。本管线本就同步阻塞跑 recall,planner 同步调用一致。
    if not payload.get("product_focus") and not _text(payload.get("target_persona")) and not payload.get("_worker_planned"):
        try:
            from app.domains.kol import smart_query_planner as _sqp
            _plan = _sqp.plan_text_query(query, body=payload, staff=None)
            _eff = _text(_plan.get("search_query"))
            if _eff:
                query = _eff
            payload["product_focus"] = _plan.get("product_focus")
            payload["target_persona"] = _text(_plan.get("target_persona"))
            for _k in ("creator_quota", "reviewer_quota", "new_discovery_limit"):
                if payload.get(_k) is None and _plan.get(_k) is not None:
                    payload[_k] = _plan.get(_k)
            payload["_worker_planned"] = True
            payload["query_plan_source"] = "llm_plan"
        except Exception:
            # 诚实标注:planner 抛错 → 退 rule_v0 英文兜底(行为不变),仅记录走了哪条路。
            payload["query_plan_source"] = "rule_v0_fallback"
    recall_result = profile_recall.recall_kol_profiles(
        query_text=query,
        product_sku=_text(payload.get("product_sku")),
        candidate_limit=max(1, min(_int(payload.get("candidate_limit"), 100), 500)),
        limit=max(1, min(_int(payload.get("limit"), 30), 50)),
        creator_quota=max(0, min(_int(payload.get("creator_quota"), 15), 50)),
        reviewer_quota=max(0, min(_int(payload.get("reviewer_quota"), 15), 50)),
        ratio_policy=_text(payload.get("ratio_policy") or "soft"),
        mixed_policy=_text(payload.get("mixed_policy") or "dominant"),
        dedupe=bool(payload.get("dedupe", True)),
        vector_weight=float(payload.get("vector_weight") if payload.get("vector_weight") is not None else 0.7),
        type_weight=float(payload.get("type_weight") if payload.get("type_weight") is not None else 0.3),
        type_boost_enabled=bool(payload.get("type_boost_enabled", True)),
        exclude_chinese=bool(payload.get("exclude_chinese", True)),
        product_focus=payload.get("product_focus"),
        target_persona=_text(payload.get("target_persona")),
    )
    recall_session = search_sessions.attach_recall_result(int(session_id), recall_result)
    new_discovery: dict[str, Any] | None = None
    if bool(payload.get("include_new_discovery", True)):
        # persona 检索词原料:payload 有 product_focus/target_persona(来自 llm_query_plan);
        # verticals/ideal_creator_types/avoid_types 不在 payload → 用 product_sku 实时兜底取(只读 KB,零 LLM)。
        _persona_kb: dict[str, Any] = {}
        _sku = _text(payload.get("product_sku"))
        if _sku:
            try:
                from app.domains.costs import product_persona as _product_persona_kb
                _persona_kb = _product_persona_kb.get_product_persona(_sku) or {}
            except Exception:
                _persona_kb = {}
        new_discovery = await discover_new_creators(
            query_text=query,
            platforms=payload.get("new_discovery_platforms") or payload.get("discovery_platforms"),
            platform_hint=_text(payload.get("platform")),
            market=_text(payload.get("market") or payload.get("country")),
            limit=max(1, min(_int(payload.get("new_discovery_limit"), 15), 50)),
            per_platform_limit=max(1, min(_int(payload.get("new_discovery_per_platform_limit"), 15), 50)),
            search_query_en=query,  # pipeline 入参 query 已是 effective_query(planner 英文 search_query;失效退 rule_v0 英文兜底)
            product_focus=payload.get("product_focus"),
            ideal_creator_types=_persona_kb.get("ideal_creator_types_json"),
            verticals=_persona_kb.get("verticals_json"),
            avoid_types=_persona_kb.get("avoid_types_json"),
            target_persona=_text(payload.get("target_persona")),
        )
        # 收口路①-4:新人优先展示信号(新发现/低合作/成长期加权,饱和大号降位)。纯展示透出,
        # 绝不写 viltrox_fit_score / 不改 rule_v0;注解后再 attach(库内召回的 display_rank_score 已在 recall 侧产出)。
        new_discovery = _annotate_new_priority(new_discovery)
        search_sessions.attach_new_discovery_result(int(session_id), new_discovery)

    advance_result = advance_search_session_items(
        session_id=int(session_id),
        body={
            **payload,
            "execute": True,
            "limit": max(1, min(_int(payload.get("advance_limit") or payload.get("profile_advance_limit"), 15), 15)),
            "max_posts": max(1, min(_int(payload.get("max_posts"), 12), 12)),
            "mode": _text(payload.get("advance_mode") or payload.get("mode") or "account_deep"),
            "item_types": payload.get("item_types") or ["new_creator", "existing_kol", "recall_candidate"],
            "include_completed": bool(payload.get("include_completed")),
        },
    )
    changed_ids = [
        _int(value)
        for value in (advance_result.get("viltrox_fit_score_changed_ids") or [])
        if _int(value) > 0
    ]
    # 收口路①-2:搜索拿到候选(库内召回 + 发现 + advance 补全)后,对**头部 N 个有视频证据的
    # 库内候选**异步入队内容契合深析(「思考中」段)。控量(top N + 有证据 + cache 复用 + 去重)。
    # 纯编排入队 + exposure_potential 展示计算,零烧 LLM、零写 fit。入队失败不阻断 pipeline。
    content_fit: dict[str, Any] | None = None
    if bool(payload.get("include_content_fit", True)):
        try:
            from app.domains.kol import content_fit_enqueue

            content_fit = content_fit_enqueue.enqueue_content_fit_for_session(
                session_id=int(session_id),
                product_sku=_text(payload.get("product_sku")),
                top_n=max(1, min(_int(payload.get("content_fit_top_n"), content_fit_enqueue.DEFAULT_TOP_N), content_fit_enqueue.MAX_TOP_N)),
                triggered_by_user_id=_int(payload.get("triggered_by_user_id")) or None,
            )
        except Exception as exc:
            content_fit = {"status": "error", "reason": str(exc)[:300]}
    # Lane D(用户裁令「搜索时顺带懒抓」):对搜索召回的、**缺视频**的库内候选,顺带抓少数 account_deep,
    # 成本摊到未来、按需、自动优先真被搜到的人(不一次性全量烧 $660)。入队失败不阻断 pipeline。
    video_backfill: dict[str, Any] | None = None
    if bool(payload.get("include_lazy_video_backfill", True)):
        try:
            from app.domains.kol import video_backfill_enqueue

            video_backfill = video_backfill_enqueue.enqueue_lazy_video_backfill_for_session(
                session_id=int(session_id),
                top_n=max(1, min(_int(payload.get("lazy_video_backfill_top_n"), video_backfill_enqueue.DEFAULT_TOP_N), video_backfill_enqueue.MAX_TOP_N)),
                staff=None,
            )
        except Exception as exc:
            video_backfill = {"status": "error", "reason": str(exc)[:300]}
    pipeline_status = "failed" if advance_result.get("status") == "failed" else "ready"
    search_sessions.update_session_result_summary(
        int(session_id),
        status="partial" if changed_ids or advance_result.get("status") == "partial" else pipeline_status,
        summary_patch={
            "smart_search_profile_advance_job": {
                "status": pipeline_status,
                "query_text": query,
                "recall_returned": len(recall_result.get("items") or []),
                "new_discovery_status": (new_discovery or {}).get("status") if new_discovery else "not_requested",
                "advance_status": advance_result.get("status"),
                "advance_counts": advance_result.get("counts"),
                # 内容契合入队状态(「思考中」桶进度):入队数 / 跳过原因,纯展示透出。
                "content_fit_status": (content_fit or {}).get("status") if content_fit else "not_requested",
                "content_fit_enqueued": (content_fit or {}).get("enqueued_count") if content_fit else 0,
                "viltrox_fit_score_changed_ids": changed_ids,
                "viltrox_fit_score_untouched": not changed_ids,
                # 诚实信号:本次走 LLM planner('llm_plan')还是 rule_v0 英文兜底('rule_v0_fallback');
                # 未尝试规划(已带 product_focus/persona)则为 None。前端据此如实告知用户。
                "query_plan_source": payload.get("query_plan_source"),
            }
        },
    )
    return {
        "status": pipeline_status,
        "session_id": int(session_id),
        "query": query,
        "query_plan_source": payload.get("query_plan_source"),
        "content_fit": content_fit,
        "recall": {
            "method": recall_result.get("method"),
            "returned_count": len(recall_result.get("items") or []),
            "diagnostics": recall_result.get("diagnostics"),
            "search_session": recall_session,
        },
        "new_discovery": new_discovery,
        "advance": advance_result,
        "provider_calls_performed": True,
        "write_db": True,
        "writes": ["vkpi_kol_search_sessions", "vkpi_kol_search_session_items", "vkpi_kol_pool", "vkpi_kol_url_deep_crawl_runs"],
        "viltrox_fit_score_changed_ids": changed_ids,
        "viltrox_fit_score_untouched": not changed_ids,
    }


def _dedupe_enrolled_row_best_effort(enroll_result: Any) -> None:
    """L6:enroll 落库后跑去重 hook(最佳努力)。

    从 write_kol_profile_basics 返回里取写入行 id,调 pool_merge.dedupe_enrolled_pool_row:
    email 强信号自动合并(走 apply_merge 带 fit 守卫)、模糊信号只进人工清单(不写)。
    env(KOL_AUTO_DEDUP_ENROLL)可关。任何异常静默吞(只 debug),绝不阻断 enroll。
    """
    import os

    if str(os.getenv("KOL_AUTO_DEDUP_ENROLL", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        if not isinstance(enroll_result, dict):
            return
        pool_id = enroll_result.get("kol_pool_id")
        if not pool_id:
            return
        from app.domains.kol.pool_merge import dedupe_enrolled_pool_row

        dedupe_enrolled_pool_row(int(pool_id), auto_merge=True)
    except Exception:
        logger.debug("auto_dedup_enroll skip", exc_info=True)


def _auto_enroll_discoveries(new_creators: list[dict[str, Any]]) -> int:
    """把本次「全网新发现」的人即时轻量入库,治去重根因(用户口径:「抓到自动入库就不会再重复出现」)。

    发现项原本不落库 → 下次同/近似搜索 find_history_match 命中不到 → 反复以「新人」出现在「全网新发现」。
    这里逐个 upsert 到 vkpi_kol_pool(仅 platform/handle/avatar/bio/followers 等 profile-basics),
    下次即被归到「库内已有」、不再重复。
    redline-safe:走 write_kol_profile_basics——其 score 守卫会在任何 fit 变动时回滚,结构上不可能动评分域。
    最佳努力:env(KOL_AUTO_ENROLL_DISCOVERY)可关、单条失败只记日志不抛、绝不阻断发现主流程。返回入库条数。
    """
    import os

    if str(os.getenv("KOL_AUTO_ENROLL_DISCOVERY", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return 0
    if not new_creators:
        return 0
    try:
        from app.domains.kol.profile_basics import write_kol_profile_basics
    except Exception:
        return 0
    enrolled = 0
    full_ignited = 0  # full 档单次入库封顶(防一次大搜索烧穿;超出者降 light,预算闸另兜底)
    _FULL_CAP = 10
    for item in new_creators:
        if item.get("history_kol_pool_id") or item.get("kol_pool_id"):
            continue  # 已是库内行 → 不重复入库
        platform = _text(item.get("platform"))
        handle = _text(item.get("handle") or item.get("channel_handle") or item.get("username"))
        if not platform or not handle:
            continue
        profile_data = {
            "platform": platform,
            "handle": handle,
            "profile_url": _text(item.get("profile_url") or item.get("channel_url") or item.get("url")),
            "avatar_url": _text(item.get("avatar_url") or item.get("avatar")),
            "bio": _text(item.get("bio") or item.get("description") or item.get("snippet")),
            "followers": _int(item.get("followers") or item.get("subscriber_count") or item.get("avg_views") or 0),
        }
        try:
            _enroll_res = write_kol_profile_basics(None, profile_data, dry_run=False)
            # ⚠不要把 kol_pool_id 回写到会话项! 设计不变量(search_sessions.approve_session 注释):
            # new_creator 入池后会话项 kol_pool_id 必须保持 NULL,否则「会话项交集」会把这些真候选
            # 全误杀 → 全网发现框整组消失(550pro2 监视器搜索 15 个新发现却 0 显示的真因)。
            enrolled += 1
            # L6 去重 hook:落库后立即为该行找跨平台同一人。email 强信号自动合并、模糊只进人工清单。
            # 最佳努力:apply_merge 自带 fit 守卫;任何异常吞掉只记日志,绝不阻断 enroll 主流程。
            _dedupe_enrolled_row_best_effort(_enroll_res)
            # 发现即建档(B+A 合体):按相关度分档自动点火完整档案——高相关 full(深爬3帖+评论,
            # 受众/契合链自动跟),其余 light(深爬1帖)。best-effort 绝不阻断 enroll。
            try:
                _pid = (_enroll_res or {}).get("kol_pool_id") if isinstance(_enroll_res, dict) else None
                if _pid:
                    from app.domains.discovery.buildout import ignite_profile_buildout

                    _score = 0.0
                    for _k in ("recall_rank_score", "relevance_score", "score", "vector_score"):
                        try:
                            _score = float(item.get(_k) or 0)
                        except (TypeError, ValueError):
                            _score = 0.0
                        if _score:
                            break
                    _demoted = _text(item.get("relevance_tier_hint")) == "demote" or _text(item.get("relevance_tier")) == "demote"
                    _res = ignite_profile_buildout(
                        int(_pid),
                        score=_score,
                        demoted=_demoted or full_ignited >= _FULL_CAP,
                        source="smart_search_discovery",
                    )
                    if _res.get("tier") == "full":
                        full_ignited += 1
            except Exception:
                logger.info("discovery buildout ignite skip(不阻断 enroll)", exc_info=True)
        except Exception as exc:
            logger.info("auto_enroll_discovery skip handle=%r: %s", handle, str(exc)[:200])
    if enrolled:
        logger.info("auto_enroll_discovery enrolled=%d into vkpi_kol_pool", enrolled)
    return enrolled


async def discover_new_creators(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    market: str = "",
    limit: int = 15,
    per_platform_limit: int = 15,
    search_query_en: str = "",
    product_focus: Any = None,
    ideal_creator_types: Any = None,
    verticals: Any = None,
    avoid_types: Any = None,
    target_persona: str = "",
) -> dict[str, Any]:
    """Search platforms for creator candidates and mark existing KOL matches.

    发现精准修:search_query_en(英文平台检索词,优先于中文 query_text 用于实际平台搜索,治
    中文 query 捞中文圈);product_focus/ideal_creator_types/verticals/avoid_types 供 per-item
    persona 启发式相关度打分(纯本地字符串比对,零 LLM/零 Apify,无需预算闸)。全 default,既有调用不破坏。"""
    query = _text(search_query_en) or _text(query_text)
    # 区域语言本地化:目标市场非英语区 → 英文检索词翻成该区语言搜平台(捞本地达人),relevanceLanguage 同步。
    # 英语区/空 market → search_term=query、relevance_language='en',与现状完全一致(零回归)。
    _relevance_language, _region_code = _market_to_language(market)
    search_term = _localize_search_terms(query, _relevance_language)
    safe_limit = max(1, min(_int(limit, 15), 50))
    safe_per_platform = max(1, min(_int(per_platform_limit, 15), 50))
    resolved_platforms = _platforms(platforms, fallback=platform_hint)
    if not query:
        return {
            "status": "invalid_query",
            "query": query,
            "platforms": resolved_platforms,
            "items": [],
            "new_creators": [],
            "existing_matches": [],
            "provider_calls": False,
            "message": "query is required",
        }

    new_creators: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []  # 全部通过去重/garbage/地区过滤的存活候选,待 relevance 排序后再 top-N 截断
    existing_matches: list[dict[str, Any]] = []
    platform_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    # persona 检索词原料(英文优先;avoid 命中重扣)。helper 内做泛词过滤与归一,空表零影响。
    _pos_terms = _persona_positive_terms(product_focus, ideal_creator_types, verticals, search_query_en or query_text)
    _neg_terms = _persona_avoid_terms(avoid_types)
    errors: list[dict[str, Any]] = []
    _gate_dropped = {"hard_avoid": 0, "no_camera_signal": 0}  # 相机闸门丢弃计数(可观测,用于调参)

    async def _search_one_platform(platform: str) -> dict[str, Any]:
        """Run one platform search with error isolation; returns annotated items + meta.

        每平台并发(asyncio.gather)。单平台失败在此捕获、绝不传播,其余平台照常返回。
        """
        try:
            result = await search_platform_content(
                platform,
                search_term,
                market=_text(market).upper(),
                max_results=safe_per_platform,
                relevance_language=_relevance_language,
            )
        except Exception as exc:
            return {"platform": platform, "status": "failed", "message": str(exc)[:500], "annotated": [], "error": True}
        raw_items = [dict(item or {}) for item in (result.get("items") or [])]
        annotated = history_match.annotate_platform_items(raw_items, platform=platform)
        return {
            "platform": platform,
            "status": result.get("status"),
            "message": result.get("message"),
            "metadata": result.get("metadata") or {},
            "annotated": annotated,
            "error": result.get("status") not in {"done", "ready"} and not annotated,
        }

    # 并行化:YT/IG/TikTok 同时发,替代旧串行 for(一个接一个 await)。
    # return_exceptions=True 双保险——_search_one_platform 已内部捕获,这里再兜一层防御。
    platform_outcomes = await asyncio.gather(
        *[_search_one_platform(platform) for platform in resolved_platforms],
        return_exceptions=True,
    )

    # 按 resolved_platforms 原顺序合并(gather 保序),保留确定性的去重/limit 语义。
    for platform, outcome in zip(resolved_platforms, platform_outcomes):
        if isinstance(outcome, BaseException):
            errors.append({"platform": platform, "status": "failed", "message": str(outcome)[:500]})
            platform_results.append({"platform": platform, "status": "failed", "returned": 0, "metadata": {}, "message": str(outcome)[:500]})
            continue
        annotated = outcome.get("annotated") or []
        platform_results.append(
            {
                "platform": platform,
                "status": outcome.get("status"),
                "returned": len(annotated),
                "metadata": outcome.get("metadata") or {},
                "message": outcome.get("message"),
            }
        )
        if outcome.get("error"):
            errors.append({"platform": platform, "status": outcome.get("status"), "message": outcome.get("message")})
        for item in annotated:
            key = _candidate_key(item, platform)
            if key in seen:
                continue
            seen.add(key)
            if item.get("historical_match") or item.get("history_kol_pool_id"):
                existing_matches.append(item)
                continue
            # P0-6 修:地区排除判据改扫**真正带地区信号的文本字段**(sample_title/channel_name/handle);
            # 发现 item 无 per-item country、market 恒=搜索市场 US,旧判据三参全空 → 形同虚设。
            # 口径保留海外华人(马六甲=马来西亚/新加坡/海外华人摄影师不排,只排 CN大陆/HK/TW 强信号)。
            _region = _detect_excluded_region(item)
            if _is_discovery_garbage(item) or _region:
                continue
            # 相机/视觉创作者闸门(用户硬要求:得有相机、得需要拍摄)。全新发现(无库内历史匹配,
            # 上方 existing_matches 已先行 continue)零相机信号 → 真丢弃。red line:只丢,绝不杜撰分。
            if _is_hard_avoid(item, _neg_terms):
                _gate_dropped["hard_avoid"] += 1
                continue
            if not _has_camera_signal(item):
                _gate_dropped["no_camera_signal"] += 1
                continue
            # persona 启发式相关度(纯本地零 LLM):写 item['score']/relevance_score/relevance_tier;
            # 先全收集到 survivors,循环外按 relevance 降序排序再 top-N 截断(否则按到达顺序砍掉高相关项)。
            item.update(_persona_relevance(item, pos_terms=_pos_terms, neg_terms=_neg_terms))
            survivors.append(item)

    # 相机闸门可观测:单行 INFO,丢弃明细(诚实——被丢=结果中静默缺席,非杜撰分)。便于调参。
    _total_dropped = _gate_dropped["hard_avoid"] + _gate_dropped["no_camera_signal"]
    if _total_dropped:
        logger.info(
            "camera_relevance_gate dropped=%d hard_avoid=%d no_camera_signal=%d survivors=%d query=%r",
            _total_dropped, _gate_dropped["hard_avoid"], _gate_dropped["no_camera_signal"],
            len(survivors), query,
        )
    # relevance 降序排序 → top-N 截断。red line:relevance 是独立展示信号,绝不并入 viltrox_fit_score / rule_v0。
    survivors.sort(key=lambda it: float(it.get("relevance_score") or 0.0), reverse=True)
    # 平台轮转截断(platform_round_robin,2026-07-02 用户令):此前全局 relevance 排序取 top-N,
    # YouTube 元数据富、分普遍更高 → 单平台屠榜。现按平台分组(组内保持 relevance 降序),
    # YT/IG/TT 轮流各取一个直到装满;某平台弹尽由其余平台自然补位。
    # red line 不变:relevance 是独立展示信号,绝不并入 viltrox_fit_score / rule_v0。
    _by_platform: dict[str, list[dict[str, Any]]] = {}
    for item in survivors:
        _by_platform.setdefault(_text(item.get("platform")).lower(), []).append(item)
    _order = [p for p in resolved_platforms if _by_platform.get(p)]
    for _extra in _by_platform:
        if _extra not in _order:
            _order.append(_extra)
    _cursor = {p: 0 for p in _order}
    while len(new_creators) < safe_limit and _order:
        _progressed = False
        for _p in _order:
            if len(new_creators) >= safe_limit:
                break
            _lst = _by_platform.get(_p) or []
            if _cursor[_p] < len(_lst):
                new_creators.append(_lst[_cursor[_p]])
                _cursor[_p] += 1
                _progressed = True
        if not _progressed:
            break

    # B 去重根治:把本次全网新发现即时轻量入库,下次同/近似搜索归「库内已有」、不再重复
    # (用户口径:「抓到自动入库就不会再出现这个状况」)。best-effort 同步小写,失败不阻断发现。
    # K3 正账(2026-07-03):接住返回值(此前被丢弃)记进 counts.auto_enrolled,
    # attach_new_discovery_result 会把 counts 原样透传进会话 result_summary → 前端显示真实入库数。
    auto_enrolled_count = 0
    try:
        auto_enrolled_count = _auto_enroll_discoveries(new_creators)
    except Exception as exc:
        logger.info("auto_enroll_discovery batch skipped: %s", str(exc)[:200])

    status = "ready" if new_creators or existing_matches else "empty"
    if errors and (new_creators or existing_matches):
        status = "partial"
    elif errors:
        status = "failed"
    return {
        "status": status,
        "query": query,
        "platforms": resolved_platforms,
        "market": _text(market).upper(),
        "limit": safe_limit,
        "per_platform_limit": safe_per_platform,
        "items": [*existing_matches, *new_creators],
        "new_creators": new_creators,
        "existing_matches": existing_matches,
        "counts": {
            "new_creators": len(new_creators),
            "existing_matches": len(existing_matches),
            # K3:本次真实自动入库条数(_auto_enroll_discoveries 逐条 upsert 的成功数;
            # 缺 handle/入库失败/已在库的项不计)。前端据此显示真数,不再拿发现数冒充入库数。
            "auto_enrolled": auto_enrolled_count,
            "platforms": len(resolved_platforms),
            "errors": len(errors),
        },
        "platform_results": platform_results,
        "errors": errors,
        "provider_calls": True,
    }
