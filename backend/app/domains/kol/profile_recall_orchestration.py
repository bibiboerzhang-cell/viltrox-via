"""Low-complexity orchestration for :mod:`profile_recall`.

The public module remains the dependency seam.  Tests and callers historically
monkeypatch helpers on ``profile_recall``; accepting that module as ``deps``
keeps those seams intact while the request, retrieval, evidence, ranking and
response phases stay independently reviewable.
"""
from __future__ import annotations

from typing import Any

from app.domains.kol import profile_recall_backfill_ladder as _ladder
from app.domains.kol import profile_recall_backfill_wiring as _wiring
from app.domains.kol import search_relaxation as _relax
from app.domains.kol.profile_recall_orchestration_contract import RecallRequest
from app.domains.kol.profile_recall_observability import (
    elapsed_ms as _elapsed_ms,
    recall_stage_timing as _recall_stage_timing,
    register_embedding_call as _register_embedding_call,
)


def _prepare_context(request: RecallRequest, deps: Any) -> dict[str, Any]:
    resolved_text, query_meta = deps.resolve_query_text(
        query_text=request.query_text,
        product_sku=request.product_sku,
    )
    resolved_at = deps.perf_counter()
    raw_terms = request.required_product_evidence_terms
    if isinstance(raw_terms, dict):
        safe_product_evidence_terms = deps.product_evidence_terms(raw_terms)
    else:
        term_items = raw_terms if isinstance(raw_terms, (list, tuple, set)) else [raw_terms]
        safe_product_evidence_terms = deps.product_evidence_terms(
            {"marketing_name": " ".join(str(item or "") for item in term_items)}
        )

    profile_key = str(query_meta.get("query_profile") or "")
    persona_meta = deps.PRODUCT_LINE_PERSONAS.get(profile_key) or {}
    product_label = str(persona_meta.get("label") or persona_meta.get("persona") or "")
    persona_text = deps._persona_text_for_query(
        {**query_meta, "query_text": resolved_text},
        request.product_focus,
        request.target_persona,
    )
    evidence_query_text = (
        f"{resolved_text} {persona_text}".strip()
        if persona_text
        else resolved_text
    )
    return {
        "resolved_text": resolved_text,
        "query_meta": query_meta,
        "resolved_at": resolved_at,
        "safe_product_evidence_terms": safe_product_evidence_terms,
        "persona_meta": persona_meta,
        "product_label": product_label,
        "persona_text": persona_text,
        "evidence_query_text": evidence_query_text,
        "video_leaning": deps._is_video_leaning_product(
            query_meta,
            persona_text,
            request.product_focus,
        ),
    }


def _retrieve_candidates(
    request: RecallRequest,
    context: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    resolved_text = context["resolved_text"]
    pool_text_fallback_count = 0
    lexical_candidate_count = 0
    recall_degraded = ""
    # 检索段占本地腿 93-94%,但此前整段只有一个 retrieve_ms 总数,拆不出到底是
    # embedding、向量检索、词法还是兜底在吃时间——先装秤,再谈优化。
    # 键名带 retrieve_ 前缀且值恒为数字:两个持久化白名单(会话摘要的
    # stage_timing 只收 float、诊断块原样透传)都能原样接住,不用改别人的投影。
    breakdown: dict[str, float] = {}
    retrieve_started = deps.perf_counter()
    if request.provider_free:
        embedding_meta: dict[str, Any] = {"recall_mode": "provider_free_pool_text"}
        hits = deps._pool_text_fallback_hits(
            resolved_text,
            request.safe_candidate_limit,
            include_relevance_backfill=bool(request.allow_backfill),
            operator_query_text=request.operator_query_text,
            filters=request.retrieval_filters,
        )
        breakdown["retrieve_pool_text_ms"] = _elapsed_ms(retrieve_started, deps)
        pool_text_fallback_count = len(hits)
        lexical_candidate_count = sum(
            1 for hit in hits if hit.retrieval_method == deps.LEXICAL_METHOD
        )
    else:
        lexical_hits = _lexical_hits(request, resolved_text, deps)
        breakdown["retrieve_lexical_ms"] = _elapsed_ms(retrieve_started, deps)
        lexical_candidate_count = len(lexical_hits)
        embed_started = deps.perf_counter()
        try:
            query_vector, embedding_meta = deps._embed_query(resolved_text)
            breakdown["retrieve_embed_ms"] = _elapsed_ms(embed_started, deps)
            _register_embedding_call(
                query_text=resolved_text,
                embedding_meta=embedding_meta,
                latency_ms=breakdown["retrieve_embed_ms"],
                status="success",
                deps=deps,
            )
            vector_started = deps.perf_counter()
            vector_hits = deps._search_qdrant(
                query_vector,
                request.safe_candidate_limit,
            )
            breakdown["retrieve_vector_search_ms"] = _elapsed_ms(vector_started, deps)
            fuse_started = deps.perf_counter()
            hits = deps._hybrid_fuse_hits(
                vector_hits,
                lexical_hits,
                limit=request.safe_candidate_limit,
                factual_anchor_required=deps.query_requires_factual_anchor(
                    resolved_text,
                    request.operator_query_text,
                ),
            )
            breakdown["retrieve_fuse_ms"] = _elapsed_ms(fuse_started, deps)
        except Exception as exc:  # noqa: BLE001 - public recall degrades honestly.
            recall_degraded = deps._support.classify_recall_failure(exc)
            # 只有 embedding 这一步自己炸了才补一行失败调用记录;向量检索/融合
            # 炸掉时 embedding 已经成功登记过,不许重复登。
            if "retrieve_embed_ms" not in breakdown:
                breakdown["retrieve_embed_ms"] = _elapsed_ms(embed_started, deps)
                _register_embedding_call(
                    query_text=resolved_text,
                    embedding_meta={},
                    latency_ms=breakdown["retrieve_embed_ms"],
                    status="provider_exception",
                    failure_reason=recall_degraded,
                    deps=deps,
                )
            deps.logger.warning(
                "recall_degraded reason=%s",
                recall_degraded,
                exc_info=True,
            )
            embedding_meta = {}
            hits = lexical_hits

    floor_started = deps.perf_counter()
    hits, fallback_count = _ensure_recall_floor(
        request,
        resolved_text,
        hits,
        deps,
    )
    breakdown["retrieve_recall_floor_ms"] = _elapsed_ms(floor_started, deps)
    if fallback_count:
        pool_text_fallback_count = fallback_count
    hits = hits[: request.safe_candidate_limit]
    retrieved_hit_count = len(hits)
    exclusion_started = deps.perf_counter()
    considered_hits = list(hits)
    survivors, favorite_exclusion = deps._favorite_exclusion.exclude_favorited_hits(hits)
    # 默认**不隐藏**:同事关注过的人照旧参与召回与排序,卡面标注「已被同事关注」——找新人
    # 这一步的问题是「有没有这个人」,谁去联系是外联那一步的事。严口径 / 操作员点名隐藏时
    # 逐字保持旧行为:摘出来交给回填梯第一级兜底。
    favorite_policy = _relax.apply_favorite_policy(
        considered_hits, survivors, favorite_exclusion,
        hide=_relax.hide_team_favorites(request.local_qualification_policy),
    )
    hits = favorite_policy["hits"]
    breakdown["retrieve_favorite_exclusion_ms"] = _elapsed_ms(exclusion_started, deps)
    return {
        "hits": hits,
        "favorited_hits": favorite_policy["favorited_hits"],
        "favorited_ids": favorite_policy["favorited_ids"],
        "retrieved_hit_count": retrieved_hit_count,
        "favorite_exclusion": favorite_policy["exclusion"],
        "retrieved_at": deps.perf_counter(),
        "pool_text_fallback_count": pool_text_fallback_count,
        "lexical_candidate_count": lexical_candidate_count,
        "recall_degraded": recall_degraded,
        "embedding_meta": embedding_meta,
        "stage_timing": breakdown,
    }


def _lexical_hits(request: RecallRequest, resolved_text: str, deps: Any) -> list[Any]:
    try:
        return deps._pool_text_fallback_hits(
            resolved_text,
            request.safe_candidate_limit,
            include_relevance_backfill=False,
            operator_query_text=request.operator_query_text,
            filters=request.retrieval_filters,
        )
    except Exception:
        deps.logger.warning("profile_recall lexical retrieval unavailable", exc_info=True)
        return []


def _ensure_recall_floor(
    request: RecallRequest,
    resolved_text: str,
    hits: list[Any],
    deps: Any,
) -> tuple[list[Any], int]:
    if not hits:
        fallback = deps._pool_text_fallback_hits(
            resolved_text,
            request.safe_candidate_limit,
            include_relevance_backfill=bool(request.allow_backfill),
            operator_query_text=request.operator_query_text,
            filters=request.retrieval_filters,
        )
        return fallback, len(fallback)
    if not request.allow_backfill or len(hits) >= request.safe_candidate_limit:
        return hits, 0

    known_ids = {hit.kol_pool_id for hit in hits}
    try:
        local_backfill = deps._pool_text_fallback_hits(
            "",
            request.safe_candidate_limit,
            include_relevance_backfill=True,
            operator_query_text=request.operator_query_text,
            filters=request.retrieval_filters,
        )
    except Exception:
        deps.logger.warning("profile_recall local backfill unavailable", exc_info=True)
        local_backfill = []
    hits.extend(hit for hit in local_backfill if hit.kol_pool_id not in known_ids)
    return hits[: request.safe_candidate_limit], 0


def _hydrate_candidates(
    request: RecallRequest,
    retrieval: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    ordered_hits: list[Any] = []
    seen: set[int] = set()
    duplicate_count = 0
    for hit in retrieval["hits"]:
        if request.dedupe and hit.kol_pool_id in seen:
            duplicate_count += 1
            continue
        seen.add(hit.kol_pool_id)
        ordered_hits.append(hit)

    ids = [hit.kol_pool_id for hit in ordered_hits]
    rows_by_id = deps._entry_rows(ids)
    try:
        evidence_by_id = deps._evidence_summaries(ids)
    except Exception:
        if not request.smart_local_enabled:
            raise
        deps.logger.warning(
            "smart_local rich evidence projection unavailable",
            exc_info=True,
        )
        evidence_by_id = deps._smart_local_evidence_summaries(ids)
    fallback_rows = deps._pool_rows_fallback(
        [item_id for item_id in ids if item_id not in rows_by_id]
    )
    qualification_rows = {**fallback_rows, **rows_by_id}
    if request.smart_local_enabled:
        qualification_rows, evidence_by_id = deps._smart_local_qualification_context(
            ids,
            rows_by_id=qualification_rows,
            evidence_by_id=evidence_by_id,
        )
    return {
        "ordered_hits": ordered_hits,
        "duplicate_count": duplicate_count,
        "rows_by_id": rows_by_id,
        "evidence_by_id": evidence_by_id,
        "fallback_rows": fallback_rows,
        "qualification_rows": qualification_rows,
        # 「同事已关注」的人在召回段就认出来了,带到投影段盖章(隐藏模式下恒为空集)。
        "favorited_ids": set(retrieval.get("favorited_ids") or ()),
        "evidence_loaded_at": deps.perf_counter(),
    }


def _project_candidates(
    request: RecallRequest,
    context: dict[str, Any],
    hydration: dict[str, Any],
    deps: Any,
    reserve: Any = None,
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "creator": [],
        "reviewer": [],
        "unknown": [],
    }
    ledger = deps.RecallStageLedger()
    # 储备账本只在 smart-local 车道收人(其余车道 enabled=False,是空操作):
    # 通过集合与记账前逐字节一致,回填只发生在选人之后。
    if reserve is None:
        reserve = _ladder.BackfillReserve(enabled=bool(request.smart_local_enabled))
    for hit in hydration["ordered_hits"]:
        item = _project_candidate(
            hit,
            request=request,
            context=context,
            hydration=hydration,
            ledger=ledger,
            deps=deps,
            reserve=reserve,
        )
        if item is not None:
            buckets[item["bucket"]].append(item)
    return {"buckets": buckets, "ledger": ledger, "reserve": reserve}


def _project_candidate(
    hit: Any,
    *,
    request: RecallRequest,
    context: dict[str, Any],
    hydration: dict[str, Any],
    ledger: Any,
    deps: Any,
    reserve: Any,
) -> dict[str, Any] | None:
    row = hydration["rows_by_id"].get(hit.kol_pool_id)
    if not row:
        row = hydration["fallback_rows"].get(hit.kol_pool_id)
        if not row:
            ledger.missing_type += 1
            return None
        ledger.fallback_used += 1
    if request.exclude_chinese and deps._country_in_excluded_region(row.get("country")):
        ledger.excluded_region += 1
        return None
    if not request.smart_local_enabled:
        reach_state = deps._reach_display_state(row)
        if reach_state == "low_reach":
            ledger.low_reach += 1
            deps.logger.debug(
                "recall_reach_floor_filtered handle=%r kol_pool_id=%s reason=%s",
                row.get("handle"),
                hit.kol_pool_id,
                deps._reach_floor_reason(row) or "low_reach_flag",
            )
            return None
        if reach_state == "unknown":
            ledger.unknown_reach += 1
            deps.logger.debug(
                "recall_reach_unknown_hidden handle=%r kol_pool_id=%s",
                row.get("handle"),
                hit.kol_pool_id,
            )
            return None

    evidence = hydration["evidence_by_id"].get(hit.kol_pool_id, {})
    vertical_reading = deps.classify_verticals(row, evidence)
    verdict = deps._candidate_filter_verdict(
        row,
        evidence,
        request.normalized_filters,
        vertical_reading=vertical_reading,
    )
    passes_filters, rejected_fields, unknown_fields = verdict
    ledger.note_hard_filter(rejected_fields, unknown_fields, passed=passes_filters)
    entry = _ladder.ReserveEntry(
        hit=hit,
        row=row,
        evidence=evidence,
        vertical_reading=vertical_reading,
        unknown_fields=list(unknown_fields),
        rejected_fields=list(rejected_fields),
    )
    if not passes_filters:
        ledger.note_topup_candidates(
            getattr(verdict, "unknown_field_candidates", ())
        )
        reserve.note_hard_filter(entry)
        return None

    field_evidence = _wiring.match_evidence_for(entry, request=request, context=context, deps=deps)
    if not request.allow_backfill and not field_evidence:
        ledger.no_match_evidence += 1
        reserve.note_no_evidence(entry)
        return None
    item = _wiring.materialize_item(
        entry, field_evidence, request=request, context=context, deps=deps
    )
    return _relax.annotate_if_team_favorite(
        item, hit.kol_pool_id, hydration.get("favorited_ids")
    )


def _rank_candidates(
    request: RecallRequest,
    context: dict[str, Any],
    projection: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    buckets = projection["buckets"]
    all_ranked_candidates = [item for values in buckets.values() for item in values]
    robust_ranking_diagnostics = deps.apply_robust_ranking(all_ranked_candidates)
    deps._assign_business_buckets(
        all_ranked_candidates,
        request.normalized_bucket_policy,
    )
    for bucket_items in buckets.values():
        bucket_items.sort(key=deps.ranking_key, reverse=True)
    rerank_note = deps._display_boost.apply_display_boost_and_rerank(
        buckets,
        provider_free=bool(request.provider_free),
        resolved_text=context["resolved_text"],
        persona_text=context["persona_text"],
        product_label=context["product_label"],
        adoption_profile=deps._adoption_profile,
        adoption_boost_for=deps._adoption_boost_for,
        llm_rerank_buckets=deps._llm_rerank_buckets,
        ranking_key=deps.ranking_key,
        to_float=deps._float,
    )
    return {
        "all_ranked_candidates": all_ranked_candidates,
        "robust_ranking_diagnostics": robust_ranking_diagnostics,
        "rerank_note": rerank_note,
        "gated_at": deps.perf_counter(),
    }


def _select_candidates(
    request: RecallRequest,
    hydration: dict[str, Any],
    projection: dict[str, Any],
    ranking: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    buckets = projection["buckets"]
    local_qualification: dict[str, Any] | None = None
    if request.smart_local_enabled:
        items, selected_buckets, local_qualification = deps.qualify_local_candidates(
            buckets={"creator": buckets["creator"], "reviewer": buckets["reviewer"]},
            rows_by_id=hydration["qualification_rows"],
            evidence_by_id=hydration["evidence_by_id"],
            policy=dict(request.local_qualification_policy or {}),
            creator_quota=request.safe_creator_quota,
            reviewer_quota=request.safe_reviewer_quota,
        )
        selected_creator = selected_buckets["creator"]
        selected_reviewer = selected_buckets["reviewer"]
        selected_unknown: list[dict[str, Any]] = []
        lane_selection = {
            "selection_method": "smart_local_qualification_before_limit",
            "selected_count": len(items),
            "selected_by_lane": {
                lane: sum(
                    1 for item in items if item.get("candidate_bucket") == lane
                )
                for lane in ("core_vertical", "expansion", "exploration")
            },
        }
    else:
        items, lane_selection = deps.select_with_business_lane_quotas(
            ranking["all_ranked_candidates"],
            limit=request.safe_limit,
            bucket_policy=request.normalized_bucket_policy,
            creator_quota=request.safe_creator_quota,
            reviewer_quota=request.safe_reviewer_quota,
            allow_backfill=bool(request.allow_backfill),
        )
        selected_creator = [item for item in items if item.get("bucket") == "creator"]
        selected_reviewer = [item for item in items if item.get("bucket") == "reviewer"]
        selected_unknown = [item for item in items if item.get("bucket") == "unknown"]
    business_buckets = {
        lane: [item for item in items if item.get("candidate_bucket") == lane]
        for lane in ("core_vertical", "expansion", "exploration")
    }
    return {
        "items": items,
        "selected_creator": selected_creator,
        "selected_reviewer": selected_reviewer,
        "selected_unknown": selected_unknown,
        "business_buckets": business_buckets,
        "lane_selection": lane_selection,
        "local_qualification": local_qualification,
    }


def _selection_metrics(
    request: RecallRequest,
    ranking: dict[str, Any],
    selection: dict[str, Any],
) -> dict[str, Any]:
    items = selection["items"]
    candidates = ranking["all_ranked_candidates"]
    tier_counts = {
        tier: sum(1 for item in items if item.get("match_tier") == tier)
        for tier in ("strict", "relaxed", "backfill")
    }
    available_counts = {
        tier: sum(1 for item in candidates if item.get("match_tier") == tier)
        for tier in ("strict", "relaxed", "backfill")
    }
    creator_take = min(request.safe_creator_quota, request.safe_limit)
    reviewer_take = min(
        request.safe_reviewer_quota,
        max(0, request.safe_limit - creator_take),
    )
    quota_refill = (
        max(0, len(selection["selected_creator"]) - creator_take)
        + max(0, len(selection["selected_reviewer"]) - reviewer_take)
        + len(selection["selected_unknown"])
    )
    shortfall = max(0, request.safe_limit - len(items))
    if items:
        empty_reason = ""
        shortfall_reason = (
            "" if len(items) >= request.safe_limit else "evidence_candidates_exhausted"
        )
    elif candidates:
        empty_reason = "quota_excluded_evidence_candidates"
        shortfall_reason = empty_reason
    else:
        empty_reason = "no_evidence_match" if not request.allow_backfill else ""
        shortfall_reason = empty_reason
    return {
        "tier_counts": tier_counts,
        "available_counts": available_counts,
        "profile_quota_refill_count": quota_refill,
        "shortfall": shortfall,
        "empty_reason": empty_reason,
        "shortfall_reason": shortfall_reason,
    }


def _build_diagnostics(
    request: RecallRequest,
    retrieval: dict[str, Any],
    hydration: dict[str, Any],
    projection: dict[str, Any],
    ranking: dict[str, Any],
    selection: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    buckets = projection["buckets"]
    selected_creator = selection["selected_creator"]
    selected_reviewer = selection["selected_reviewer"]
    selected_unknown = selection["selected_unknown"]
    items = selection["items"]
    favorites = retrieval["favorite_exclusion"]
    diagnostics = {
        "candidate_count": len(retrieval["hits"]),
        "retrieved_candidate_count": retrieval["retrieved_hit_count"],
        "favorite_excluded_count": int(favorites.get("excluded_count") or 0),
        # 松绑口径下他们没被藏起来,只是带了标注——两个数是两件事,不能互相冒充。
        "favorite_annotated_count": int(favorites.get("annotated_count") or 0),
        "favorite_exclusion": favorites,
        # 松绑回执:开关叫什么、默认是什么、这次是什么——一眼可查、可回退。
        "search_relaxation": _relax.relaxation_receipt(request.local_qualification_policy),
        "deduped_candidate_count": len(hydration["ordered_hits"]),
        "duplicate_count": hydration["duplicate_count"],
        "typed_candidate_count": len(buckets["creator"]) + len(buckets["reviewer"]),
        "unknown_type_candidate_count": len(buckets["unknown"]),
        **projection["ledger"].as_diagnostics(
            deduped_candidate_count=len(hydration["ordered_hits"])
        ),
        "evidence_gate_enabled": not bool(request.allow_backfill),
        "empty_reason": metrics["empty_reason"],
        "shortfall_reason": metrics["shortfall_reason"],
        "applied_filters": request.normalized_filters,
        "unsupported_filters": request.unsupported_filters,
        "fallback_pool_rows": projection["ledger"].fallback_used,
        "pool_text_fallback_count": retrieval["pool_text_fallback_count"],
        "lexical_candidate_count": retrieval["lexical_candidate_count"],
        "display_rerank": ranking["rerank_note"],
        "creator_candidate_count": len(buckets["creator"]),
        "reviewer_candidate_count": len(buckets["reviewer"]),
        "creator_returned": len(selected_creator),
        "reviewer_returned": len(selected_reviewer),
        "unknown_type_returned": len(selected_unknown),
        "returned_count": len(items),
        "requested_count": request.safe_limit,
        "strict_available_count": metrics["available_counts"]["strict"],
        "relaxed_available_count": metrics["available_counts"]["relaxed"],
        "strict_count": metrics["tier_counts"]["strict"],
        "relaxed_count": metrics["tier_counts"]["relaxed"],
        "backfill_available_count": metrics["available_counts"]["backfill"],
        "backfill_count": metrics["tier_counts"]["backfill"],
        "profile_quota_refill_count": metrics["profile_quota_refill_count"],
        "final_count": len(items),
        "shortfall": metrics["shortfall"],
        "result_contract_satisfied": metrics["shortfall"] == 0,
        "result_contract_note": "仅表示数量达到且硬筛选未放宽，不代表检索精准度。",
        "backfill_policy": (
            "query_relevance_only_hard_filters_never_relaxed"
            if request.allow_backfill
            else "disabled_evidence_gate"
        ),
        "bucket_policy": request.normalized_bucket_policy,
        "bucket_policy_adjusted": request.bucket_policy_adjusted,
        "business_bucket_counts": {
            key: len(value) for key, value in selection["business_buckets"].items()
        },
        "lane_selection": selection["lane_selection"],
        "recall_degraded": retrieval["recall_degraded"],
        "provider_free_initial": bool(request.provider_free),
        **retrieval["embedding_meta"],
    }
    if selection.get("backfill_ladder") is not None:
        backfill_items = selection.get("backfill_items") or []
        diagnostics.update(
            {
                "precise_count": len(items),
                "backfill_count": len(backfill_items),
                "backfill_ladder": selection["backfill_ladder"],
                "result_explanation": selection["result_explanation"],
            }
        )
    return diagnostics


def _build_response(
    request: RecallRequest,
    context: dict[str, Any],
    retrieval: dict[str, Any],
    hydration: dict[str, Any],
    projection: dict[str, Any],
    ranking: dict[str, Any],
    selection: dict[str, Any],
    metrics: dict[str, Any],
    deps: Any,
) -> dict[str, Any]:
    items = selection["items"]
    distribution_rows = {
        **hydration["fallback_rows"],
        **hydration["rows_by_id"],
    }
    return {
        "method": deps.METHOD,
        "match_status": "matched" if items else "empty",
        "candidate_set_distribution": deps.candidate_set_distribution(
            items,
            distribution_rows,
            hydration["evidence_by_id"],
        ),
        "query": {
            **context["query_meta"],
            "query_text": context["resolved_text"],
            "collection_name": deps.COLLECTION_NAME,
            "candidate_limit": request.safe_candidate_limit,
            "requested_candidate_limit": request.requested_candidate_limit,
            "server_candidate_limit_override": request.safe_server_candidate_limit_override,
            "server_candidate_limit_override_applied": request.server_candidate_limit_override_applied,
            "limit": request.safe_limit,
            "product_label": context["product_label"],
            "product_persona": str(context["persona_meta"].get("persona") or ""),
            "video_leaning_product": bool(context["video_leaning"]),
            "search_strategy": str(request.search_strategy or "balanced").strip().lower(),
            "allow_backfill": bool(request.allow_backfill),
            "required_product_evidence_terms": context["safe_product_evidence_terms"],
        },
        "ratio": {
            "creator_quota": request.safe_creator_quota,
            "reviewer_quota": request.safe_reviewer_quota,
            "policy": request.ratio_policy,
            "mixed_policy": request.mixed_policy,
            "dedupe": bool(request.dedupe),
        },
        "filters": {
            "applied": request.normalized_filters,
            "unsupported": request.unsupported_filters,
            "hard_filters_relaxed": False,
        },
        "bucket_policy": request.normalized_bucket_policy,
        "ranking": {
            "type_boost_enabled": bool(request.type_boost_enabled),
            "vector_weight": request.safe_vector_weight,
            "type_weight": request.safe_type_weight,
            "score_formula": "observed_signals_weighted_mean_missing_omitted",
            "robust_rank_method": ranking["robust_ranking_diagnostics"].get("version"),
            "claim_status": "descriptive_only",
            "note": "robust_rank_score 是检索排序分，不是业务 precision 或预测准确率。",
            **ranking["robust_ranking_diagnostics"],
        },
        "evaluation_status": deps.build_runtime_evaluation_status(
            algorithm_version=deps.ROBUST_RANK_VERSION,
        ),
        "items": items,
        # 回填区:精准命中不够时按梯放宽补上的人,每个都带 backfill_tier 标记,不混进 items。
        "backfill_items": list(selection.get("backfill_items") or []),
        "buckets": {
            "creator": selection["selected_creator"],
            "reviewer": selection["selected_reviewer"],
            "unknown": selection["selected_unknown"],
        },
        "business_buckets": selection["business_buckets"],
        "diagnostics": _build_diagnostics(
            request,
            retrieval,
            hydration,
            projection,
            ranking,
            selection,
            metrics,
        ),
    }


def _finalize_smart_local(
    response: dict[str, Any],
    request: RecallRequest,
    selection: dict[str, Any],
    timing: dict[str, float],
    deps: Any,
) -> dict[str, Any]:
    local_qualification = selection["local_qualification"]
    if local_qualification is None:
        deps._favorite_exclusion.annotate_shortfall(response["diagnostics"])
        return response
    local_qualification["stage_timing"].update(timing)
    local_qualification["total_ms"] = timing["total_ms"]
    response["local_qualification"] = local_qualification
    items = selection["items"]
    response["match_status"] = "matched" if items else "empty"
    response["diagnostics"].update(
        {
            "returned_count": len(items),
            "creator_returned": len(selection["selected_creator"]),
            "reviewer_returned": len(selection["selected_reviewer"]),
            "shortfall": local_qualification["shortfall"],
            "shortfall_reason": local_qualification["shortfall_reason"],
            "empty_reason": "" if items else "no_qualified_candidates",
            "result_contract_satisfied": local_qualification["shortfall"] == 0,
            "smart_local_qualification": True,
        }
    )
    response = deps.project_smart_local_result(response)
    # 回填区走同一道隐私投影(同一个函数、同一份合同),绝不带 raw/联系方式出门。
    response["backfill_items"] = deps.project_smart_local_result(
        {
            "items": list(response.get("backfill_items") or []),
            "buckets": {},
            "local_qualification": response["local_qualification"],
        }
    ).get("items") or []
    response["business_buckets"] = {
        lane: [
            item
            for item in response.get("items") or []
            if item.get("candidate_bucket") == lane
        ]
        for lane in ("core_vertical", "expansion", "exploration")
    }
    deps._favorite_exclusion.annotate_shortfall(response["diagnostics"])
    _wiring.annotate_favorite_note_after_backfill(response["diagnostics"])
    return response


def run_recall_pipeline(request: RecallRequest, *, deps: Any) -> dict[str, Any]:
    """Execute the recall pipeline without weakening any business guard."""
    context = _prepare_context(request, deps)
    retrieval = _retrieve_candidates(request, context, deps)
    hydration = _hydrate_candidates(request, retrieval, deps)
    projection = _project_candidates(request, context, hydration, deps)
    ranking = _rank_candidates(request, context, projection, deps)
    selection = _select_candidates(
        request,
        hydration,
        projection,
        ranking,
        deps,
    )
    if request.smart_local_enabled:
        _wiring.apply_backfill_ladder(
            request, context, retrieval, hydration, projection, selection, deps,
            hydrate=_hydrate_candidates,
            project=_project_candidates,
        )
    metrics = _selection_metrics(request, ranking, selection)
    response = _build_response(
        request,
        context,
        retrieval,
        hydration,
        projection,
        ranking,
        selection,
        metrics,
        deps,
    )
    # 秤放在 smart-local 收尾**之前**算,并且无条件挂进诊断块:非 smart 车道
    # 没有 local_qualification 这个容器,诊断块是它唯一的落点,而诊断块是原样
    # 透传进会话摘要的,所以「这次搜索每段花了多久」从此每条车道都查得到。
    timing = _recall_stage_timing(request, context, retrieval, hydration, ranking, deps)
    response["diagnostics"]["stage_timing"] = timing
    return _finalize_smart_local(response, request, selection, timing, deps)
