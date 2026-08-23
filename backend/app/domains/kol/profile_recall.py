"""KOL profile recall orchestration with compatibility re-exports."""
from __future__ import annotations

from decimal import Decimal
import os
import re
from time import perf_counter
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.costs.budget_guard import check_budget, record_cost
from app.domains.kol.discovery_filters import (
    LOW_REACH_FLAG_LIKE_PATTERN,
    _reach_display_state,
    _reach_floor_reason,
)
from app.domains.kol.pool_common import _table_columns
from app.domains.kol.profile_recall_contract import (
    COLLECTION_NAME,
    DEFAULT_RESULT_LIMIT,
    EMBEDDING_MODEL,
    LENS_MENTION_RE,
    MAX_CANDIDATE_LIMIT,
    METHOD,
    OPENAI_EMBEDDING_PRICE_PER_1M,
    PROFILE_REASON_KEYWORDS,
    PROJECT_ROOT,
    QDRANT_LOCAL_PATH,
    RecallHit,
    SEARCH_STRATEGY_BUCKET_POLICIES,
    SUPPORTED_RECALL_FILTERS,
    VECTOR_SIZE,
    _clean_text,
)
from app.domains.kol.profile_recall_product_queries import (
    PRODUCT_LINE_PERSONAS,
    PRODUCT_QUERY_TEXTS,
    PRODUCT_SKU_ALIASES,
    _normalise_sku,
    resolve_query_text,
)
from app.domains.kol.profile_recall_match_evidence import (
    build_match_evidence,
    candidate_facets,
    candidate_set_distribution,
    product_evidence_terms,
    why_fit_from_match_evidence,
)
from app.domains.kol.profile_recall_qualification import (
    SMART_LOCAL_CANDIDATE_LIMIT,
    SMART_LOCAL_TARGET,
    project_smart_local_result,
    qualify_local_candidates,
)
from app.domains.kol.profile_recall_precision import (
    HYBRID_METHOD,
    LEXICAL_METHOD,
    ROBUST_RANK_VERSION,
    apply_robust_ranking,
    explicit_platforms_from_query,
    lexical_recall_candidates,
    missingness_aware_weighted_score,
    query_requires_factual_anchor,
    ranking_key,
    select_with_business_lane_quotas,
)
from app.domains.kol.search_relevance_eval import build_runtime_evaluation_status
from app.domains.kol import profile_recall_support as _support


logger = get_logger(__name__)


def _cost_for_tokens(tokens: int) -> Decimal:
    return (Decimal(max(0, int(tokens))) * OPENAI_EMBEDDING_PRICE_PER_1M / Decimal(1_000_000)).quantize(Decimal("0.00000001"))


def _qdrant_client():
    try:
        from qdrant_client import QdrantClient
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"qdrant_client_unavailable: {exc}") from exc

    url = os.getenv("QDRANT_URL", "").strip()
    api_key = os.getenv("QDRANT_API_KEY", "").strip() or None
    if url:
        return QdrantClient(url=url, api_key=api_key)
    if not QDRANT_LOCAL_PATH.exists():
        raise RuntimeError(f"qdrant_local_path_missing:{QDRANT_LOCAL_PATH}")
    return QdrantClient(path=str(QDRANT_LOCAL_PATH))


def _openai_client(*, proxy_override: str | None = None, direct: bool = False, timeout: float = 30.0):
    return _support.openai_client(
        proxy_override=proxy_override,
        direct=direct,
        timeout=timeout,
    )


# ── embedding 403 failover(2026-07-12 根因修复)──────────────────────────────
# 根因分层(worker.log 2026-07-11 15:09 取证):CONNECT gate.decodo.com:10001 → TLS 握手成功
# → api.openai.com 返 HTTP 403(Server: cloudflare,CF-RAY *-AMS,种 __cf_bm bot-management
# cookie)= Cloudflare 按【代理出口 IP 信誉】拉黑,非请求头/UA 指纹问题(同一套头次日实测
# 200;换 UA 实测无差别)。Decodo 1xxxx 端口族是 sticky 会话端口——出口 IP 被钉住,拉黑即
# 连片 403。同一凭据换端口=换出口 IP(实测 10001/10002/10003/7000/10999 五端口五个不同出
# 口),因此 failover 策略=先原配置端口,403/连接类失败再换端口重试;供应商与向量空间零变
# 更(仍 text-embedding-3-small/1536 维),存量 Qdrant 索引零风险。
EMBED_PROXY_ROTATE_PORTS_ENV = "VKPI_EMBED_PROXY_ROTATE_PORTS"
EMBED_PROXY_ROTATE_PORTS_DEFAULT = "10002,7000"
# 直连末位兜底默认 OFF(本网络 LLM 走代理是既定口径;2026-07-12 实测直连恰好可达,但不作默认
# 假设)。置 1 开启:全部代理出口都被拉黑时最后试一次直连(trust_env=False,8s 超时封顶)。
EMBED_DIRECT_FALLBACK_ENV = "VKPI_EMBED_DIRECT_FALLBACK"


def _proxy_rotation_candidates(proxy: str) -> list[str]:
    """同凭据换端口生成备选代理 URL(换端口=换 sticky 出口 IP)。

    仅当配置代理以 :<port> 结尾才有备选;端口表读 env VKPI_EMBED_PROXY_ROTATE_PORTS
    (默认 10002,7000),跳过与当前配置相同的端口。解析失败返回空表(不轮换,行为同旧)。
    """
    return _support.proxy_rotation_candidates(
        proxy,
        ports_env=EMBED_PROXY_ROTATE_PORTS_ENV,
        default_ports=EMBED_PROXY_ROTATE_PORTS_DEFAULT,
    )


def _should_failover(exc: Exception) -> bool:
    return _support.should_failover(exc)

def _embed_transport_plan() -> list[dict[str, Any]]:
    """embedding 出网尝试序列:①原配置(行为不变)②换端口出口 ③直连(默认 OFF)。"""
    plan: list[dict[str, Any]] = [{"transport": "proxy_primary", "proxy_override": None, "direct": False, "timeout": 30.0}]
    proxy = (os.getenv("OPENAI_PROXY") or os.getenv("YTDLP_PROXY") or "").strip()
    for candidate in _proxy_rotation_candidates(proxy)[:2]:
        port = candidate.rsplit(":", 1)[-1]
        plan.append({"transport": f"proxy_rotated:{port}", "proxy_override": candidate, "direct": False, "timeout": 15.0})
    if os.getenv(EMBED_DIRECT_FALLBACK_ENV, "0").strip().lower() in {"1", "true", "yes"}:
        plan.append({"transport": "direct", "proxy_override": None, "direct": True, "timeout": 8.0})
    return plan


def _create_embedding_with_failover(query_text: str, *, client_factory: Any = None) -> tuple[Any, str]:
    """按 transport plan 逐个尝试 embeddings.create;返回 (resp, 实际用的 transport 标签)。

    仅 403/连接类失败才换下一个出口(_should_failover);其余错误立刻上抛。
    全部出口失败抛最后一个异常 → 上层 recall_degraded 降级路径原样保持诚实。
    client_factory 仅供测试打桩;生产恒走 _openai_client。
    """
    from app.domains.kol.contact_system import sanitize_contact_values_for_external_processing

    query_text = str(sanitize_contact_values_for_external_processing(query_text) or "")
    factory = client_factory or (lambda spec: _openai_client(
        proxy_override=spec["proxy_override"], direct=spec["direct"], timeout=spec["timeout"]
    ))
    plan = _embed_transport_plan()
    last_exc: Exception | None = None
    for i, spec in enumerate(plan):
        try:
            client = factory(spec)
            resp = client.embeddings.create(model=EMBEDDING_MODEL, input=[query_text])
            return resp, str(spec["transport"])
        except Exception as exc:  # noqa: BLE001 — failover 判据集中在 _should_failover
            if i + 1 >= len(plan) or not _should_failover(exc):
                raise
            last_exc = exc
            logger.warning(
                "embed_transport_failover from=%s to=%s reason=%s",
                spec["transport"], plan[i + 1]["transport"], str(exc)[:160],
            )
    raise last_exc if last_exc else RuntimeError("embed_transport_plan_empty")  # pragma: no cover


def _embed_query(query_text: str) -> tuple[list[float], dict[str, Any]]:
    # 护栏③ enforce(诊断 C-3③):embedding 调用前硬闸 + 调用后记账(此前裸奔零护栏零记账)。
    if not check_budget("provider:openai", 0.0, require_configured=True):
        raise RuntimeError("embedding_budget_exceeded")
    resp, transport = _create_embedding_with_failover(query_text)
    data = list(resp.data or [])
    if not data:
        raise RuntimeError("empty_embedding_response")
    vector = [float(value) for value in data[0].embedding]
    if len(vector) != VECTOR_SIZE:
        raise RuntimeError(f"embedding_vector_size_mismatch:{len(vector)}")
    usage = getattr(resp, "usage", None)
    tokens = int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "total_tokens", 0) or 0)
    cost = _cost_for_tokens(tokens)
    record_cost(
        scope="provider:openai",
        ai_provider="openai",
        model_name=EMBEDDING_MODEL,
        cost_usd=float(cost),
        tokens_in=tokens,
        extra_scopes=["monthly_total"],
    )
    return vector, {
        "embedding_model": EMBEDDING_MODEL,
        "query_embedding_tokens": tokens,
        "query_embedding_cost_usd_estimate": float(cost),
        "embedding_transport": transport,
    }


# 维度守卫(每进程一次,读侧防线):collection 向量维必须 == VECTOR_SIZE。防未来有人把
# EMBEDDING_MODEL 换成别家(维度对不上立刻炸出 recall_degraded,而不是拿错误空间的向量
# 查询返回垃圾召回);模型↔collection 配对由单测冻结,换模型必须开新 collection。
_collection_dim_verified = False


def _assert_collection_dim(client: Any) -> None:
    global _collection_dim_verified
    if _collection_dim_verified:
        return
    _collection_dim_verified = _support.assert_collection_dim(
        client,
        collection_name=COLLECTION_NAME,
        vector_size=VECTOR_SIZE,
    )


def _search_qdrant(query_vector: list[float], candidate_limit: int) -> list[RecallHit]:
    return _support.search_qdrant(
        query_vector,
        candidate_limit,
        qdrant_client_factory=_qdrant_client,
        assert_collection_dim=_assert_collection_dim,
        collection_name=COLLECTION_NAME,
        method=METHOD,
    )


def _dedupe_retrieval_hits(hits: list[RecallHit]) -> list[RecallHit]:
    return _support.dedupe_retrieval_hits(hits)


def _hybrid_fuse_hits(
    vector_hits: list[RecallHit],
    lexical_hits: list[RecallHit],
    *,
    limit: int,
    factual_anchor_required: bool,
) -> list[RecallHit]:
    return _support.hybrid_fuse_hits(
        vector_hits,
        lexical_hits,
        limit=limit,
        factual_anchor_required=factual_anchor_required,
        dedupe_hits=_dedupe_retrieval_hits,
    )

# Preserve the original module surface while implementations live by role.
from app.domains.kol import profile_recall_projection as _projection  # noqa: E402
from app.domains.kol import profile_recall_storage as _storage  # noqa: E402
from app.domains.kol.profile_recall_projection import (  # noqa: E402
    _CORE_VERTICAL_TERMS,
    _EXCLUDED_REGION_CODES,
    _EXCLUDED_REGION_RE,
    _GEAR_CONTENT_TERMS,
    _LANGUAGE_ALIASES,
    _VERTICAL_FILTER_GROUPS,
    _adoption_boost_for,
    _assign_business_buckets,
    _bucket_for,
    _candidate_filter_verdict,
    _country_in_excluded_region,
    _country_match_key,
    _evidence_score,
    _extract_lenses,
    _factual_candidate_signal_blob,
    _filter_values,
    _float,
    _format_item,
    _is_relevance_backfill,
    _language_match_key,
    _llm_rerank_buckets,
    _natural_business_lane,
    _normalise_lens_mention,
    _normalize_bucket_policy,
    _normalize_recall_filters,
    _optional_float,
    _persona_text_for_query,
    _provisional_profile_lane,
    _reason_labels,
    _recall_rank_score,
    _recall_reason,
    _type_label,
    _type_score_for_bucket,
    _vertical_filter_matches,
    _why_fit,
)
from app.domains.kol.profile_recall_relevance import (  # noqa: E402
    BRAND_COLLAB_OVERLOAD_N,
    FRESH_FOLLOWER_CEILING,
    FRESH_PRIORITY_BOOST,
    KOL_STILL_PHOTO_WORDS,
    KOL_VIDEO_SIGNAL_WORDS,
    SATURATED_FOLLOWER_TIERS,
    VIDEO_LEANING_PERSONA_WORDS,
    VIDEO_LEANING_PROFILES,
    WHY_FIT_RULES,
    _brand_collab_count,
    _followers_int,
    _is_video_leaning_product,
    _kol_signal_blob,
    _relevance_signals,
    _still_photo_dominant,
)


def _recall_table_columns(conn: Any, table_name: str) -> set[str]:
    return _support.recall_table_columns(
        conn,
        table_name,
        fallback_table_columns=_table_columns,
    )


def _entry_rows(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    return _storage._entry_rows(
        kol_pool_ids,
        get_connection=get_conn,
        table_columns=_recall_table_columns,
    )


def _pool_rows_fallback(kol_pool_ids: list[int]) -> dict[int, dict]:
    return _storage._pool_rows_fallback(
        kol_pool_ids,
        get_connection=get_conn,
        table_columns=_recall_table_columns,
    )


def _pool_text_fallback_hits(
    query_text: str,
    candidate_limit: int,
    *,
    include_relevance_backfill: bool = True,
    allow_backfill: bool | None = None,
    operator_query_text: str = "",
    filters: dict[str, Any] | None = None,
) -> list[RecallHit]:
    if allow_backfill is not None:
        include_relevance_backfill = bool(allow_backfill)
    hits = _storage._pool_text_fallback_hits(
        query_text,
        candidate_limit,
        include_relevance_backfill=include_relevance_backfill,
        operator_query_text=operator_query_text,
        filters=filters,
        get_connection=get_conn,
        lexical_recall=lexical_recall_candidates,
    )
    if not include_relevance_backfill:
        return [hit for hit in hits if hit.qdrant_point_id != "pool_relevance_backfill"]
    return hits


def _adoption_profile() -> dict:
    return _projection._adoption_profile(get_connection=get_conn)


def _evidence_summaries(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    return _support.evidence_summaries(
        kol_pool_ids,
        get_connection=get_conn,
    )


def _smart_local_evidence_summaries(
    kol_pool_ids: list[int],
) -> dict[int, dict[str, Any]]:
    return _support.smart_local_evidence_summaries(
        kol_pool_ids,
        get_connection=get_conn,
    )


def _smart_local_qualification_context(
    kol_pool_ids: list[int],
    *,
    rows_by_id: dict[int, dict[str, Any]],
    evidence_by_id: dict[int, dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    return _support.smart_local_qualification_context(
        kol_pool_ids,
        rows_by_id=rows_by_id,
        evidence_by_id=evidence_by_id,
        get_connection=get_conn,
        table_columns=_recall_table_columns,
    )



def recall_kol_profiles(
    *,
    query_text: str = "",
    product_sku: str = "",
    candidate_limit: int = 50,
    limit: int = 10,
    creator_quota: int = 7,
    reviewer_quota: int = 3,
    ratio_policy: str = "soft",
    mixed_policy: str = "dominant",
    dedupe: bool = True,
    vector_weight: float = 0.85,
    type_weight: float = 0.15,
    type_boost_enabled: bool = True,
    exclude_chinese: bool = True,
    product_focus: Any = None,
    target_persona: str = "",
    provider_free: bool = False,
    filters: dict[str, Any] | None = None,
    search_strategy: str = "balanced",
    bucket_policy: dict[str, Any] | None = None,
    allow_backfill: bool = True,
    operator_query_text: str = "",
    required_product_evidence_terms: Any = None,
    local_qualification_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recall_started = perf_counter()
    if ratio_policy != "soft":
        raise ValueError("only ratio_policy=soft is supported")
    if mixed_policy != "dominant":
        raise ValueError("only mixed_policy=dominant is supported")

    smart_local_enabled = isinstance(local_qualification_policy, dict)
    if smart_local_enabled:
        # This contract is server-owned: old request flags cannot weaken its
        # evidence gate or re-introduce duplicate/popularity-filled rows.
        allow_backfill = False
        dedupe = True
    requested_candidate_limit = max(1, min(MAX_CANDIDATE_LIMIT, int(candidate_limit or 50)))
    safe_candidate_limit = (
        SMART_LOCAL_CANDIDATE_LIMIT
        if smart_local_enabled
        else requested_candidate_limit
    )
    safe_limit = (
        SMART_LOCAL_TARGET
        if smart_local_enabled
        else max(1, min(50, int(limit or 10)))
    )
    safe_candidate_limit = max(safe_limit, safe_candidate_limit)
    safe_creator_quota = max(0, min(50, int(creator_quota or 0)))
    safe_reviewer_quota = max(0, min(50, int(reviewer_quota or 0)))
    safe_vector_weight = max(0.0, min(1.0, _float(vector_weight)))
    safe_type_weight = max(0.0, min(1.0, _float(type_weight)))
    if safe_creator_quota + safe_reviewer_quota <= 0:
        raise ValueError("creator_quota + reviewer_quota must be greater than 0")
    if type_boost_enabled and safe_vector_weight + safe_type_weight <= 0:
        raise ValueError("vector_weight + type_weight must be greater than 0 when type_boost_enabled=true")
    normalized_filters, unsupported_filters = _normalize_recall_filters(filters)
    retrieval_filters = dict(normalized_filters)
    if normalized_filters.get("countries"):
        retrieval_filters["_country_values"] = sorted({
            value
            for raw in normalized_filters["countries"]
            for value in (
                str(raw).strip().lower(),
                _country_match_key(raw).lower(),
            )
            if value
        })
    if normalized_filters.get("languages"):
        retrieval_filters["_language_values"] = sorted({
            value
            for raw in normalized_filters["languages"]
            for value in (
                str(raw).strip().lower(),
                _language_match_key(raw),
            )
            if value
        })
    if normalized_filters:
        # Hard-filtered searches must not examine only the global high-follower
        # head.  Over-sample before filtering; SQL-safe filters are also pushed
        # into lexical and broad-backfill candidate generation.
        safe_candidate_limit = min(
            MAX_CANDIDATE_LIMIT,
            max(safe_candidate_limit, safe_limit * 16),
        )
    normalized_bucket_policy, bucket_policy_adjusted = _normalize_bucket_policy(
        bucket_policy,
        search_strategy=search_strategy,
        result_limit=safe_limit,
    )

    resolved_text, query_meta = resolve_query_text(query_text=query_text, product_sku=product_sku)
    resolved_at = perf_counter()
    if isinstance(required_product_evidence_terms, dict):
        safe_product_evidence_terms = product_evidence_terms(required_product_evidence_terms)
    else:
        raw_product_terms = (
            required_product_evidence_terms
            if isinstance(required_product_evidence_terms, (list, tuple, set))
            else [required_product_evidence_terms]
        )
        safe_product_evidence_terms = product_evidence_terms(
            {"marketing_name": " ".join(str(item or "") for item in raw_product_terms)}
        )
    # why-fit 人群侧上下文(纯展示):产品线 persona + planner product_focus/target_persona + 原始 query。
    profile_key = str(query_meta.get("query_profile") or "")
    persona_meta = PRODUCT_LINE_PERSONAS.get(profile_key) or {}
    product_label = str(persona_meta.get("label") or persona_meta.get("persona") or "")
    persona_text = _persona_text_for_query(
        {**query_meta, "query_text": resolved_text},
        product_focus,
        target_persona,
    )
    evidence_query_text = f"{resolved_text} {persona_text}".strip() if persona_text else resolved_text
    # 本次 query 是否偏视频/监视器人群(用于纯平面摄影候选的诚实标注与展示降权)。纯展示判据。
    video_leaning = _is_video_leaning_product(query_meta, persona_text, product_focus)
    # 首屏基础召回可显式选择 provider_free:只读本地 pool 文本,不打
    # embedding / Qdrant / LLM rerank。完整向量召回和语义规划保留在后台 worker。
    pool_text_fallback_count = 0
    lexical_candidate_count = 0
    recall_degraded = ""
    if provider_free:
        query_vector, embedding_meta = [], {"recall_mode": "provider_free_pool_text"}
        hits = _pool_text_fallback_hits(
            resolved_text,
            safe_candidate_limit,
            include_relevance_backfill=bool(allow_backfill),
            operator_query_text=operator_query_text,
            filters=retrieval_filters,
        )
        pool_text_fallback_count = len(hits)
        lexical_candidate_count = sum(1 for hit in hits if hit.retrieval_method == LEXICAL_METHOD)
    else:
        try:
            lexical_hits = _pool_text_fallback_hits(
                resolved_text,
                safe_candidate_limit,
                include_relevance_backfill=False,
                operator_query_text=operator_query_text,
                filters=retrieval_filters,
            )
        except Exception:
            logger.warning("profile_recall lexical retrieval unavailable", exc_info=True)
            lexical_hits = []
        lexical_candidate_count = len(lexical_hits)
        try:
            query_vector, embedding_meta = _embed_query(resolved_text)
            vector_hits = _search_qdrant(query_vector, safe_candidate_limit)
            hits = _hybrid_fuse_hits(
                vector_hits,
                lexical_hits,
                limit=safe_candidate_limit,
                factual_anchor_required=query_requires_factual_anchor(
                    resolved_text,
                    operator_query_text,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — 召回不可用时降级,不让 500/503 冒泡
            recall_degraded = _support.classify_recall_failure(exc)
            logger.warning("recall_degraded reason=%s", recall_degraded, exc_info=True)
            query_vector, embedding_meta, hits = [], {}, lexical_hits

    # 红线「召回永不零」(记忆 vkpi-text-search-resurrection):向量命中为空——无论是并发撞
    # Qdrant 文件锁 / 库缺失 / embedding 失败 / 预算超限(recall_degraded 已置),还是该 query
    # 向量库零命中——都回退到 pool 文本兜底,确保并发/降级下召回不返 0。兜底候选进同一展示管线。
    if not hits:
        hits = _pool_text_fallback_hits(
            resolved_text,
            safe_candidate_limit,
            include_relevance_backfill=bool(allow_backfill),
            operator_query_text=operator_query_text,
            filters=retrieval_filters,
        )
        pool_text_fallback_count = len(hits)
    elif allow_backfill and len(hits) < safe_candidate_limit:
        # Vector recall may legitimately return fewer candidates than the UI
        # requested.  Append local-pool rows only as an explicit relevance
        # backfill tier; no provider calls and no hard-filter relaxation.
        known_ids = {hit.kol_pool_id for hit in hits}
        try:
            local_backfill = _pool_text_fallback_hits(
                "",
                safe_candidate_limit,
                include_relevance_backfill=True,
                operator_query_text=operator_query_text,
                filters=retrieval_filters,
            )
        except Exception:
            # Existing vector hits remain valid even when the optional local
            # refill source is unavailable (including isolated unit tests).
            logger.warning("profile_recall local backfill unavailable", exc_info=True)
            local_backfill = []
        hits.extend(hit for hit in local_backfill if hit.kol_pool_id not in known_ids)
        hits = hits[:safe_candidate_limit]
    retrieved_at = perf_counter()

    ordered_hits: list[RecallHit] = []
    seen: set[int] = set()
    duplicate_count = 0
    for hit in hits:
        if dedupe and hit.kol_pool_id in seen:
            duplicate_count += 1
            continue
        seen.add(hit.kol_pool_id)
        ordered_hits.append(hit)

    rows_by_id = _entry_rows([hit.kol_pool_id for hit in ordered_hits])
    evidence_ids = [hit.kol_pool_id for hit in ordered_hits]
    try:
        evidence_by_id = _evidence_summaries(evidence_ids)
    except Exception:
        if not smart_local_enabled:
            raise
        # Older read-only snapshots may lack optional engagement columns used
        # by the richer legacy projection.  Strict local still has a minimal,
        # factual video/title projection and never relaxes qualification.
        logger.warning("smart_local rich evidence projection unavailable", exc_info=True)
        evidence_by_id = _smart_local_evidence_summaries(evidence_ids)
    buckets: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": [], "unknown": []}
    fallback_rows = _pool_rows_fallback([h.kol_pool_id for h in ordered_hits if h.kol_pool_id not in rows_by_id])
    qualification_rows = {**fallback_rows, **rows_by_id}
    if smart_local_enabled:
        qualification_rows, evidence_by_id = _smart_local_qualification_context(
            [hit.kol_pool_id for hit in ordered_hits],
            rows_by_id=qualification_rows,
            evidence_by_id=evidence_by_id,
        )
    evidence_loaded_at = perf_counter()
    fallback_used_count = 0
    missing_type_count = 0
    excluded_chinese_count = 0
    filtered_low_reach_count = 0
    filtered_unknown_reach_count = 0
    hard_filter_rejected_count = 0
    hard_filter_rejected_by: dict[str, int] = {}
    filtered_no_match_evidence_count = 0
    for hit in ordered_hits:
        row = rows_by_id.get(hit.kol_pool_id)
        if not row:
            row = fallback_rows.get(hit.kol_pool_id)
            if not row:
                missing_type_count += 1
                continue
            fallback_used_count += 1
        # P0-6:纯地区判据(CN/HK/TW),不再按汉字名排除;country 为空放行(预期)。
        if exclude_chinese and _country_in_excluded_region(row.get("country")):
            excluded_chinese_count += 1
            continue
        # 召回触达门槛(用户裁决 2026-07-11 + 2026-07-12 第二道闸升级,与地区排除同层的召回
        # FILTER):三态走 _reach_display_state 单一真源——low_reach(followers 明确 < 门槛/
        # 互动实测全零/补全后 low_reach 标)不进「库内已有」推荐列表;unknown(followers 未知)
        # 也不展示(「分析后再 po」),独立计数诚实透出。Pool 行保留不删,只挡本出口。
        # 零触 viltrox_fit_score。
        _reach_state = _reach_display_state(row)
        if not smart_local_enabled and _reach_state == "low_reach":
            filtered_low_reach_count += 1
            logger.debug(
                "recall_reach_floor_filtered handle=%r kol_pool_id=%s reason=%s",
                row.get("handle"), hit.kol_pool_id, _reach_floor_reason(row) or "low_reach_flag",
            )
            continue
        if not smart_local_enabled and _reach_state == "unknown":
            filtered_unknown_reach_count += 1
            logger.debug(
                "recall_reach_unknown_hidden handle=%r kol_pool_id=%s",
                row.get("handle"), hit.kol_pool_id,
            )
            continue
        evidence = evidence_by_id.get(hit.kol_pool_id, {})
        passes_filters, rejected_fields, unknown_fields = _candidate_filter_verdict(
            row,
            evidence,
            normalized_filters,
        )
        if not passes_filters:
            hard_filter_rejected_count += 1
            for field in rejected_fields:
                hard_filter_rejected_by[field] = hard_filter_rejected_by.get(field, 0) + 1
            continue
        # 先按检索词判(老行为);判空再用 检索词∪人群词 兜底——LLM 常给泛角色词被剔光→496/500 判无证据(08-23)
        field_evidence = build_match_evidence(row, evidence, resolved_text, required_product_terms=safe_product_evidence_terms) or (
            build_match_evidence(row, evidence, evidence_query_text, required_product_terms=safe_product_evidence_terms)
            if evidence_query_text != resolved_text else [])
        if not allow_backfill and not field_evidence:
            filtered_no_match_evidence_count += 1
            continue
        bucket = _bucket_for(row, mixed_policy)
        item = _format_item(
            hit,
            row,
            bucket,
            vector_weight=safe_vector_weight,
            type_weight=safe_type_weight,
            type_boost_enabled=bool(type_boost_enabled),
            evidence=evidence,
            persona_text=persona_text,
            product_label=product_label,
            video_leaning=video_leaning,
        )
        if not allow_backfill:
            item["match_evidence"] = list(field_evidence)
            item["why_fit"] = why_fit_from_match_evidence(field_evidence)
            item["candidate_facets"] = candidate_facets(row, evidence)
        retrieval_tier = (
            "backfill"
            if hit.qdrant_point_id == "pool_relevance_backfill"
            else str(hit.retrieval_tier or "backfill")
        )
        if retrieval_tier not in {"strict", "relaxed", "backfill"}:
            retrieval_tier = "relaxed"
        item.update(
            {
                "match_tier": retrieval_tier,
                "filter_status": retrieval_tier,
                "relaxed_filters": (
                    ["query_relevance"]
                    if retrieval_tier == "backfill"
                    else ["factual_query_anchor"]
                    if retrieval_tier == "relaxed"
                    else []
                ),
                "unknown_fields": unknown_fields,
            }
        )
        buckets[bucket].append(item)

    all_ranked_candidates = [item for bucket_items in buckets.values() for item in bucket_items]
    robust_ranking_diagnostics = apply_robust_ranking(all_ranked_candidates)
    _assign_business_buckets(all_ranked_candidates, normalized_bucket_policy)
    for bucket_items in buckets.values():
        # 展示排序用独立的 display_rank_score(= recall_rank_score + 展示 adjust);
        # recall_rank_score / vector_score 原值保留不变,仅作 tie-break 与审计。
        bucket_items.sort(
            key=ranking_key,
            reverse=True,
        )

    # ── 展示层二段增强(2026-07-02 用户令):①采纳回流上浮 ②LLM 头部 rerank。
    # 两段都只动 display_rank_score(与「新人优先」同款展示信号),失败静默、诊断留痕。
    _rerank_note = ""
    try:
        _adoption = _adoption_profile()
        _boosted = 0
        if _adoption:
            for _bucket_items in buckets.values():
                for _it in _bucket_items:
                    _b = _adoption_boost_for(_it, _adoption)
                    if _b:
                        _it["display_rank_score"] = round(_float(_it.get("display_rank_score")) + _b, 6)
                        _it["adoption_boost"] = _b
                        _boosted += 1
        if provider_free:
            _rerank_note = "provider_free_initial"
        elif os.environ.get("RECALL_LLM_RERANK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}:
            _rerank_note = _llm_rerank_buckets(buckets, resolved_text, persona_text, product_label)
        if _boosted or _rerank_note.startswith("ok"):
            for _bucket_items in buckets.values():
                _bucket_items.sort(
                    key=ranking_key,
                    reverse=True,
                )
        _rerank_note = (_rerank_note or "off") + f" boost:{_boosted}"
    except Exception as _rr_exc:
        failure_text = f"{type(_rr_exc).__name__} {_rr_exc}".lower()
        reason = "rerank_timeout" if "timeout" in failure_text or "deadline" in failure_text else "rerank_unavailable"
        logger.warning("profile_recall rerank skipped reason=%s", reason, exc_info=True)
        _rerank_note = f"stage_skipped:{reason}"
    gated_at = perf_counter()

    # Business lanes are now an actual selection contract.  Creator/reviewer
    # remains a soft secondary balance; explicit hard filters were already
    # applied and are never relaxed by lane or count refill.
    local_qualification: dict[str, Any] | None = None
    if smart_local_enabled:
        items, selected_buckets, local_qualification = qualify_local_candidates(
            buckets={
                "creator": buckets["creator"],
                "reviewer": buckets["reviewer"],
            },
            rows_by_id=qualification_rows,
            evidence_by_id=evidence_by_id,
            policy=dict(local_qualification_policy or {}),
            creator_quota=safe_creator_quota,
            reviewer_quota=safe_reviewer_quota,
        )
        selected_creator = selected_buckets["creator"]
        selected_reviewer = selected_buckets["reviewer"]
        selected_unknown: list[dict[str, Any]] = []
        lane_selection = {
            "selection_method": "smart_local_qualification_before_limit",
            "selected_count": len(items),
            "selected_by_lane": {
                lane: sum(1 for item in items if item.get("candidate_bucket") == lane)
                for lane in ("core_vertical", "expansion", "exploration")
            },
        }
    else:
        items, lane_selection = select_with_business_lane_quotas(
            all_ranked_candidates,
            limit=safe_limit,
            bucket_policy=normalized_bucket_policy,
            creator_quota=safe_creator_quota,
            reviewer_quota=safe_reviewer_quota,
            allow_backfill=bool(allow_backfill),
        )
        selected_creator = [item for item in items if item.get("bucket") == "creator"]
        selected_reviewer = [item for item in items if item.get("bucket") == "reviewer"]
        selected_unknown = [item for item in items if item.get("bucket") == "unknown"]
    business_buckets = {
        lane: [item for item in items if item.get("candidate_bucket") == lane]
        for lane in ("core_vertical", "expansion", "exploration")
    }
    strict_count = sum(1 for item in items if item.get("match_tier") == "strict")
    relaxed_count = sum(1 for item in items if item.get("match_tier") == "relaxed")
    backfill_count = sum(1 for item in items if item.get("match_tier") == "backfill")
    strict_available_count = sum(
        1 for item in all_ranked_candidates if item.get("match_tier") == "strict"
    )
    relaxed_available_count = sum(
        1 for item in all_ranked_candidates if item.get("match_tier") == "relaxed"
    )
    backfill_available_count = sum(
        1 for item in all_ranked_candidates if item.get("match_tier") == "backfill"
    )
    creator_take = min(safe_creator_quota, safe_limit)
    reviewer_take = min(safe_reviewer_quota, max(0, safe_limit - creator_take))
    profile_quota_refill_count = max(0, len(selected_creator) - creator_take) + max(
        0, len(selected_reviewer) - reviewer_take
    ) + len(selected_unknown)
    shortfall = max(0, safe_limit - len(items))
    evidence_candidate_count = len(all_ranked_candidates)
    if items:
        empty_reason = ""
        evidence_shortfall_reason = "" if len(items) >= safe_limit else "evidence_candidates_exhausted"
    elif evidence_candidate_count:
        empty_reason = "quota_excluded_evidence_candidates"
        evidence_shortfall_reason = empty_reason
    else:
        empty_reason = "no_evidence_match" if not allow_backfill else ""
        evidence_shortfall_reason = empty_reason
    distribution_rows = {**fallback_rows, **rows_by_id}

    response = {
        "method": METHOD,
        "match_status": "matched" if items else "empty",
        "candidate_set_distribution": candidate_set_distribution(
            items,
            distribution_rows,
            evidence_by_id,
        ),
        "query": {
            **query_meta,
            "query_text": resolved_text,
            "collection_name": COLLECTION_NAME,
            "candidate_limit": safe_candidate_limit,
            "requested_candidate_limit": requested_candidate_limit,
            "limit": safe_limit,
            "product_label": product_label,
            "product_persona": str(persona_meta.get("persona") or ""),
            "video_leaning_product": bool(video_leaning),
            "search_strategy": str(search_strategy or "balanced").strip().lower(),
            "allow_backfill": bool(allow_backfill),
            "required_product_evidence_terms": safe_product_evidence_terms,
        },
        "ratio": {
            "creator_quota": safe_creator_quota,
            "reviewer_quota": safe_reviewer_quota,
            "policy": ratio_policy,
            "mixed_policy": mixed_policy,
            "dedupe": bool(dedupe),
        },
        "filters": {
            "applied": normalized_filters,
            "unsupported": unsupported_filters,
            "hard_filters_relaxed": False,
        },
        "bucket_policy": normalized_bucket_policy,
        "ranking": {
            "type_boost_enabled": bool(type_boost_enabled),
            "vector_weight": safe_vector_weight,
            "type_weight": safe_type_weight,
            "score_formula": "observed_signals_weighted_mean_missing_omitted",
            "robust_rank_method": robust_ranking_diagnostics.get("version"),
            "claim_status": "descriptive_only",
            "note": "robust_rank_score 是检索排序分，不是业务 precision 或预测准确率。",
            **robust_ranking_diagnostics,
        },
        "evaluation_status": build_runtime_evaluation_status(
            algorithm_version=ROBUST_RANK_VERSION,
        ),
        "items": items,
        "buckets": {
            "creator": selected_creator,
            "reviewer": selected_reviewer,
            "unknown": selected_unknown,
        },
        "business_buckets": business_buckets,
        "diagnostics": {
            "candidate_count": len(hits),
            "deduped_candidate_count": len(ordered_hits),
            "duplicate_count": duplicate_count,
            "typed_candidate_count": len(buckets["creator"]) + len(buckets["reviewer"]),
            "unknown_type_candidate_count": len(buckets["unknown"]),
            "missing_type_count": missing_type_count,
            # 召回触达门槛命中数(诚实可见:被挡=从本出口静默缺席,非降分)。
            "filtered_low_reach": filtered_low_reach_count,
            # followers 未知不展示(「分析后再 po」,2026-07-12 裁决);补全回填达标后自动回归。
            "filtered_unknown_reach": filtered_unknown_reach_count,
            "filtered_excluded_region": excluded_chinese_count,
            "hard_filter_rejected_count": hard_filter_rejected_count,
            "hard_filter_rejected_by": hard_filter_rejected_by,
            "filtered_no_match_evidence": filtered_no_match_evidence_count,
            "evidence_gate_enabled": not bool(allow_backfill),
            "empty_reason": empty_reason,
            "shortfall_reason": evidence_shortfall_reason,
            "applied_filters": normalized_filters,
            "unsupported_filters": unsupported_filters,
            "fallback_pool_rows": fallback_used_count,
            "pool_text_fallback_count": pool_text_fallback_count,
            "lexical_candidate_count": lexical_candidate_count,
            "display_rerank": _rerank_note,
            "creator_candidate_count": len(buckets["creator"]),
            "reviewer_candidate_count": len(buckets["reviewer"]),
            "creator_returned": len(selected_creator),
            "reviewer_returned": len(selected_reviewer),
            "unknown_type_returned": len(selected_unknown),
            "returned_count": len(items),
            "requested_count": safe_limit,
            "strict_available_count": strict_available_count,
            "relaxed_available_count": relaxed_available_count,
            "strict_count": strict_count,
            "relaxed_count": relaxed_count,
            "backfill_available_count": backfill_available_count,
            "backfill_count": backfill_count,
            "profile_quota_refill_count": profile_quota_refill_count,
            "final_count": len(items),
            "shortfall": shortfall,
            "result_contract_satisfied": shortfall == 0,
            "result_contract_note": "仅表示数量达到且硬筛选未放宽，不代表检索精准度。",
            "backfill_policy": (
                "query_relevance_only_hard_filters_never_relaxed"
                if allow_backfill
                else "disabled_evidence_gate"
            ),
            "bucket_policy": normalized_bucket_policy,
            "bucket_policy_adjusted": bucket_policy_adjusted,
            "business_bucket_counts": {
                key: len(value) for key, value in business_buckets.items()
            },
            "lane_selection": lane_selection,
            "recall_degraded": recall_degraded,
            "provider_free_initial": bool(provider_free),
            **embedding_meta,
        },
    }
    if local_qualification is not None:
        completed_at = perf_counter()
        total_ms = round((completed_at - recall_started) * 1000.0, 3)
        stage_timing = local_qualification["stage_timing"]
        stage_timing.update(
            {
                "resolve_query_ms": round((resolved_at - recall_started) * 1000.0, 3),
                "retrieve_ms": round((retrieved_at - resolved_at) * 1000.0, 3),
                "load_evidence_ms": round((evidence_loaded_at - retrieved_at) * 1000.0, 3),
                "evidence_gate_ms": round((gated_at - evidence_loaded_at) * 1000.0, 3),
                "rank_and_select_ms": round((completed_at - gated_at) * 1000.0, 3),
                "total_ms": total_ms,
            }
        )
        local_qualification["total_ms"] = total_ms
        response["local_qualification"] = local_qualification
        response["match_status"] = "matched" if items else "empty"
        response["diagnostics"].update(
            {
                "returned_count": len(items),
                "creator_returned": len(selected_creator),
                "reviewer_returned": len(selected_reviewer),
                "shortfall": local_qualification["shortfall"],
                "shortfall_reason": local_qualification["shortfall_reason"],
                "empty_reason": "" if items else "no_qualified_candidates",
                "result_contract_satisfied": local_qualification["shortfall"] == 0,
                "smart_local_qualification": True,
            }
        )
        response = project_smart_local_result(response)
        # business_buckets is a secondary view of the same selected rows.
        # Rebuild it from the already-projected canonical items so profile text
        # or contact-bearing bio values cannot survive through an alias view.
        response["business_buckets"] = {
            lane: [
                item
                for item in response.get("items") or []
                if item.get("candidate_bucket") == lane
            ]
            for lane in ("core_vertical", "expansion", "exploration")
        }
    return response
