"""Provider-backed creator discovery and enrollment orchestration."""
from __future__ import annotations

import logging
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import (
    history_match,
    profile_discovery_provider_flow as provider_flow,
)
from app.domains.kol.brand_official_gate import BRAND_OFFICIAL_SKIP_REASON, discovery_wall_verdict
from app.domains.kol.discovery_enroll_intake import (
    enroll_profile_payload,
    enroll_skip_counts,
    mark_gate_rejection,
    mark_writer_skip,
)
from app.domains.kol.search_session_diagnostics import provider_gate_funnel
from app.domains.kol.discovery_filters import (
    LOW_REACH_FLAG_LIKE_PATTERN,
    _brand_official_verdict,
    _candidate_key,
    _detect_excluded_region,
    _has_camera_signal,
    _int,
    _is_bio_irrelevant,
    _is_discovery_garbage,
    _is_hard_avoid,
    _persona_avoid_terms,
    _persona_positive_terms,
    _persona_relevance,
    _reach_display_state,
    _reach_floor_enabled,
    _reach_floor_reason,
    _staff_user_id,
    _text,
)
from app.domains.kol.profile_discovery_candidates import (
    _candidate_platform_signals,
    _is_own_brand_account,
    _strict_discovery_platforms,
)
from app.domains.kol.profile_discovery_rounds import leg_cursors, pagination_state
from app.domains.kol.profile_discovery_supply import (
    build_enrich_doom_gate,
    leg_accounting,
    leg_deadline_seconds,
    resolve_platform_limit,
    run_all_legs,
    sanitize_platform_limits,
)
from app.domains.kol.profile_discovery_localize import (
    _has_cjk,
    _localize_search_terms,
    _market_to_language,
)
from app.domains.kol.identity import (
    YOUTUBE_CHANNEL_ID_RE,
    canonical_creator_aliases,
)
from app.services.intelligence.account_scan_service import search_platform_content

logger = logging.getLogger(__name__)


def _provider_handle_quality(value: Any, platform: str) -> int:
    handle = _text(value).lstrip("@")
    if not handle:
        return 0
    if platform == "youtube" and YOUTUBE_CHANNEL_ID_RE.fullmatch(handle):
        return 1
    return 2


def _merge_provider_creator_observation(
    existing: dict[str, Any],
    incoming: dict[str, Any],
    *,
    platform: str,
) -> dict[str, Any]:
    """Combine two provider rows for one observed identity without synthesis."""
    incoming_wins = _provider_handle_quality(
        incoming.get("handle"), platform
    ) > _provider_handle_quality(existing.get("handle"), platform)
    winner = dict(incoming if incoming_wins else existing)
    other = existing if incoming_wins else incoming
    for key, value in other.items():
        if winner.get(key) in (None, "", [], {}):
            winner[key] = value
    winner["platform"] = platform
    return winner


def _canonicalize_provider_candidates(
    items: list[dict[str, Any]],
    *,
    platform: str,
) -> list[dict[str, Any]]:
    """Fold UC-id/@handle/URL variants before any account-quality gate."""
    pending = [{**raw, "platform": platform} for raw in items]
    while True:
        output: list[dict[str, Any]] = []
        groups: list[tuple[set[str], int]] = []
        fallback_indexes: dict[str, int] = {}
        for item in pending:
            aliases = canonical_creator_aliases(item)
            match_index: int | None = None
            if aliases:
                for group_aliases, output_index in groups:
                    if aliases.intersection(group_aliases):
                        match_index = output_index
                        group_aliases.update(aliases)
                        break
            else:
                fallback = _candidate_key(item, platform)
                match_index = fallback_indexes.get(fallback)
            if match_index is None:
                output_index = len(output)
                output.append(item)
                if aliases:
                    groups.append((set(aliases), output_index))
                else:
                    fallback_indexes[fallback] = output_index
                continue
            output[match_index] = _merge_provider_creator_observation(
                output[match_index], item, platform=platform
            )
        if len(output) == len(pending):
            return output
        # A bridge row may join two groups that did not overlap earlier in the
        # input (UC-only, handle-only, then UC+handle).  Repeat until the
        # connected identity components are stable.
        pending = output


def discovery_plan(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    limit: int = 15,
) -> dict[str, Any]:
    safe_limit = max(1, min(_int(limit, 15), 50))
    resolved_platforms = _strict_discovery_platforms(platforms, fallback=platform_hint)
    if not resolved_platforms:
        return {
            "status": "invalid_platform",
            "query": _text(query_text),
            "platforms": [],
            "limit": safe_limit,
            "provider_calls": False,
            "message": "no supported discovery platform was selected",
        }
    return {
        "status": "planned",
        "query": _text(query_text),
        "platforms": resolved_platforms,
        "limit": safe_limit,
        "provider_calls": False,
        "message": "new discovery is planned only; set execute_new_discovery=true to call platform providers",
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

    2026-08-25:写端两道闸拦下时,被拦的项打 ``auto_enroll_skipped`` 原因标(见
    ``discovery_enroll_intake``),**不计入返回的入库条数**。函数签名不动——门面壳与
    真实现签名同集是硬约束,新增关键字参数=prod TypeError。
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
        profile_data = enroll_profile_payload(item, platform, handle)
        try:
            _enroll_res = write_kol_profile_basics(None, profile_data, dry_run=False)
            # ⚠不要把 kol_pool_id 回写到会话项! 设计不变量(search_sessions.approve_session 注释):
            # new_creator 入池后会话项 kol_pool_id 必须保持 NULL,否则「会话项交集」会把这些真候选
            # 全误杀 → 全网发现框整组消失(550pro2 监视器搜索 15 个新发现却 0 显示的真因)。
            if mark_writer_skip(item, _enroll_res):
                continue  # 建档闸拦下(品牌官号):不算入库,原因已就地留标
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
            mark_gate_rejection(item, exc)  # 闸抛错拦人也要留诚实原因标,不与「网络挂了」混为一谈
            logger.info("auto_enroll_discovery skip handle=%r: %s", handle, str(exc)[:200])
    if enrolled:
        logger.info("auto_enroll_discovery enrolled=%d into vkpi_kol_pool", enrolled)
    return enrolled


def _warm_discovery_avatar_cache(new_creators: list[dict[str, Any]], *, max_items: int = 15) -> None:
    """新面孔头像预热(2026-07-21 头像灰占位案):库内卡头像能显示的真通路 = image-proxy 磁盘缓存
    已温(入库/浏览时抓过);新面孔第一次渲染必冷抓,签名 CDN 冷抓超时/瞬时失败就只剩占位。
    这里把发现项 avatar_url 后台线程预热进同一份磁盘缓存(cache_image 幂等、短超时、逐条吞错),
    前端首屏即命中缓存。best-effort:线程 daemon、任何异常静默,绝不阻断/拖慢发现主流程。"""
    urls: list[str] = []
    seen: set[str] = set()
    for item in new_creators[: max(0, int(max_items))]:
        url = _text((item or {}).get("avatar_url"))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    if not urls:
        return

    def _warm() -> None:
        try:
            from app.domains.media import cache_image

            for url in urls:
                try:
                    cache_image(url, timeout=4)
                except Exception:
                    continue
        except Exception:
            logger.debug("discovery avatar warmup skipped", exc_info=True)

    try:
        import threading

        threading.Thread(target=_warm, name="discovery-avatar-warmup", daemon=True).start()
    except Exception:
        logger.debug("discovery avatar warmup thread not started", exc_info=True)


def _existing_match_pool_id(item: dict[str, Any]) -> int:
    """「库内已有」发现项的 pool 行 id(history_kol_pool_id 或 historical_match.kol_pool_id;缺 → 0)。"""
    pid = _int(item.get("history_kol_pool_id"))
    if pid > 0:
        return pid
    match = item.get("historical_match")
    if isinstance(match, dict):
        return max(0, _int(match.get("kol_pool_id")))
    return 0


def _triage_existing_matches_reach(
    existing_matches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """「库内已有」发现项的触达分诊(第二道闸读端,2026-07-12 两粉号案)。

    此前 existing_matches 跳过全部闸门 → 补全回填 followers=2 的行(kol_pool 12297)照样回到
    「全网新发现」面。按 pool 行现值走 _reach_display_state 单一真源三态:
    - low_reach(实时判据/low_reach 标命中)→ 丢出结果 + 计 filtered_low_reach(行保留,只挡入口);
    - unknown(followers 未知)→ 丢出展示 + 计 analyzing(分析后再 po),并 best-effort 补点火
      light 档 buildout(复用既有队列,queue 按 URL 自去重,绝不新造抓取);
    - ok → 保留。DB 不可用 → 全放行(fail-open 不误杀)。零触 viltrox_fit_score / rule_v0。
    """
    counts = {"low_reach": 0, "analyzing": 0}
    if not existing_matches or not _reach_floor_enabled():
        return existing_matches, counts
    ids = [_existing_match_pool_id(item) for item in existing_matches]
    want = sorted({pid for pid in ids if pid})
    pool_rows: dict[int, dict[str, Any]] = {}
    if want:
        try:
            placeholders = ",".join(["?"] * len(want))
            rows = get_conn().execute(
                f"""
                SELECT id, followers, avg_views, avg_comments, engagement_rate,
                       (raw_platform_data LIKE ?) AS low_reach_flagged
                FROM vkpi_kol_pool
                WHERE id IN ({placeholders})
                """,
                (LOW_REACH_FLAG_LIKE_PATTERN, *want),
            ).fetchall()
            pool_rows = {int(dict(r)["id"]): dict(r) for r in rows}
        except Exception:
            logger.warning("existing match reach triage skipped(fail-open 不误杀)", exc_info=True)
            return existing_matches, counts
    kept: list[dict[str, Any]] = []
    for item, pid in zip(existing_matches, ids):
        row = pool_rows.get(pid)
        state = _reach_display_state(row) if row else "ok"  # pool 行缺 → 放行(不误杀)
        if state == "low_reach":
            counts["low_reach"] += 1
            logger.debug(
                "discovery_existing_reach_floor_filtered handle=%r kol_pool_id=%s",
                item.get("handle"), pid,
            )
            continue
        if state == "unknown":
            counts["analyzing"] += 1
            # 补全链自动触发:followers 未知的库内行补点火 light 档(幂等;深爬回填 followers
            # 后经 profile_basics 第二道闸重过闸,达标即在会话读端自动放出)。
            try:
                from app.domains.discovery.buildout import ignite_profile_buildout

                ignite_profile_buildout(pid, score=0.0, demoted=True, source="discovery_reach_backfill")
            except Exception:
                logger.info("reach backfill ignite skip kol=%s", pid, exc_info=True)
            continue
        kept.append(item)
    return kept, counts


async def discover_new_creators(
    *,
    query_text: str,
    platforms: Any = None,
    platform_hint: str = "",
    market: str = "",
    limit: int = 15,
    per_platform_limit: int = 15,
    per_platform_limits: Any = None,
    search_query_en: str = "",
    product_focus: Any = None,
    ideal_creator_types: Any = None,
    verticals: Any = None,
    avoid_types: Any = None,
    target_persona: str = "",
    auto_enroll: bool = True,
    exclude_chinese: bool = True,
    page_cursors: Any = None,
    exact_query: bool = False,
) -> dict[str, Any]:
    """Search providers, gate candidates, and project the compatibility response.

    The public signature stays aligned with the profile_discovery facade.  The
    flow module receives live module bindings so facade and direct monkeypatches
    keep affecting provider calls, enrollment, triage, and cache warming.
    """
    plan = provider_flow.prepare_discovery_plan(
        query_text=query_text,
        platforms=platforms,
        platform_hint=platform_hint,
        market=market,
        limit=limit,
        per_platform_limit=per_platform_limit,
        per_platform_limits=per_platform_limits,
        search_query_en=search_query_en,
        product_focus=product_focus,
        ideal_creator_types=ideal_creator_types,
        verticals=verticals,
        auto_enroll=auto_enroll,
        exclude_chinese=exclude_chinese,
        page_cursors=page_cursors,
        exact_query=exact_query,
        text_value=_text,
        int_value=_int,
        market_to_language=_market_to_language,
        localize_search_terms=_localize_search_terms,
        has_cjk=_has_cjk,
        persona_positive_terms=_persona_positive_terms,
        strict_platforms=_strict_discovery_platforms,
        sanitize_limits=sanitize_platform_limits,
        resolve_limit=resolve_platform_limit,
        normalize_leg_cursors=leg_cursors,
    )
    invalid_response = provider_flow.invalid_plan_response(plan)
    if invalid_response is not None:
        return invalid_response

    plan.pos_terms = _persona_positive_terms(
        product_focus,
        ideal_creator_types,
        verticals,
        search_query_en or query_text,
    )
    plan.neg_terms = _persona_avoid_terms(avoid_types)
    enrich_prefilter = build_enrich_doom_gate(
        exclude_chinese=exclude_chinese,
        neg_terms=plan.neg_terms,
    )
    outcomes = await provider_flow.search_provider_legs(
        plan,
        enrich_prefilter=enrich_prefilter,
        search_platform=search_platform_content,
        annotate_platform_items=history_match.annotate_platform_items,
        canonicalize_candidates=_canonicalize_provider_candidates,
        platform_signals=_candidate_platform_signals,
        run_legs=run_all_legs,
        deadline_seconds=leg_deadline_seconds,
        logger=logger,
    )
    state = provider_flow.collect_discovery_state(
        plan,
        outcomes,
        account_leg=leg_accounting,
        creator_aliases=canonical_creator_aliases,
        candidate_key=_candidate_key,
        brand_gate=discovery_wall_verdict,
        detect_excluded_region=_detect_excluded_region,
        is_garbage=_is_discovery_garbage,
        is_own_brand=_is_own_brand_account,
        is_hard_avoid=_is_hard_avoid,
        has_camera_signal=_has_camera_signal,
        is_bio_irrelevant=_is_bio_irrelevant,
        reach_floor_reason=_reach_floor_reason,
        persona_relevance=_persona_relevance,
        logger=logger,
    )
    provider_flow.log_gate_summary(state, query=plan.query, logger=logger)
    new_creators = provider_flow.select_new_creators(
        state.survivors,
        platforms=plan.resolved_platforms,
        limit=plan.safe_limit,
        text_value=_text,
        int_value=_int,
    )
    effects = provider_flow.apply_discovery_effects(
        plan,
        state,
        new_creators,
        reach_state=_reach_display_state,
        triage_existing=_triage_existing_matches_reach,
        auto_enroll_discoveries=_auto_enroll_discoveries,
        warm_avatar_cache=_warm_discovery_avatar_cache,
        logger=logger,
    )
    enroll_skips = enroll_skip_counts(new_creators)
    pagination = pagination_state(state.platform_results)
    return provider_flow.project_discovery_response(
        plan,
        state,
        new_creators,
        effects,
        enroll_skips=enroll_skips,
        brand_official_skip_reason=BRAND_OFFICIAL_SKIP_REASON,
        pagination=pagination,
        build_funnel=provider_gate_funnel,
    )
