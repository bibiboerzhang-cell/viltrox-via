"""KOL profile recall orchestration with compatibility re-exports."""
from __future__ import annotations

from decimal import Decimal
import os
import re
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
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"openai_sdk_unavailable: {exc}") from exc

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("missing_openai_api_key")
    # api.openai.com 在本网络下通常不可直连(SSL 握手超时,区域封锁);走与 yt-dlp 同一残留代理
    # (YTDLP_PROXY / OPENAI_PROXY)可达(实测直连 ConnectTimeout、走代理 HTTP200 2.9s)。
    # direct=True 仅供 403 failover 末位尝试(trust_env=False,防 env HTTPS_PROXY 把"直连"
    # 悄悄绕回同一个被拉黑代理出口);失败由调用方接管,不比现状差。
    if direct:
        try:
            import httpx

            return OpenAI(api_key=api_key, http_client=httpx.Client(trust_env=False, timeout=timeout))
        except Exception as exc:
            raise RuntimeError(f"openai_direct_client_unavailable: {exc}") from exc
    proxy = (proxy_override or os.getenv("OPENAI_PROXY") or os.getenv("YTDLP_PROXY") or "").strip()
    if proxy:
        try:
            import httpx

            return OpenAI(api_key=api_key, http_client=httpx.Client(proxy=proxy, timeout=timeout))
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
    return OpenAI(api_key=api_key)


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
    m = re.match(r"^(?P<base>.+):(?P<port>\d+)/?$", str(proxy or "").strip())
    if not m:
        return []
    base, current_port = m.group("base"), m.group("port")
    raw = os.getenv(EMBED_PROXY_ROTATE_PORTS_ENV, EMBED_PROXY_ROTATE_PORTS_DEFAULT)
    out: list[str] = []
    for token in str(raw).split(","):
        token = token.strip()
        if token.isdigit() and token != current_port and f"{base}:{token}" not in out:
            out.append(f"{base}:{token}")
    return out


def _should_failover(exc: Exception) -> bool:
    """是否值得换出口重试:403(CF 按出口 IP 拉黑)或连接类失败。

    401/429/400 等其余 API 错误换出口无意义,立刻上抛(降级诊断照旧诚实)。
    鸭子类型判据(status_code 属性 + 异常类名),不强依赖 openai/httpx 异常类导入。
    """
    if getattr(exc, "status_code", None) == 403:
        return True
    return exc.__class__.__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ProxyError",
        "ReadTimeout",
    }

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
    size: Any = None
    try:
        info = client.get_collection(COLLECTION_NAME)
        vectors = info.config.params.vectors
        size = getattr(vectors, "size", None)
        if size is None and isinstance(vectors, dict) and vectors:  # named-vectors 形态兜底
            size = getattr(next(iter(vectors.values())), "size", None)
    except Exception:
        # 结构解析不了(qdrant-client 版本漂移)不挡召回:守卫是纵深防线,
        # 主防线是 _embed_query 的向量尺寸检查 + Qdrant 自身的维度校验。
        logger.debug("collection_dim_probe_failed", exc_info=True)
        return
    if size is not None and int(size) != VECTOR_SIZE:
        raise RuntimeError(f"qdrant_collection_dim_mismatch:{size}!={VECTOR_SIZE}")
    _collection_dim_verified = True


def _search_qdrant(query_vector: list[float], candidate_limit: int) -> list[RecallHit]:
    from qdrant_client.http import models as qdrant_models

    client = _qdrant_client()
    if not client.collection_exists(COLLECTION_NAME):
        raise RuntimeError(f"qdrant_collection_missing:{COLLECTION_NAME}")
    _assert_collection_dim(client)
    query_filter = qdrant_models.Filter(
        must=[
            qdrant_models.FieldCondition(
                key="method",
                match=qdrant_models.MatchValue(value=METHOD),
            )
        ]
    )
    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=query_filter,
            limit=max(1, int(candidate_limit)),
            with_payload=True,
        )
        points = list(getattr(response, "points", []) or [])
    else:  # pragma: no cover - older qdrant-client fallback
        points = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=max(1, int(candidate_limit)),
            with_payload=True,
        )

    hits: list[RecallHit] = []
    for point in points:
        payload = dict(getattr(point, "payload", None) or {})
        try:
            kol_pool_id = int(payload.get("kol_pool_id") or 0)
        except (TypeError, ValueError):
            kol_pool_id = 0
        if kol_pool_id <= 0:
            continue
        hits.append(
            RecallHit(
                kol_pool_id=kol_pool_id,
                vector_score=float(getattr(point, "score", 0.0) or 0.0),
                qdrant_point_id=str(getattr(point, "id", "") or ""),
            )
        )
    return hits


def _dedupe_retrieval_hits(hits: list[RecallHit]) -> list[RecallHit]:
    output: list[RecallHit] = []
    seen: set[int] = set()
    for hit in hits:
        if hit.kol_pool_id <= 0 or hit.kol_pool_id in seen:
            continue
        seen.add(hit.kol_pool_id)
        output.append(hit)
    return output


def _hybrid_fuse_hits(
    vector_hits: list[RecallHit],
    lexical_hits: list[RecallHit],
    *,
    limit: int,
    factual_anchor_required: bool,
) -> list[RecallHit]:
    """Deterministic weighted RRF; missing sources are absent, never score zero."""

    vectors = _dedupe_retrieval_hits(vector_hits)
    lexicals = _dedupe_retrieval_hits(lexical_hits)
    if not vectors:
        return lexicals[:limit]
    if not lexicals:
        if not factual_anchor_required:
            return [
                RecallHit(
                    kol_pool_id=hit.kol_pool_id,
                    vector_score=hit.vector_score,
                    qdrant_point_id=hit.qdrant_point_id,
                    retrieval_score=hit.vector_score,
                    retrieval_method="vector_v1",
                    retrieval_tier="relaxed",
                    retrieval_meta={"relaxed_reason": "fielded_factual_gate_unproven"},
                )
                for hit in vectors[:limit]
            ]
        return [
            RecallHit(
                kol_pool_id=hit.kol_pool_id,
                vector_score=hit.vector_score,
                qdrant_point_id=hit.qdrant_point_id,
                retrieval_score=hit.vector_score,
                retrieval_method="vector_v1",
                retrieval_tier="relaxed",
                retrieval_meta={"relaxed_reason": "factual_product_anchor_unproven"},
            )
            for hit in vectors[:limit]
        ]

    vector_rank = {hit.kol_pool_id: rank for rank, hit in enumerate(vectors, 1)}
    lexical_rank = {hit.kol_pool_id: rank for rank, hit in enumerate(lexicals, 1)}
    vector_by_id = {hit.kol_pool_id: hit for hit in vectors}
    lexical_by_id = {hit.kol_pool_id: hit for hit in lexicals}
    max_rrf = 0.60 / 61.0 + 0.40 / 61.0
    fused: list[RecallHit] = []
    for kol_pool_id in sorted(set(vector_rank) | set(lexical_rank)):
        vector_hit = vector_by_id.get(kol_pool_id)
        lexical_hit = lexical_by_id.get(kol_pool_id)
        raw_rrf = (
            (0.60 / (60 + vector_rank[kol_pool_id]) if kol_pool_id in vector_rank else 0.0)
            + (0.40 / (60 + lexical_rank[kol_pool_id]) if kol_pool_id in lexical_rank else 0.0)
        )
        rrf_score = round(raw_rrf / max_rrf, 6)
        lexical_tier = lexical_hit.retrieval_tier if lexical_hit else ""
        if factual_anchor_required:
            retrieval_tier = "strict" if lexical_tier == "strict" else "relaxed"
            relaxed_reason = "" if retrieval_tier == "strict" else "factual_product_anchor_unproven"
        else:
            retrieval_tier = "strict" if lexical_tier == "strict" else "relaxed"
            relaxed_reason = "" if retrieval_tier == "strict" else "fielded_factual_gate_unproven"
        fused.append(
            RecallHit(
                kol_pool_id=kol_pool_id,
                vector_score=vector_hit.vector_score if vector_hit else None,
                qdrant_point_id=vector_hit.qdrant_point_id if vector_hit else "",
                lexical_score=lexical_hit.lexical_score if lexical_hit else None,
                retrieval_score=rrf_score,
                retrieval_method=HYBRID_METHOD,
                retrieval_tier=retrieval_tier,
                hybrid_rrf_score=rrf_score,
                retrieval_meta={
                    **(lexical_hit.retrieval_meta if lexical_hit else {}),
                    "relaxed_reason": relaxed_reason or (
                        lexical_hit.retrieval_meta.get("relaxed_reason", "") if lexical_hit else ""
                    ),
                    "vector_rank": vector_rank.get(kol_pool_id),
                    "lexical_rank": lexical_rank.get(kol_pool_id),
                    "rrf_k": 60,
                    "rrf_weights": {"vector": 0.60, "lexical": 0.40},
                },
            )
        )
    fused.sort(
        key=lambda hit: (
            hit.retrieval_tier == "strict",
            float(hit.hybrid_rrf_score or 0.0),
            -(vector_rank.get(hit.kol_pool_id) or 10**9),
            -(lexical_rank.get(hit.kol_pool_id) or 10**9),
            -hit.kol_pool_id,
        ),
        reverse=True,
    )
    return fused[:limit]

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


def _entry_rows(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    return _storage._entry_rows(
        kol_pool_ids,
        get_connection=get_conn,
        table_columns=_table_columns,
    )


def _pool_rows_fallback(kol_pool_ids: list[int]) -> dict[int, dict]:
    return _storage._pool_rows_fallback(
        kol_pool_ids,
        get_connection=get_conn,
        table_columns=_table_columns,
    )


def _pool_text_fallback_hits(
    query_text: str,
    candidate_limit: int,
    *,
    include_relevance_backfill: bool = False,
    operator_query_text: str = "",
    filters: dict[str, Any] | None = None,
) -> list[RecallHit]:
    return _storage._pool_text_fallback_hits(
        query_text,
        candidate_limit,
        include_relevance_backfill=include_relevance_backfill,
        operator_query_text=operator_query_text,
        filters=filters,
        get_connection=get_conn,
        lexical_recall=lexical_recall_candidates,
    )


def _adoption_profile() -> dict:
    return _projection._adoption_profile(get_connection=get_conn)


def _evidence_summaries(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    return _projection._evidence_summaries(kol_pool_ids, get_connection=get_conn)



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
) -> dict[str, Any]:
    if ratio_policy != "soft":
        raise ValueError("only ratio_policy=soft is supported")
    if mixed_policy != "dominant":
        raise ValueError("only mixed_policy=dominant is supported")

    requested_candidate_limit = max(1, min(MAX_CANDIDATE_LIMIT, int(candidate_limit or 50)))
    safe_candidate_limit = requested_candidate_limit
    safe_limit = max(1, min(50, int(limit or 10)))
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
    # why-fit 人群侧上下文(纯展示):产品线 persona + planner product_focus/target_persona + 原始 query。
    profile_key = str(query_meta.get("query_profile") or "")
    persona_meta = PRODUCT_LINE_PERSONAS.get(profile_key) or {}
    product_label = str(persona_meta.get("label") or persona_meta.get("persona") or "")
    persona_text = _persona_text_for_query(
        {**query_meta, "query_text": resolved_text},
        product_focus,
        target_persona,
    )
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
            failure_text = f"{type(exc).__name__} {exc}".lower()
            recall_degraded = "embedding_timeout" if "timeout" in failure_text or "deadline" in failure_text else "embedding_unavailable"
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
    evidence_by_id = _evidence_summaries([hit.kol_pool_id for hit in ordered_hits])
    buckets: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": [], "unknown": []}
    fallback_rows = _pool_rows_fallback([h.kol_pool_id for h in ordered_hits if h.kol_pool_id not in rows_by_id])
    fallback_used_count = 0
    missing_type_count = 0
    excluded_chinese_count = 0
    filtered_low_reach_count = 0
    filtered_unknown_reach_count = 0
    hard_filter_rejected_count = 0
    hard_filter_rejected_by: dict[str, int] = {}
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
        if _reach_state == "low_reach":
            filtered_low_reach_count += 1
            logger.debug(
                "recall_reach_floor_filtered handle=%r kol_pool_id=%s reason=%s",
                row.get("handle"), hit.kol_pool_id, _reach_floor_reason(row) or "low_reach_flag",
            )
            continue
        if _reach_state == "unknown":
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

    # Business lanes are now an actual selection contract.  Creator/reviewer
    # remains a soft secondary balance; explicit hard filters were already
    # applied and are never relaxed by lane or count refill.
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

    return {
        "method": METHOD,
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
            "backfill_policy": "query_relevance_only_hard_filters_never_relaxed",
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
