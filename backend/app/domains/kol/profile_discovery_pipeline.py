"""Queued smart-search profile advance pipeline."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.kol import (
    profile_discovery_evidence,
    profile_discovery_rounds,
    profile_online_qualification,
    profile_recall,
    profile_recall_qualification,
    recall_favorite_exclusion,
    search_session_diagnostics,
    search_sessions,
)
from app.domains.kol.discovery_filters import _annotate_new_priority, _int, _text
from app.domains.kol.profile_discovery_candidates import (
    explicit_platforms_from_query,
    filter_recall_result_market,
    filter_recall_result_platforms,
    resolve_market_constraint,
)
from app.domains.kol.profile_discovery_provider import discover_new_creators
from app.domains.kol.profile_recall_match_evidence import query_evidence_terms
from app.domains.kol.profile_discovery_session import (
    _profile_advance_pipeline_status,
    advance_search_session_items,
)
from app.domains.kol.search_progress_contract import completion_contract

logger = get_logger(__name__)


async def execute_smart_search_profile_advance_pipeline(
    *,
    session_id: int,
    payload: dict[str, Any],
    provider_actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a queued text recall/new-discovery/profile-advance pipeline."""

    query = _text(payload.get("query_text") or payload.get("input") or payload.get("query"))
    if not query:
        raise ValueError("smart profile advance payload missing query_text")
    operator_query = query
    # 证据埋点:planner 会把解析出的 SKU 回填进 payload,之后就分不清「操作员点的产品」
    # 和「模型猜的产品」了 —— 锚的来源必须在改写之前拍照。纯读,零副作用。
    operator_anchor = profile_discovery_evidence.operator_anchor_inputs(payload)
    operator_platforms = explicit_platforms_from_query(operator_query)
    operator_market = resolve_market_constraint(
        operator_query,
        payload.get("market") or payload.get("country"),
    )
    if operator_platforms:
        payload["platforms"] = operator_platforms
        payload["new_discovery_platforms"] = operator_platforms
    if operator_market:
        payload["market"] = operator_market
    # P0-1:LLM planner 改在 worker 跑(请求侧已去同步 LLM,见 vkpi_kol_pool smart-search 端点)。
    # payload 未带 plan 时,worker 侧补 planner:拿英文 search_query(治中文 query 捞中文圈)+ persona。
    # 失效则退原 query(管线既有 rule_v0 英文兜底)。本管线本就同步阻塞跑 recall,planner 同步调用一致。
    if payload.get("_worker_planned") is not True:
        # Queue input ultimately originates in an HTTP body.  Product/persona
        # plan fields are therefore hints at most, never an authorization or
        # catalog-validation boundary.  Clear them before planning so an
        # injected preview plan cannot bypass unknown/conflicting SKU guards.
        for untrusted_plan_key in (
            "product_focus",
            "target_persona",
            "resolved_product",
            "llm_query_plan",
            "query_plan_source",
        ):
            payload.pop(untrusted_plan_key, None)
        from app.domains.kol import smart_query_planner as _sqp

        # The provider-free catalog pass is the mandatory identity guard.  If
        # it cannot run, the job fails closed; if the richer planner fails, we
        # retain this validated plan instead of reverting to unanchored input.
        _guard_plan = _sqp.plan_text_query_provider_free(query, body=payload)
        try:
            _plan = _sqp.plan_text_query(query, body=payload, staff=None)
            _plan_source = "llm_plan"
        except Exception:
            _plan = _guard_plan
            _plan_source = "provider_free_guard_fallback"
        if _text(_guard_plan.get("status")) == "needs_clarification":
            _plan = _guard_plan
        else:
            guard_query = _text(_guard_plan.get("search_query"))
            guard_terms = set(query_evidence_terms(guard_query))
            rich_terms = set(query_evidence_terms(_plan.get("search_query")))
            # A fluent but wrong-specific rich plan is more dangerous than a
            # generic plan: even partial overlap (for example lens/fashion)
            # must not drop a catalog-required anchor such as review.  The
            # rich plan is usable only when it preserves every guard term.
            if guard_terms and not guard_terms.issubset(rich_terms):
                _plan = {
                    **_plan,
                    "search_query": guard_query,
                    "evidence_anchor_source": "provider_free_guard",
                }
                _plan_source = f"{_plan_source}_with_guard_anchors"
            elif not rich_terms:
                _plan = _sqp._require_evidence_anchor(_guard_plan)
        if _text(_plan.get("status")) == "needs_clarification":
            clarification_contract = completion_contract(
                base_count=0,
                total=0,
                terminal_count=0,
                ready_count=0,
            )
            search_sessions.update_session_result_summary(
                int(session_id),
                status="partial",
                summary_patch={
                    "phase": "partial",
                    "progress": {
                        "base": 0,
                        "total": 0,
                        "profile_ready": 0,
                        "profile_failed": 0,
                        "complete_ready": 0,
                        "complete_partial": 0,
                        **clarification_contract,
                    },
                    **clarification_contract,
                    "llm_query_plan": _plan,
                    "smart_search_profile_advance_job": {
                        "status": "needs_clarification",
                        "query_text": query,
                        "advance_status": "not_started",
                        "viltrox_fit_score_untouched": True,
                    },
                },
            )
            return {
                "status": "needs_clarification",
                "session_id": int(session_id),
                "query": query,
                "query_plan_source": "product_catalog_guard",
                "llm_query_plan": _plan,
                "recall": {"method": "product_catalog_guard", "returned_count": 0, "diagnostics": {}},
                "new_discovery": None,
                "advance": {"status": "not_started", "selected": 0, "counts": {}},
                "provider_calls_performed": False,
                "write_db": True,
                "writes": ["vkpi_kol_search_sessions"],
                "viltrox_fit_score_changed_ids": [],
                "viltrox_fit_score_untouched": True,
            }
        _eff = _text(_plan.get("search_query"))
        if _eff:
            query = _eff
        payload["product_focus"] = _plan.get("product_focus")
        payload["target_persona"] = _text(_plan.get("target_persona"))
        payload["resolved_product"] = _plan.get("resolved_product")
        payload["llm_query_plan"] = _plan
        if not payload.get("product_sku") and isinstance(_plan.get("resolved_product"), dict):
            payload["product_sku"] = _text(_plan["resolved_product"].get("sku"))
        if not operator_platforms and not any(
            payload.get(key)
            for key in ("platforms", "platform", "discovery_platforms", "new_discovery_platforms")
        ):
            payload["platforms"] = []
        for _k in ("creator_quota", "reviewer_quota", "new_discovery_limit"):
            if payload.get(_k) is None and _plan.get(_k) is not None:
                payload[_k] = _plan.get(_k)
        payload["_worker_planned"] = True
        payload["query_plan_source"] = _plan_source
    recall_filters = dict(payload.get("filters") or {}) if isinstance(payload.get("filters"), dict) else {}
    resolved_platforms = (
        operator_platforms
        or payload.get("platforms")
        or payload.get("new_discovery_platforms")
        or payload.get("discovery_platforms")
        or payload.get("platform")
    )
    if resolved_platforms and not recall_filters.get("platforms"):
        recall_filters["platforms"] = resolved_platforms
    normalized_market = operator_market
    recall_result = profile_recall.recall_kol_profiles(
        query_text=query,
        product_sku=_text(payload.get("product_sku")),
        candidate_limit=profile_recall_qualification.SMART_LOCAL_CANDIDATE_LIMIT,
        limit=profile_recall_qualification.SMART_LOCAL_TARGET,
        creator_quota=max(0, min(_int(payload.get("creator_quota"), 15), 50)),
        reviewer_quota=max(0, min(_int(payload.get("reviewer_quota"), 15), 50)),
        ratio_policy=_text(payload.get("ratio_policy") or "soft"),
        mixed_policy=_text(payload.get("mixed_policy") or "dominant"),
        dedupe=True,
        vector_weight=float(payload.get("vector_weight") if payload.get("vector_weight") is not None else profile_recall_qualification.SMART_LOCAL_VECTOR_WEIGHT),
        type_weight=float(payload.get("type_weight") if payload.get("type_weight") is not None else profile_recall_qualification.SMART_LOCAL_TYPE_WEIGHT),
        type_boost_enabled=bool(payload.get("type_boost_enabled", True)),
        exclude_chinese=bool(payload.get("exclude_chinese", True)),
        product_focus=payload.get("product_focus"),
        target_persona=_text(payload.get("target_persona")),
        filters=recall_filters,
        search_strategy=_text(payload.get("search_strategy") or "balanced"),
        bucket_policy=payload.get("bucket_policy") if isinstance(payload.get("bucket_policy"), dict) else None,
        # Search-session results must have field evidence; follower-based
        # popularity refill is not evidence for the operator's query.
        allow_backfill=False,
        operator_query_text=operator_query,
        required_product_evidence_terms=payload.get("resolved_product"),
        local_qualification_policy=profile_recall_qualification.smart_local_policy(
            market=normalized_market,
            platforms=resolved_platforms,
            languages=payload.get("languages") or payload.get("content_languages"),
            profile_types=payload.get("profile_types") or payload.get("kol_types"),
        ),
    )
    recall_result = filter_recall_result_platforms(
        recall_result,
        recall_filters.get("platforms"),
    )
    recall_result = filter_recall_result_market(
        recall_result,
        normalized_market,
    )
    recall_result = profile_recall_qualification.project_smart_local_result(recall_result)
    if isinstance(payload.get("llm_query_plan"), dict):
        recall_result["llm_query_plan"] = payload["llm_query_plan"]
    recall_items = recall_result.get("items") if isinstance(recall_result.get("items"), list) else []
    recall_buckets = recall_result.get("buckets") if isinstance(recall_result.get("buckets"), dict) else {}
    recall_count = len(recall_items) or sum(
        len(items) for items in recall_buckets.values() if isinstance(items, list)
    )
    smart_local_30 = payload.get("_smart_local_30_contract") is True
    advance_cap = profile_recall_qualification.SMART_LOCAL_TARGET if smart_local_30 else 15
    advance_default = profile_recall_qualification.SMART_LOCAL_TARGET if smart_local_30 else 15
    advance_limit = max(
        1,
        min(
            _int(payload.get("advance_limit") or payload.get("profile_advance_limit"), advance_default),
            advance_cap,
        ),
    )
    recall_total = min(recall_count, advance_limit)
    recall_contract = completion_contract(
        base_count=recall_count,
        total=recall_total,
        terminal_count=0,
        ready_count=0,
        active_tasks=recall_total,
        requested_tasks_terminal=False,
    )
    recall_session = search_sessions.attach_recall_result(
        int(session_id),
        {
            **recall_result,
            "_session_pipeline_running": True,
            "_session_progress": {
                "base": recall_count,
                "total": recall_total,
                "profile_ready": 0,
                "profile_failed": 0,
                "complete_ready": 0,
                "complete_partial": 0,
                **recall_contract,
            },
        },
    )
    base_count = recall_count
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
        strict_online_30 = payload.get("_smart_online_30_contract") is True
        # A7:逐轮收走 provider 自带的闸门漏斗切片(只读不改),用于会话诊断落库。
        provider_funnels: list[dict[str, Any]] = []
        online_contract: dict[str, Any] | None = None
        # 证据埋点:逐轮收「实际用了哪几条检索词 / 真烧了多少配额 / 到手候选的字段普查」,
        # 外加候选原件(带 discovery_query 溯源标)用于把合格新人回连到具体检索词。
        _term_rounds: list[dict[str, Any]] = []
        # 轮次预报表在两条腿上都要可读(legacy 腿不跑多轮 → 恒空,诊断按空态处理)。
        _round_forecasts: list[dict[str, Any]] = []
        _observed_candidates: list[dict[str, Any]] = []
        # YouTube 腿这次真打算发几条变体(确定性纯函数,零 IO)。配额预报按它算,
        # 治「每轮固定按 301 预报、实际 201」的 50% 高估。
        _yt_variants = profile_discovery_evidence.planned_youtube_variants(query)
        # 用户裁决 2026-08-25 第 2 条的在线腿:任意员工收藏过的人,在线也不再出现。
        # 身份键(平台+handle)只查一次,逐轮复用——在线候选没有 pool id,只能按身份比。
        # 摘除点在 provider 一返回、进资质判定之前,是在线腿最早的可排除位置。
        _favorite_identity_keys = recall_favorite_exclusion.favorited_identity_keys()
        _online_favorite_blocks: list[dict[str, Any]] = []
        discovery_kwargs = {
            "query_text": query,
            "platforms": resolved_platforms,
            "platform_hint": _text(payload.get("platform")),
            "market": normalized_market,
            "exclude_chinese": bool(payload.get("exclude_chinese", True)),
            "search_query_en": query,
            "product_focus": payload.get("product_focus"),
            "ideal_creator_types": _persona_kb.get("ideal_creator_types_json"),
            "verticals": _persona_kb.get("verticals_json"),
            "avoid_types": _persona_kb.get("avoid_types_json"),
            "target_persona": _text(payload.get("target_persona")),
        }
        if strict_online_30:
            # 车道 2:多轮 + 分页。第 1 轮与旧行为逐字一致(全平台、第一页);第 2 轮起
            # 只跑「真能翻页且真还有下一页」的腿(默认只有 YouTube:实测 <2s、零 Apify 花费),
            # 所以多轮不会把在线段耗时线性拉长 —— 107s 那条 IG 腿只跑第一轮。
            _per_platform_limit = max(1, min(_int(payload.get("new_discovery_per_platform_limit"), 50), 50))
            _per_platform_limits = payload.get("new_discovery_per_platform_limits")
            # 第 1 轮的预报只能按「operator 选了哪些平台」估;真解析出的腿在第 1 轮返回后写回
            # _round_legs(provider 的 platforms 才是权威)。空 = 未限定平台 = 三条腿全上。
            _plan_legs = [
                _text(item) for item in (
                    resolved_platforms if isinstance(resolved_platforms, (list, tuple, set))
                    else [resolved_platforms] if resolved_platforms else []
                ) if _text(item)
            ] or sorted(profile_online_qualification.ONLINE_SUPPORTED_PLATFORMS)
            _round_legs: list[str] = []
            _round_cursor: dict[str, Any] = {}
            _round_yield: dict[str, int] = {"last": 0}

            async def _fetch_online_batch(*, round_no: int, limit: int, cursor: Any) -> dict[str, Any]:
                legs = profile_discovery_rounds.platforms_for_round(round_no, _round_legs, cursor)
                if round_no > 1 and not legs:
                    # 兜底(正常路径上 _round_gate 已在发 provider 之前以
                    # no_paginated_leg_left 拦下,并保住 has_more 的真值)。走到这里说明
                    # 没装闸,那就诚实收工:一次抓取都别发。
                    return {"status": "empty", "new_creators": [], "provider_calls": False, "has_more": False}
                _forecast = profile_discovery_rounds.round_cost_forecast(
                    legs if round_no > 1 else (_round_legs or _plan_legs),
                    round_no=round_no,
                    per_platform_limit=_per_platform_limit,
                    per_platform_limits=_per_platform_limits,
                    youtube_query_variants=_yt_variants,
                )
                _round_forecasts.append(_forecast)
                logger.info("discovery_round_forecast %s", profile_discovery_rounds.forecast_line(_forecast))
                batch = await discover_new_creators(
                    **{**discovery_kwargs, **({"platforms": legs} if round_no > 1 else {})},
                    limit=max(1, min(limit, 150)),
                    per_platform_limit=_per_platform_limit,
                    # B3:operator 的每平台上限覆盖({平台: 上限});缺 → 全平台沿用上面的标量。
                    per_platform_limits=_per_platform_limits,
                    auto_enroll=False,
                    page_cursors=cursor,
                )
                # 证据埋点:先按 provider 原件记账(收藏排除之前),这样「某条词捞回几个人」
                # 是 provider 侧的真相;后面各道闸丢了多少另有 discovery_funnel 的账。
                _term_rounds.append(profile_discovery_evidence.observe_round(
                    round_no=round_no,
                    platform_results=batch.get("platform_results"),
                    candidates=batch.get("new_creators"),
                ))
                _observed_candidates.extend(
                    row for row in (batch.get("new_creators") or []) if isinstance(row, dict)
                )
                # 全局排除已被关注的人:就地摘掉,计数逐轮累加(缺口照实,不补别人充数)。
                _kept, _fav_block = recall_favorite_exclusion.exclude_favorited_online_candidates(
                    batch.get("new_creators") or [],
                    identity_keys=_favorite_identity_keys,
                )
                batch["new_creators"] = _kept
                _online_favorite_blocks.append(_fav_block)
                if not _round_legs:
                    _round_legs.extend(_text(item) for item in (batch.get("platforms") or []) if _text(item))
                _round_cursor.clear()
                _round_cursor.update(batch.get("next_cursor") or {})
                _round_yield["last"] = len(batch.get("new_creators") or [])
                if isinstance(batch.get("discovery_funnel"), dict):
                    provider_funnels.append(batch["discovery_funnel"])
                return batch

            _round_gate = profile_discovery_rounds.build_round_gate(
                legs_for_round=lambda round_no: profile_discovery_rounds.platforms_for_round(
                    round_no, _round_legs, _round_cursor,
                ),
                per_platform_limit=_per_platform_limit,
                per_platform_limits=_per_platform_limits,
                progress_reader=lambda: _round_yield["last"],
            )
            online_result = await profile_online_qualification.collect_strict_online_for_session(
                session_id=int(session_id),
                query_text=query,
                policy=profile_online_qualification.online_policy(
                    market=normalized_market,
                    platforms=resolved_platforms,
                    languages=payload.get("languages") or payload.get("content_languages"),
                    profile_types=payload.get("profile_types") or payload.get("kol_types"),
                    exclude_chinese=bool(payload.get("exclude_chinese", True)),
                ),
                fetch_batch=_fetch_online_batch,
                candidate_budget=150,
                # 多轮不再是死代码:上限 3 轮,但每一轮都要先过 _round_gate(时间/钱/
                # 真的还有下一页)。够 30 人、真翻完、或闸拦下,三者任一即停。
                max_provider_rounds=profile_online_qualification.ONLINE_MAX_PROVIDER_ROUNDS,
                round_gate=_round_gate,
                exhaustion_reason="bounded_provider_batch_exhausted",
            )
            # Base strict discovery is intentionally bounded to qualification and
            # materialization.  It must never fan out into crawler/LLM/contact jobs;
            # those require a separate, explicit post-approval action and budget.
            online_result["enrichment_queue"] = {
                "status": "not_enriched",
                "async": False,
                "queued": 0,
                "already_queued": 0,
                "failed": 0,
            }
            online_result["_session_pipeline_running"] = True
            # 在线腿摘了几个已被关注的人,逐轮加总后如实回执(0 也写,空态诚实)。
            online_result["favorite_exclusion"] = recall_favorite_exclusion.merge_diagnostics(
                *_online_favorite_blocks
            )
            search_sessions.attach_online_qualified_result(int(session_id), online_result)
            online_contract = {key: value for key, value in online_result.items() if key != "items"}
            new_discovery = {
                "status": online_result.get("status"),
                "query": query,
                "platforms": (
                    list(resolved_platforms)
                    if isinstance(resolved_platforms, (list, tuple, set))
                    else [resolved_platforms] if resolved_platforms else []
                ),
                "items": list(online_result.get("items") or []),
                "new_creators": list(online_result.get("items") or []),
                "existing_matches": [],
                "provider_calls": online_result.get("provider_calls_performed"),
                "online_qualification": online_contract,
                "favorite_exclusion": online_result.get("favorite_exclusion"),
            }
        else:
            new_discovery = await discover_new_creators(
                **discovery_kwargs,
                limit=max(1, min(_int(payload.get("new_discovery_limit"), 15), 50)),
                per_platform_limit=max(1, min(_int(payload.get("new_discovery_per_platform_limit"), 15), 50)),
                per_platform_limits=payload.get("new_discovery_per_platform_limits"),
            )
            # 证据埋点:两条在线路径的用词/配额记账口径必须一致(legacy 只有一轮)。
            _term_rounds.append(profile_discovery_evidence.observe_round(
                round_no=1,
                platform_results=new_discovery.get("platform_results"),
                candidates=new_discovery.get("new_creators"),
            ))
            _observed_candidates.extend(
                row for row in (new_discovery.get("new_creators") or []) if isinstance(row, dict)
            )
            # 旧发现路径同样受全局排除约束(两条在线路径口径必须一致)。
            _kept, _fav_block = recall_favorite_exclusion.exclude_favorited_online_candidates(
                new_discovery.get("new_creators") or [],
                identity_keys=_favorite_identity_keys,
            )
            new_discovery["new_creators"] = _kept
            new_discovery["favorite_exclusion"] = _fav_block
            # 收口路①-4:新人优先展示信号(新发现/低合作/成长期加权,饱和大号降位)。纯展示透出,
            # 绝不写 viltrox_fit_score / 不改 rule_v0;注解后再 attach(库内召回的 display_rank_score 已在 recall 侧产出)。
            new_discovery = _annotate_new_priority(new_discovery)
            if isinstance(new_discovery.get("discovery_funnel"), dict):
                provider_funnels.append(new_discovery["discovery_funnel"])
        discovery_count = len(new_discovery.get("existing_matches") or []) + len(new_discovery.get("new_creators") or [])
        if discovery_count <= 0:
            discovery_count = len(new_discovery.get("items") or [])
        base_count += discovery_count
        discovery_total = min(base_count, advance_limit)
        discovery_contract = completion_contract(
            base_count=base_count,
            total=discovery_total,
            terminal_count=0,
            ready_count=0,
            active_tasks=discovery_total,
            requested_tasks_terminal=False,
        )
        if not strict_online_30:
            search_sessions.attach_new_discovery_result(
                int(session_id),
                {
                    **new_discovery,
                    "_session_pipeline_running": True,
                    "_session_progress": {
                        "base": base_count,
                        "total": discovery_total,
                        "profile_ready": 0,
                        "profile_failed": 0,
                        "complete_ready": 0,
                        "complete_partial": 0,
                        **discovery_contract,
                    },
                },
            )
        # A7-a:漏斗落库。严格在线模式跳过 attach_new_discovery_result,这段坍缩此前在库里
        # 零痕迹、唯一证据是会滚掉的 INFO 日志。这里把每层进/出与丢弃原因分布并进会话诊断
        # ——纯记账,零过滤行为改动。必须写在 attach_* 之后:那些是整块覆写 result_summary。
        search_session_diagnostics.record_search_diagnostics(
            int(session_id),
            {
                search_session_diagnostics.DISCOVERY_FUNNEL_KEY: (
                    search_session_diagnostics.build_discovery_funnel(
                        lane="online_strict" if strict_online_30 else "legacy_discovery",
                        provider_funnels=provider_funnels,
                        online_contract=online_contract,
                        discovery_counts=new_discovery.get("counts"),
                        returned_count=discovery_count,
                    )
                ),
                # 车道 2:这次搜索每一轮各要了几次抓取、预估多少钱、闸怎么判的 —— 落库可查,
                # 因为在线严格契约的白名单不收这些键(会话摘要那边只留计数)。
                **(
                    {
                        "discovery_round_plan": profile_discovery_rounds.round_plan_record(
                            forecasts=_round_forecasts,
                            round_gate=(online_contract or {}).get("round_gate"),
                            provider_rounds=(online_contract or {}).get("provider_rounds"),
                            # 预报的对账面:provider metadata 报回来的真实消耗。
                            actual_quota_units=sum(
                                _int(row.get("quota_units_actual")) for row in _term_rounds
                            ),
                            actual_apify_runs=sum(
                                _int(row.get("apify_actor_runs")) for row in _term_rounds
                            ),
                        )
                    }
                    if strict_online_30 else {}
                ),
                # 证据埋点(2026-08-27):实际用词 / 产品锚及来源 / 判定时字段数 /
                # 每条词的产出与配额。四样落库后,「新东西有没有被生产路径调用」
                # 就是一条 SELECT 的事,不必再翻会滚掉的日志。
                profile_discovery_evidence.TERM_EVIDENCE_KEY: (
                    profile_discovery_evidence.build_term_evidence(
                        lane="online_strict" if strict_online_30 else "legacy_discovery",
                        anchor=profile_discovery_evidence.product_anchor_record(
                            payload=payload,
                            operator_anchor=operator_anchor,
                            effective_query=query,
                        ),
                        rounds=_term_rounds,
                        observed_candidates=_observed_candidates,
                        accepted_items=(new_discovery or {}).get("new_creators"),
                        # legacy 腿没有轮次预报 → None(不拿 0 冒充「预报过 0 单位」)。
                        quota_forecast_units=(
                            sum(_int(row.get("youtube_quota_units")) for row in _round_forecasts)
                            if _round_forecasts else None
                        ),
                    )
                ),
            },
        )

    advance_result = advance_search_session_items(
        session_id=int(session_id),
        smart_local_contract=smart_local_30,
        body={
            **payload,
            "execute": True,
            "limit": advance_limit,
            "_pipeline_running": True,
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
                provider_actor=provider_actor,
            )
        except Exception:
            content_fit = {"status": "error", "reason": "content_fit_enqueue_failed"}
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
        except Exception:
            video_backfill = {"status": "error", "reason": "video_backfill_enqueue_failed"}
    # 车道 1(用户裁令「每次搜索的时候定点抓取就行」):硬筛标出的「其他维度都合格、
    # 只差 country/language 未知」候选,在这里被消费——按需补一次轻量档案。总开关默认 OFF,
    # 关着时只算账单不花钱。写在结果装配之后 = 绝不阻塞首屏;补完的人下次搜索才可能出现。
    # 入队失败不阻断 pipeline。
    field_topup: dict[str, Any] | None = None
    if bool(payload.get("include_field_topup", True)):
        try:
            from app.domains.kol import profile_field_topup_enqueue

            field_topup = profile_field_topup_enqueue.enqueue_field_topup_for_candidates(
                candidates=(recall_result.get("diagnostics") or {}).get("field_topup_candidates"),
                session_id=int(session_id),
                staff=None,
                dry_run=bool(payload.get("field_topup_dry_run")),
            )
        except Exception:
            logger.warning("field_topup_enqueue_failed session_id=%s", session_id, exc_info=True)
            field_topup = {"status": "error", "reason": "field_topup_enqueue_failed"}
        # 诊断落库不另起一次写:搭本管线收尾那次 update_session_result_summary 的顺风车
        # (见下方 summary_patch 里的 "field_topup" 键),省掉每次搜索都要付的一个往返。
    pipeline_status = _profile_advance_pipeline_status(recall_result, new_discovery, advance_result)
    final_status = "partial" if changed_ids else pipeline_status
    profile_ready = 0
    profile_failed = 0
    for item in advance_result.get("items") or []:
        result = item.get("result") if isinstance(item, dict) and isinstance(item.get("result"), dict) else {}
        profile_status = _text(result.get("profile_status") or item.get("status")).lower()
        if profile_status in {"ready", "already_analyzed"}:
            profile_ready += 1
        elif "failed" in profile_status or profile_status == "error":
            profile_failed += 1
    profile_failed = max(
        profile_failed,
        _int((advance_result.get("counts") or {}).get("failed"))
        + _int((advance_result.get("counts") or {}).get("errors")),
    )
    selected_count = int(advance_result.get("selected") or 0)
    profile_completed = len(advance_result.get("items") or [])
    final_contract = completion_contract(
        base_count=base_count,
        total=selected_count,
        terminal_count=profile_completed,
        ready_count=profile_ready,
        profile_failed=profile_failed,
        active_tasks=max(0, selected_count - profile_completed),
        # Downstream video/comments/audience jobs are registered and rebuilt
        # after this profile loop.  A 15/15 profile batch must not masquerade
        # as terminal or as the strict full-analysis state in this gap.
        requested_tasks_terminal=False,
    )
    search_sessions.update_session_result_summary(
        int(session_id),
        status=final_status,
        summary_patch={
            "phase": "complete" if final_status == "ready" else "partial",
            "progress": {
                "base": base_count,
                "total": selected_count,
                "profile_ready": profile_ready,
                "profile_failed": profile_failed,
                "profile_completed": profile_completed,
                "profile_succeeded": max(0, profile_completed - profile_failed),
                "profile_remaining": max(0, selected_count - profile_completed),
                "complete_ready": _int((advance_result.get("counts") or {}).get("ready")),
                "complete_partial": _int((advance_result.get("counts") or {}).get("partial")),
                **final_contract,
            },
            **final_contract,
            # 车道 1 记账:本次标记了多少人待补 / 实际入队多少 / 因预算·冷却·上限跳过多少,
            # 以及「这一次要花多少次抓取」(planned_fetch_count)。总开关关着时也照落库,
            # 让人在武装之前先看见账单。applies_to_this_search 恒 False,不假装本次就补上了。
            **({"field_topup": field_topup} if field_topup else {}),
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
                "content_fit_ai_analysis": (content_fit or {}).get("ai_analysis") if content_fit else {
                    "state": "not_requested",
                    "reason": "not_requested",
                    "provider_calls_allowed": False,
                },
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
        # 车道 1 诊断:本次标记多少人待补 / 实际入队多少 / 被预算或冷却拦下多少。
        # applies_to_this_search 恒 False —— 补齐是后台的,不假装本次就补上了。
        "field_topup": field_topup,
        "recall": {
            "method": recall_result.get("method"),
            "returned_count": len(recall_result.get("items") or []),
            "diagnostics": recall_result.get("diagnostics"),
            "local_qualification": recall_result.get("local_qualification"),
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
