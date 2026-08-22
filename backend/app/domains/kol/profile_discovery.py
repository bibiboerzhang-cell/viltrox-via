"""Public compatibility surface for KOL Pool profile discovery.

Implementations are split by responsibility while this module retains the
established imports and monkeypatch points used by routers, workers, and tests.
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import history_match, profile_recall, search_sessions, url_deep_crawl
from app.domains.kol import profile_discovery_pipeline as _pipeline_impl
from app.domains.kol import profile_discovery_provider as _provider_impl
from app.domains.kol import profile_discovery_session as _session_impl
from app.domains.kol.discovery_filters import (  # noqa: F401
    LOW_REACH_FLAG_LIKE_PATTERN,
    SUPPORTED_DISCOVERY_PLATFORMS,
    _CAMERA_SIGNAL_TERMS,
    _EXCLUDED_REGION_CITY_RE,
    _EXCLUDED_REGION_CODES,
    _EXCLUDED_REGION_RE,
    _HARD_AVOID_TERMS,
    _PERSONA_GENERIC_TERMS,
    _annotate_new_priority,
    _below_reach_floor,
    _brand_official_verdict,
    _candidate_blob,
    _candidate_key,
    _competitor_brand_official,
    _dynamic_brand_official,
    _country_in_excluded_region,
    _detect_excluded_region,
    _discovery_brand_collab_count,
    _has_camera_signal,
    _int,
    _is_bio_irrelevant,
    _is_discovery_garbage,
    _is_hard_avoid,
    _new_priority_signal,
    _persona_avoid_terms,
    _persona_positive_terms,
    _persona_relevance,
    _persona_term_list,
    _platforms,
    _reach_display_state,
    _reach_floor_enabled,
    _reach_floor_reason,
    _staff_user_id,
    _text,
)
from app.domains.kol.profile_discovery_candidates import (  # noqa: F401
    _OWN_BRAND_PREFIXES,
    _PLATFORM_ALIASES,
    _PLATFORM_HOSTS,
    _candidate_platform_signals,
    _is_own_brand_account,
    _normalize_discovery_platform,
    _strict_discovery_platforms,
    explicit_platforms_from_query,
    filter_recall_result_platforms,
    explicit_market_constraint,
    filter_recall_result_market,
    normalize_market_constraint,
    resolve_market_constraint,
)
from app.domains.kol.profile_discovery_localize import (  # noqa: F401
    MARKET_LANGUAGE,
    _LANG_DISPLAY,
    _LOCALIZE_CACHE,
    _has_cjk,
    _localize_search_terms,
    _market_to_language,
)
from app.domains.kol.profile_discovery_queue import (  # noqa: F401
    cancel_search_session_advance,
    enqueue_search_session_advance,
    enqueue_smart_search_profile_advance,
)
from app.services.intelligence.account_scan_service import search_platform_content

logger = logging.getLogger(__name__)


_PROVIDER_IMPLEMENTATIONS = {
    "discovery_plan": _provider_impl.discovery_plan,
    "_dedupe_enrolled_row_best_effort": _provider_impl._dedupe_enrolled_row_best_effort,
    "_auto_enroll_discoveries": _provider_impl._auto_enroll_discoveries,
    "_existing_match_pool_id": _provider_impl._existing_match_pool_id,
    "_triage_existing_matches_reach": _provider_impl._triage_existing_matches_reach,
    "discover_new_creators": _provider_impl.discover_new_creators,
}
_PROVIDER_SHARED_NAMES = (
    "LOW_REACH_FLAG_LIKE_PATTERN",
    "_brand_official_verdict",
    "_candidate_key",
    "_candidate_platform_signals",
    "_competitor_brand_official",
    "_detect_excluded_region",
    "_has_camera_signal",
    "_has_cjk",
    "_int",
    "_is_bio_irrelevant",
    "_is_discovery_garbage",
    "_is_hard_avoid",
    "_is_own_brand_account",
    "_localize_search_terms",
    "_market_to_language",
    "_persona_avoid_terms",
    "_persona_positive_terms",
    "_persona_relevance",
    "_reach_display_state",
    "_reach_floor_enabled",
    "_reach_floor_reason",
    "_staff_user_id",
    "_strict_discovery_platforms",
    "_text",
    "get_conn",
    "history_match",
    "logger",
    "search_platform_content",
)


def _sync_provider_compat() -> None:
    for name in _PROVIDER_SHARED_NAMES:
        setattr(_provider_impl, name, globals()[name])
    for name, implementation in _PROVIDER_IMPLEMENTATIONS.items():
        current = globals()[name]
        default = _PROVIDER_COMPAT_DEFAULTS[name]
        setattr(_provider_impl, name, implementation if current is default else current)


def discovery_plan(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    _sync_provider_compat()
    return _PROVIDER_IMPLEMENTATIONS["discovery_plan"](
        query_text=query_text,
        platforms=platforms,
        platform_hint=platform_hint,
        limit=limit,
    )


def _dedupe_enrolled_row_best_effort(enroll_result: Any) -> None:
    _sync_provider_compat()
    return _PROVIDER_IMPLEMENTATIONS["_dedupe_enrolled_row_best_effort"](enroll_result)


def _auto_enroll_discoveries(new_creators: list[dict[str, Any]]) -> int:
    _sync_provider_compat()
    return _PROVIDER_IMPLEMENTATIONS["_auto_enroll_discoveries"](new_creators)


def _existing_match_pool_id(item: dict[str, Any]) -> int:
    _sync_provider_compat()
    return _PROVIDER_IMPLEMENTATIONS["_existing_match_pool_id"](item)


def _triage_existing_matches_reach(
    existing_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    _sync_provider_compat()
    return _PROVIDER_IMPLEMENTATIONS["_triage_existing_matches_reach"](existing_matches)


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
    auto_enroll: bool = True,
    exclude_chinese: bool = True,
) -> dict[str, Any]:
    # 与 profile_discovery_provider.discover_new_creators 签名同集(守卫测试
    # test_profile_discovery_facade_signature);流水线经 _sync_pipeline_compat 拿到的是本壳。
    _sync_provider_compat()
    return await _PROVIDER_IMPLEMENTATIONS["discover_new_creators"](
        query_text=query_text,
        platforms=platforms,
        platform_hint=platform_hint,
        market=market,
        limit=limit,
        per_platform_limit=per_platform_limit,
        search_query_en=search_query_en,
        product_focus=product_focus,
        ideal_creator_types=ideal_creator_types,
        verticals=verticals,
        avoid_types=avoid_types,
        target_persona=target_persona,
        auto_enroll=auto_enroll,
        exclude_chinese=exclude_chinese,
    )


_PROVIDER_COMPAT_DEFAULTS = {
    "discovery_plan": discovery_plan,
    "_dedupe_enrolled_row_best_effort": _dedupe_enrolled_row_best_effort,
    "_auto_enroll_discoveries": _auto_enroll_discoveries,
    "_existing_match_pool_id": _existing_match_pool_id,
    "_triage_existing_matches_reach": _triage_existing_matches_reach,
    "discover_new_creators": discover_new_creators,
}


_SESSION_IMPLEMENTATIONS = {
    "_profile_url_from_kol_pool_id": _session_impl._profile_url_from_kol_pool_id,
    "_profile_url_from_item": _session_impl._profile_url_from_item,
    "profile_crawl_plan_for_session_item": _session_impl.profile_crawl_plan_for_session_item,
    "_enqueue_audience_enrichment": _session_impl._enqueue_audience_enrichment,
    "execute_profile_crawl_for_session_item": _session_impl.execute_profile_crawl_for_session_item,
    "advance_search_session_items": _session_impl.advance_search_session_items,
    "_profile_advance_pipeline_status": _session_impl._profile_advance_pipeline_status,
}
_SESSION_SHARED_NAMES = ("_int", "_text", "get_conn", "search_sessions", "url_deep_crawl")


def _sync_session_compat() -> None:
    for name in _SESSION_SHARED_NAMES:
        setattr(_session_impl, name, globals()[name])
    for name, implementation in _SESSION_IMPLEMENTATIONS.items():
        current = globals()[name]
        default = _SESSION_COMPAT_DEFAULTS[name]
        setattr(_session_impl, name, implementation if current is default else current)


def _profile_url_from_kol_pool_id(kol_pool_id: Any) -> str:
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["_profile_url_from_kol_pool_id"](kol_pool_id)


def _profile_url_from_item(item: dict[str, Any]) -> str:
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["_profile_url_from_item"](item)


def profile_crawl_plan_for_session_item(
    *,
    session_id: int,
    item_id: int,
    max_posts: int = 12,
    mode: str = "profile_only",
) -> dict[str, Any]:
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["profile_crawl_plan_for_session_item"](
        session_id=session_id,
        item_id=item_id,
        max_posts=max_posts,
        mode=mode,
    )


def _enqueue_audience_enrichment(kol_pool_id: int) -> dict[str, Any]:
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["_enqueue_audience_enrichment"](kol_pool_id)


def execute_profile_crawl_for_session_item(
    *,
    session_id: int,
    item_id: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["execute_profile_crawl_for_session_item"](
        session_id=session_id,
        item_id=item_id,
        body=body,
    )


def advance_search_session_items(
    *,
    session_id: int,
    body: dict[str, Any] | None = None,
    smart_local_contract: bool = False,
) -> dict[str, Any]:
    # 门面壳会被 _sync_pipeline_compat() 塞回流水线模块,签名必须与
    # profile_discovery_session 真实现保持同集;漏转发 smart_local_contract 曾让
    # 严格 30+30 搜索在 prod 直接 TypeError(「分析未完成」)。
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["advance_search_session_items"](
        session_id=session_id,
        body=body,
        smart_local_contract=bool(smart_local_contract),
    )


def _profile_advance_pipeline_status(
    recall_result: dict[str, Any],
    new_discovery: dict[str, Any] | None,
    advance_result: dict[str, Any],
) -> str:
    _sync_session_compat()
    return _SESSION_IMPLEMENTATIONS["_profile_advance_pipeline_status"](
        recall_result,
        new_discovery,
        advance_result,
    )


_SESSION_COMPAT_DEFAULTS = {
    "_profile_url_from_kol_pool_id": _profile_url_from_kol_pool_id,
    "_profile_url_from_item": _profile_url_from_item,
    "profile_crawl_plan_for_session_item": profile_crawl_plan_for_session_item,
    "_enqueue_audience_enrichment": _enqueue_audience_enrichment,
    "execute_profile_crawl_for_session_item": execute_profile_crawl_for_session_item,
    "advance_search_session_items": advance_search_session_items,
    "_profile_advance_pipeline_status": _profile_advance_pipeline_status,
}


_PIPELINE_IMPLEMENTATION = _pipeline_impl.execute_smart_search_profile_advance_pipeline
_PIPELINE_SHARED_NAMES = (
    "_annotate_new_priority",
    "_int",
    "_profile_advance_pipeline_status",
    "_text",
    "advance_search_session_items",
    "discover_new_creators",
    "explicit_platforms_from_query",
    "filter_recall_result_market",
    "filter_recall_result_platforms",
    "resolve_market_constraint",
    "profile_recall",
    "search_sessions",
)


def _sync_pipeline_compat() -> None:
    for name in _PIPELINE_SHARED_NAMES:
        setattr(_pipeline_impl, name, globals()[name])


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
    provider_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _sync_pipeline_compat()
    return await _PIPELINE_IMPLEMENTATION(
        session_id=session_id,
        payload=payload,
        provider_actor=provider_actor,
    )
