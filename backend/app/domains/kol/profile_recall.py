"""KOL profile recall orchestration with compatibility re-exports."""
from __future__ import annotations

from decimal import Decimal
import os
import re
import sys
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
from app.domains.kol.profile_recall_funnel import RecallStageLedger
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
from app.domains.kol.profile_query_cell_evidence import build_query_cell_match_evidence
from app.domains.kol.profile_recall_match_evidence import (
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
from app.domains.kol import profile_recall_display_boost as _display_boost
from app.domains.kol import recall_favorite_exclusion as _favorite_exclusion
from app.domains.kol.profile_vertical_signals import classify_verticals, vertical_explanations
from app.domains.kol.search_relevance_eval import build_runtime_evaluation_status
from app.domains.kol.profile_recall_orchestration import run_recall_pipeline
from app.domains.kol.profile_recall_orchestration_contract import RecallRequest
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
    server_candidate_limit_override: int | None = None,
    targeted_query_cell: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recall KOL profiles through the phase-oriented orchestration pipeline."""
    recall_started = perf_counter()
    if ratio_policy != "soft":
        raise ValueError("only ratio_policy=soft is supported")
    if mixed_policy != "dominant":
        raise ValueError("only mixed_policy=dominant is supported")

    smart_local_enabled = isinstance(local_qualification_policy, dict)
    if smart_local_enabled:
        # Server-owned qualification cannot be weakened by legacy flags.
        allow_backfill = False
        dedupe = True
    requested_candidate_limit = max(
        1,
        min(MAX_CANDIDATE_LIMIT, int(candidate_limit or 50)),
    )
    safe_limit = (
        SMART_LOCAL_TARGET
        if smart_local_enabled
        else max(1, min(50, int(limit or 10)))
    )
    safe_candidate_limit, safe_server_candidate_limit_override = (
        _support.smart_local_candidate_limit(
            smart_local_enabled=smart_local_enabled,
            requested_candidate_limit=requested_candidate_limit,
            result_limit=safe_limit,
            server_override=server_candidate_limit_override,
            max_candidate_limit=MAX_CANDIDATE_LIMIT,
            smart_local_default=SMART_LOCAL_CANDIDATE_LIMIT,
        )
    )
    server_candidate_limit_override_applied = (
        safe_server_candidate_limit_override is not None
    )
    safe_creator_quota = max(0, min(50, int(creator_quota or 0)))
    safe_reviewer_quota = max(0, min(50, int(reviewer_quota or 0)))
    safe_vector_weight = max(0.0, min(1.0, _float(vector_weight)))
    safe_type_weight = max(0.0, min(1.0, _float(type_weight)))
    if safe_creator_quota + safe_reviewer_quota <= 0:
        raise ValueError("creator_quota + reviewer_quota must be greater than 0")
    if type_boost_enabled and safe_vector_weight + safe_type_weight <= 0:
        raise ValueError(
            "vector_weight + type_weight must be greater than 0 "
            "when type_boost_enabled=true"
        )

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
    if normalized_filters and not server_candidate_limit_override_applied:
        safe_candidate_limit = min(
            MAX_CANDIDATE_LIMIT,
            max(safe_candidate_limit, safe_limit * 16),
        )
    normalized_bucket_policy, bucket_policy_adjusted = _normalize_bucket_policy(
        bucket_policy,
        search_strategy=search_strategy,
        result_limit=safe_limit,
    )

    request = RecallRequest(
        recall_started=recall_started,
        query_text=query_text,
        product_sku=product_sku,
        safe_candidate_limit=safe_candidate_limit,
        requested_candidate_limit=requested_candidate_limit,
        safe_server_candidate_limit_override=safe_server_candidate_limit_override,
        server_candidate_limit_override_applied=server_candidate_limit_override_applied,
        safe_limit=safe_limit,
        safe_creator_quota=safe_creator_quota,
        safe_reviewer_quota=safe_reviewer_quota,
        safe_vector_weight=safe_vector_weight,
        safe_type_weight=safe_type_weight,
        ratio_policy=ratio_policy,
        mixed_policy=mixed_policy,
        dedupe=dedupe,
        type_boost_enabled=type_boost_enabled,
        exclude_chinese=exclude_chinese,
        product_focus=product_focus,
        target_persona=target_persona,
        provider_free=provider_free,
        normalized_filters=normalized_filters,
        unsupported_filters=unsupported_filters,
        retrieval_filters=retrieval_filters,
        search_strategy=search_strategy,
        normalized_bucket_policy=normalized_bucket_policy,
        bucket_policy_adjusted=bucket_policy_adjusted,
        allow_backfill=allow_backfill,
        operator_query_text=operator_query_text,
        required_product_evidence_terms=required_product_evidence_terms,
        local_qualification_policy=local_qualification_policy,
        smart_local_enabled=smart_local_enabled,
        targeted_query_cell=targeted_query_cell,
    )
    # Degrade classification is implemented by
    # _support.classify_recall_failure(exc) in profile_recall_orchestration.
    return run_recall_pipeline(request, deps=sys.modules[__name__])
