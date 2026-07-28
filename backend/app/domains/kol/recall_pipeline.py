"""KOL 语义召回三段式管线(semantic_recall_v1)——词表召回漏语义相近创作者的补刀。

三段式(全程 payload.stages 可追溯):
① 召回段:优先 embedding —— OpenAI text-embedding-3-small 查询向量 → 本地 Qdrant
   vkpi_kol_profile_index_v1(档案级索引,345 点位)。守卫 import 复用 profile_recall
   的 _embed_query/_search_qdrant(预算闸在 _embed_query 内走 budget_guard.check_budget
   'provider:openai';代理由 runtime_env.sh 注入的 HTTPS_PROXY/YTDLP_PROXY 生效,
   本模块只读 env 绝不改)。embedding 不可用(无网/无 key/预算闸/Qdrant 缺)→
   决定性降级:字符 3-gram Dice + 词表标签重合 混合召回,method 诚实标
   ngram_fallback_v0,零 LLM、双跑一致可复算。
② 粗排段:十一维余弦 —— video_similarity 的向量口径(守卫 import 复用其
   _numeric_vector/_cosine/_loads/_as_dict 工具函数,不改该文件):候选 KOL 的
   final_v1 十一维均值向量 vs 召回头部原型向量的余弦,与召回分 0.7/0.3 融合。
   候选无深析向量 → 该项诚实退回召回分(basis 如实标注),绝不硬凑。
③ 重排段:LLM 只对 top-20 重排并逐人一句「为何合适」——llm_gateway.invoke
   (带预算闸,preflight 再走 budget_guard.check_budget)+ 当日缓存
   (vkpi_analysis_cache,同 competitor_exposure 模式)。预算不足/LLM 不可用/
   解析失败 → 诚实标 rerank=skipped 返回粗排序,绝不编造理由。

payload 恒带 stages{recall:{method,candidates},coarse:{n},rerank:{status,cost_note}}。

compat 约定:SQL 占位符用 ?;SQL 字符串零字面 percent(不用 LIKE);JSONB 读回
dict/str 双态容错;BOOLEAN 读回可能是 int 1/0。函数内懒 import get_conn。
红线:纯读聚合(唯一写入=当日缓存表 vkpi_analysis_cache,非分数列),绝不写
viltrox_fit_score / 任何评分列,零触 rule_v0;独立展示信号,不参与 V6 Fit 评分。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

PIPELINE_METHOD = "semantic_recall_v1"
RECALL_METHOD_EMBEDDING = "profile_embedding_v1"
RECALL_METHOD_FALLBACK = "ngram_fallback_v0"
RERANK_PURPOSE = "vkpi_semantic_recall_rerank_v1"
RERANK_COST_TAG = "kol_recall"

CACHE_TARGET_TYPE = "semantic_recall"
CACHE_DERIVE_METHOD = "semantic_recall_rerank_v1"

MAX_LIMIT = 50
DEFAULT_LIMIT = 20
RECALL_CANDIDATE_LIMIT = 80  # 召回段候选池上限(粗排的输入)
RERANK_TOP_N = 20            # LLM 只看粗排 top-20(成本闸的一半是少喂)
PROTOTYPE_TOP_K = 10         # 粗排原型向量取召回头部 K 人的均值

# 粗排融合权重(决定性常量;十一维向量缺失时整权退给召回分,item.basis 如实报)
W_RECALL = 0.7
W_DIMS = 0.3
# 重排融合权重(LLM 相关性只占 0.3,粗排分保底,防 LLM 单点跑飞)
W_COARSE_IN_RERANK = 0.7
W_LLM = 0.3

# 降级召回的混合权重(决定性)
W_TRIGRAM = 0.7
W_TAGS = 0.3

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9一-鿿]+")


# ── 通用小工具(决定性、容错)────────────────────────────────────────


def _clean_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


def _stable_failure_reason(exc: BaseException, *, operation: str) -> str:
    """Map implementation failures to frontend-safe, deterministic reason codes."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timeout" in message or "deadline" in message:
        return f"{operation}_timeout"
    return f"{operation}_unavailable"


def _stable_gateway_reason(value: Any) -> str:
    reason = str(value or "").strip().lower()
    if "timeout" in reason or "deadline" in reason:
        return "llm_timeout"
    allowed = {
        "all_providers_failed",
        "budget_exceeded",
        "empty_response",
        "invalid_json",
        "not_configured",
        "parse_failure",
        "provider_unavailable",
    }
    return reason if reason in allowed else "llm_unavailable"


def _float(value: Any) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _loads_json(value: Any) -> Any:
    """JSONB 经 compat 层可能回 dict 也可能回 str,双态容错。"""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _query_hash(query_text: str) -> str:
    normalised = _WS_RE.sub(" ", str(query_text or "").strip().lower())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:40]


# ── 地区排除(P0-6 口径:按地区 CN/HK/TW,非按中文;守卫 import + 本地兜底)──

_EXCLUDED_REGION_RE = re.compile(
    r"中国|中國|大陆|大陸|香港|台湾|台灣|hong\s*kong|taiwan|china", re.IGNORECASE
)
_EXCLUDED_REGION_CODES = {"CN", "HK", "TW", "CHINA"}


def _country_excluded(value: Any) -> bool:
    try:
        from app.domains.kol.profile_recall import _country_in_excluded_region

        return bool(_country_in_excluded_region(value))
    except Exception:
        text = str(value or "").strip()
        if not text:
            return False
        if text.upper() in _EXCLUDED_REGION_CODES:
            return True
        return bool(_EXCLUDED_REGION_RE.search(text))


# ── 降级召回:字符 3-gram + 词表标签(决定性,零 LLM)──────────────────

# 词表标签兜底(profile_recall.PROFILE_REASON_KEYWORDS 守卫 import 失败时用;
# 与其口径同源,宁缺毋滥)
_FALLBACK_REASON_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("人像", ("portrait", "portraits", "人像", "model", "fashion", "bokeh")),
    ("街拍", ("street", "街拍", "street photography", "stranger")),
    ("婚礼", ("wedding", "engagement", "婚礼")),
    ("纪实", ("documentary", "storytelling", "cinematic", "film", "filmmaker", "video", "叙事", "电影感")),
    ("测评", ("review", "comparison", "test", "unboxing", "评测", "对比")),
    ("旅行", ("travel", "landscape", "city", "城市", "风光")),
)


def _reason_keyword_table() -> tuple[tuple[str, tuple[str, ...]], ...]:
    try:
        from app.domains.kol.profile_recall import PROFILE_REASON_KEYWORDS

        return tuple(PROFILE_REASON_KEYWORDS)
    except Exception:
        return _FALLBACK_REASON_KEYWORDS


def _norm_blob(text: Any, limit: int = 1600) -> str:
    return " ".join(_WORD_RE.findall(str(text or "").lower()))[:limit]


def _trigrams(text: str) -> set[str]:
    """字符 3-gram 集合(词内滚动;短词整词入集,中文单字也能命中)。"""
    grams: set[str] = set()
    for word in text.split(" "):
        if not word:
            continue
        if len(word) <= 3:
            grams.add(word)
            continue
        for i in range(len(word) - 2):
            grams.add(word[i : i + 3])
    return grams


def _dice(a: set[str], b: set[str]) -> float:
    """Dice 系数;任一侧为空诚实 0(没得比,不装相似)。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return (2.0 * inter) / (len(a) + len(b))


def _tag_labels(blob: str) -> set[str]:
    labels: set[str] = set()
    for label, keywords in _reason_keyword_table():
        if any(str(k).lower() in blob for k in keywords):
            labels.add(str(label))
    return labels


def _tag_overlap(query_tags: set[str], cand_tags: set[str]) -> float:
    """标签重合率(按 query 侧覆盖比;query 无标签诚实 0)。"""
    if not query_tags:
        return 0.0
    return len(query_tags & cand_tags) / len(query_tags)


def _load_fallback_corpus(conn: Any) -> list[dict[str, Any]]:
    """降级召回语料:全池 KOL(非重复)LEFT JOIN 档案索引文本(有则用,无则 bio 顶)。

    ~1200 行级全扫,读端一把梭;compat 占位符 ?,零字面 percent。
    """
    rows = conn.execute(
        """
        SELECT p.id AS kol_pool_id,
               p.handle, p.display_name, p.platform, p.profile_url, p.avatar_url,
               p.followers, p.bio, p.country, p.primary_topic, p.content_style,
               e.profile_text, e.profile_type, e.type_reason
        FROM vkpi_kol_pool p
        LEFT JOIN vkpi_kol_profile_index_entries e
          ON e.kol_pool_id = p.id
         AND e.collection_name = ?
         AND e.method = ?
         AND e.status = 'ready'
        WHERE p.duplicate_of_id IS NULL
        ORDER BY p.id
        """,
        ("vkpi_kol_profile_index_v1", "vector_recall"),
    ).fetchall()
    return [dict(r) for r in rows]


def _ngram_recall(conn: Any, query_text: str, candidate_limit: int) -> list[dict[str, Any]]:
    """决定性降级召回:score = 0.7 × trigram_dice + 0.3 × tag_overlap,双跑一致。"""
    query_blob = _norm_blob(query_text, 400)
    query_grams = _trigrams(query_blob)
    query_tags = _tag_labels(query_blob)
    scored: list[dict[str, Any]] = []
    for row in _load_fallback_corpus(conn):
        cand_blob = _norm_blob(
            " ".join(
                str(row.get(k) or "")
                for k in ("profile_text", "bio", "primary_topic", "content_style", "type_reason", "display_name", "handle")
            )
        )
        if not cand_blob:
            continue
        trigram = _dice(query_grams, _trigrams(cand_blob))
        tags = _tag_overlap(query_tags, _tag_labels(cand_blob))
        score = W_TRIGRAM * trigram + W_TAGS * tags
        if score <= 0:
            continue
        scored.append(
            {
                **row,
                "recall_score": round(score, 6),
                "recall_basis": "trigram+tags",
                "recall_parts": {"trigram_dice": round(trigram, 6), "tag_overlap": round(tags, 6)},
            }
        )
    # 决定性排序:分数降序,kol_pool_id 升序兜平局
    scored.sort(key=lambda it: (-_float(it.get("recall_score")), int(it.get("kol_pool_id") or 0)))
    return scored[: max(1, int(candidate_limit))]


# ── embedding 召回(守卫 import 复用 profile_recall,不改该文件)────────


def _embedding_recall(
    conn: Any,
    query_text: str,
    candidate_limit: int,
    *,
    embed_fn: Callable[[str], tuple[list[float], dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """embedding 召回:失败(无网/无 key/预算闸/Qdrant 缺)由调用方接管降级。

    embed_fn 仅供测试打桩(mock 断网路径);生产恒走 profile_recall._embed_query
    (其内部已带 budget_guard.check_budget('provider:openai') 硬闸 + 记账)。
    """
    from app.domains.kol import profile_recall as pr

    embed = embed_fn or pr._embed_query
    query_vector, embedding_meta = embed(query_text)
    hits = pr._search_qdrant(query_vector, max(1, int(candidate_limit)))

    # 去重(同 KOL 多点位保最高分;qdrant 已按分降序返回)
    ordered: list[Any] = []
    seen: set[int] = set()
    for hit in hits:
        if hit.kol_pool_id in seen:
            continue
        seen.add(hit.kol_pool_id)
        ordered.append(hit)

    ids = [h.kol_pool_id for h in ordered]
    rows_by_id = pr._entry_rows(ids)
    fallback_rows = pr._pool_rows_fallback([i for i in ids if i not in rows_by_id])
    items: list[dict[str, Any]] = []
    for hit in ordered:
        row = rows_by_id.get(hit.kol_pool_id) or fallback_rows.get(hit.kol_pool_id)
        if not row:
            continue
        items.append(
            {
                **row,
                "recall_score": round(float(hit.vector_score), 6),
                "recall_basis": "vector",
                "recall_parts": {"vector_score": round(float(hit.vector_score), 6)},
            }
        )
    return items, dict(embedding_meta or {})


# ── 粗排段:十一维余弦(video_similarity 向量口径,守卫 import)────────


def _kol_dim_vectors(conn: Any, kol_pool_ids: list[int]) -> dict[int, dict[str, float]]:
    """候选 KOL → final_v1 十一维均值向量(同 KOL 多视频按维取均值;缺维不补零)。"""
    if not kol_pool_ids:
        return {}
    try:
        from app.domains.kol.video_similarity import _as_dict, _loads, _numeric_vector
    except Exception:
        logger.warning("video_similarity utils unavailable; coarse stage degrades to recall order", exc_info=True)
        return {}
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    rows = conn.execute(
        f"""
        SELECT kol_pool_id, llm_dimensions_11 AS dims
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE analysis_kind = 'video_final_v1'
          AND status = 'ready'
          AND llm_dimensions_11 IS NOT NULL
          AND kol_pool_id IN ({placeholders})
        ORDER BY id
        """,
        tuple(int(x) for x in kol_pool_ids),
    ).fetchall()
    sums: dict[int, dict[str, float]] = {}
    counts: dict[int, dict[str, int]] = {}
    for raw in rows:
        row = dict(raw)
        kid = _int_or_none(row.get("kol_pool_id"))
        if kid is None:
            continue
        dims = _as_dict(_loads(row.get("dims")))
        if not dims:
            continue
        vector = _numeric_vector(dims)
        if not vector:
            continue
        s = sums.setdefault(kid, {})
        c = counts.setdefault(kid, {})
        for key, value in vector.items():
            s[key] = s.get(key, 0.0) + float(value)
            c[key] = c.get(key, 0) + 1
    out: dict[int, dict[str, float]] = {}
    for kid, s in sums.items():
        out[kid] = {key: s[key] / counts[kid][key] for key in s if counts[kid].get(key)}
    return out


def _prototype_vector(candidates: list[dict[str, Any]], vectors: dict[int, dict[str, float]]) -> dict[str, float]:
    """召回头部 K 人的均值向量作原型(伪相关反馈口径;不足 1 人诚实空)。"""
    head = [c for c in candidates[:PROTOTYPE_TOP_K] if int(c.get("kol_pool_id") or 0) in vectors]
    if not head:
        return {}
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for cand in head:
        vec = vectors[int(cand["kol_pool_id"])]
        for key, value in vec.items():
            sums[key] = sums.get(key, 0.0) + float(value)
            counts[key] = counts.get(key, 0) + 1
    return {key: sums[key] / counts[key] for key in sums if counts.get(key)}


def _coarse_rank(conn: Any, candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """粗排:coarse_score = 0.7 × recall_norm + 0.3 × cosine(候选十一维, 原型)。

    十一维缺(没深析/工具不可用)→ 该候选诚实退回召回分,basis 标 recall_only。
    决定性:排序平局按 kol_pool_id 升序。
    """
    try:
        from app.domains.kol.video_similarity import _cosine
    except Exception:
        _cosine = None  # type: ignore[assignment]

    ids = [int(c.get("kol_pool_id") or 0) for c in candidates]
    vectors = _kol_dim_vectors(conn, ids) if _cosine is not None else {}
    prototype = _prototype_vector(candidates, vectors)
    max_recall = max((_float(c.get("recall_score")) for c in candidates), default=0.0)
    with_cosine = 0
    for cand in candidates:
        recall_norm = _float(cand.get("recall_score")) / max_recall if max_recall > 0 else 0.0
        kid = int(cand.get("kol_pool_id") or 0)
        cosine_val: float | None = None
        if _cosine is not None and prototype and kid in vectors:
            cosine_val = _cosine(vectors[kid], prototype)
        if cosine_val is not None:
            cand["coarse_score"] = round(W_RECALL * recall_norm + W_DIMS * cosine_val, 6)
            cand["coarse_basis"] = "recall+dims_cosine"
            cand["dims_cosine"] = round(cosine_val, 6)
            with_cosine += 1
        else:
            cand["coarse_score"] = round(W_RECALL * recall_norm, 6)
            cand["coarse_basis"] = "recall_only"
            cand["dims_cosine"] = None
        cand["dims_count"] = len(vectors.get(kid, {}))
    candidates.sort(key=lambda it: (-_float(it.get("coarse_score")), int(it.get("kol_pool_id") or 0)))
    meta = {
        "n": len(candidates),
        "with_dims_cosine": with_cosine,
        "prototype_dims": len(prototype),
        "prototype_top_k": PROTOTYPE_TOP_K,
        "weights": {"recall": W_RECALL, "dims_cosine": W_DIMS},
        "vector_basis": "video_similarity._numeric_vector(final_v1 llm_dimensions_11 十一维口径,KOL 内按维均值)",
    }
    return candidates, meta


# ── 重排段:LLM top-20 + 当日缓存(vkpi_analysis_cache)────────────────


def _read_rerank_cache(conn: Any, query_text: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT result FROM vkpi_analysis_cache
        WHERE target_type=? AND target_id=? AND derive_method=? AND status='ready'
        ORDER BY updated_at DESC, id DESC LIMIT 1
        """,
        (CACHE_TARGET_TYPE, _query_hash(query_text), CACHE_DERIVE_METHOD),
    ).fetchone()
    if not row:
        return None
    result = _loads_json(dict(row).get("result"))
    if not isinstance(result, dict):
        return None
    return result if str(result.get("generated_date") or "") == _today() else None


def _write_rerank_cache(conn: Any, query_text: str, payload: dict[str, Any]) -> None:
    """缓存写失败只记日志不抛(缓存是省钱增益,重排结果照常返回)。"""
    try:
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO vkpi_analysis_cache (
                target_type, target_id, model, derive_method, result, cost, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?::jsonb, 0, 'ready', ?, ?)
            ON CONFLICT (target_type, target_id, derive_method)
            DO UPDATE SET result=EXCLUDED.result, model=EXCLUDED.model,
                status='ready', updated_at=EXCLUDED.updated_at
            """,
            (
                CACHE_TARGET_TYPE,
                _query_hash(query_text),
                "llm_gateway",
                CACHE_DERIVE_METHOD,
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        logger.warning("semantic_recall rerank cache write failed", exc_info=True)


def _parse_rerank_array(text: str) -> list[dict[str, Any]]:
    """LLM 回复 → [{i, s, why}](复用 audience_stats 的截断救援解析;守卫 import)。"""
    try:
        from app.domains.kol.audience_stats import _extract_json_array

        arr = _extract_json_array(text)
        return arr if isinstance(arr, list) else []
    except Exception:
        try:
            start = text.index("[")
            end = text.rindex("]") + 1
            arr = json.loads(text[start:end])
            return arr if isinstance(arr, list) else []
        except Exception:
            return []


def _budget_gate_note() -> str:
    """重排前置预算闸(既有 budget_guard.check_budget;闸不过回原因,过了回空串)。

    口径:kol_recall 池(配置了才认)+ monthly_total 月度总闸。missing 配置按
    check_budget 的 require_configured=False 语义放行(与 profile_recall rerank 一致);
    llm_gateway.invoke 内部还有 provider 级硬闸兜底,双闸不重复记账。
    """
    try:
        from app.domains.costs.budget_guard import check_budget
    except Exception:
        return "budget_guard_unavailable"
    for scope in (RERANK_COST_TAG, "monthly_total"):
        try:
            if not check_budget(scope, 0.0, require_configured=False):
                return f"budget_gate_blocked:{scope}"
        except Exception:
            logger.warning("semantic_recall budget preflight failed scope=%s", scope, exc_info=True)
    return ""


def _invoke_rerank_llm(query_text: str, top_items: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], str]:
    """LLM 打相关分 + 逐人一句为何合适;不可用/解析失败 → ({}, 诚实原因)。"""
    try:
        from app.platform import llm_gateway
    except Exception:
        return {}, "gateway_unavailable"
    lines: list[str] = []
    for i, item in enumerate(top_items):
        # blurb 压到 150 字符/人:thinking 模型思考 token 吃输出预算是常态(final_v1
        # 分镜截断同款教训),输入越省、输出越不容易被 max_tokens 掐头去尾。
        blurb = _clean_text(
            " ".join(
                str(item.get(k) or "")
                for k in ("display_name", "handle", "platform", "profile_type", "type_reason", "profile_text", "bio")
            ),
            150,
        )
        lines.append(f"{i + 1}. {blurb}")
    prompt = (
        "Task: rerank creator candidates for a semantic KOL search and explain fit.\n"
        + "Query: " + _clean_text(query_text, 200)
        + "\nFor EACH numbered candidate output one object"
        + " {\"i\": number, \"s\": relevance 0-100, \"why\": one short sentence (Chinese, why this creator fits the query)}."
        + " Keep each why under 25 Chinese characters."
        + " Output STRICTLY one JSON array, no prose, reply starts with [\n\n"
        + "\n".join(lines)
    )
    try:
        resp = llm_gateway.invoke(
            prompt,
            purpose=RERANK_PURPOSE,
            max_output_tokens=4000,  # gateway 上限;给足输出预算防 thinking 截断(scored=1/20 实测教训)
            cost_tag=RERANK_COST_TAG,
        )
    except Exception as exc:  # noqa: BLE001 - optional rerank must preserve coarse results
        logger.warning("semantic_recall rerank provider failed", exc_info=True)
        return {}, _stable_failure_reason(exc, operation="llm")
    if str(resp.get("model") or "") == "rule_v0" or str(resp.get("status") or "") != "success":
        return {}, _stable_gateway_reason(resp.get("reason") or resp.get("status"))
    parsed = _parse_rerank_array(str(resp.get("text") or ""))
    scores: dict[int, dict[str, Any]] = {}
    for obj in parsed:
        if not isinstance(obj, dict):
            continue
        try:
            idx = int(obj.get("i")) - 1
            score = max(0.0, min(100.0, float(obj.get("s"))))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(top_items):
            kid = int(top_items[idx].get("kol_pool_id") or 0)
            if kid > 0:
                scores[kid] = {"s": round(score, 2), "why": _clean_text(obj.get("why"), 160)}
    if not scores:
        return {}, "parse_failed"
    cost_cents = _float(resp.get("cost_cents"))
    provider = _clean_text(resp.get("provider"), 30)
    return scores, f"provider={provider};cost_cents={round(cost_cents, 4)};scored={len(scores)}"


def _apply_rerank_scores(top_items: list[dict[str, Any]], scores: dict[int, dict[str, Any]]) -> int:
    """把 LLM 分融进 top-20 展示排序(0.7 粗排 + 0.3 LLM;粗排分原值保留供审计)。"""
    applied = 0
    for item in top_items:
        kid = int(item.get("kol_pool_id") or 0)
        entry = scores.get(kid)
        if not entry:
            item["rerank_score"] = round(W_COARSE_IN_RERANK * _float(item.get("coarse_score")), 6)
            continue
        llm_score = _float(entry.get("s"))
        item["llm_relevance"] = round(llm_score, 2)
        why = _clean_text(entry.get("why"), 160)
        if why:
            item["why_fit"] = why
        item["rerank_score"] = round(
            W_COARSE_IN_RERANK * _float(item.get("coarse_score")) + W_LLM * (llm_score / 100.0), 6
        )
        applied += 1
    top_items.sort(key=lambda it: (-_float(it.get("rerank_score")), int(it.get("kol_pool_id") or 0)))
    return applied


def _rerank_stage(conn: Any, query_text: str, ranked: list[dict[str, Any]]) -> dict[str, Any]:
    """重排段总控:当日缓存命中零成本;预算闸/LLM 不可用 → skipped 保粗排序。"""
    top_items = ranked[:RERANK_TOP_N]
    if len(top_items) < 2:
        return {"status": "skipped", "cost_note": "too_few_candidates_zero_cost", "top_n": len(top_items)}

    cached = _read_rerank_cache(conn, query_text)
    if cached:
        raw_scores = cached.get("scores") if isinstance(cached.get("scores"), dict) else {}
        scores = {
            int(k): v for k, v in raw_scores.items()
            if str(k).isdigit() and isinstance(v, dict)
        }
        applied = _apply_rerank_scores(top_items, scores)
        return {
            "status": "cached",
            "cost_note": "same_day_cache_hit_zero_cost",
            "top_n": len(top_items),
            "applied": applied,
        }

    gate_note = _budget_gate_note()
    if gate_note:
        return {"status": "skipped", "cost_note": gate_note, "top_n": len(top_items)}

    scores, note = _invoke_rerank_llm(query_text, top_items)
    if not scores:
        return {"status": "skipped", "cost_note": note, "top_n": len(top_items)}

    applied = _apply_rerank_scores(top_items, scores)
    _write_rerank_cache(
        conn,
        query_text,
        {
            "generated_date": _today(),
            "query_text": _clean_text(query_text, 200),
            "scores": {str(k): v for k, v in sorted(scores.items())},
            "cost_note": note,
        },
    )
    return {"status": "ok", "cost_note": note, "top_n": len(top_items), "applied": applied}


# ── 展示裁剪 ─────────────────────────────────────────────────────────


def _format_item(rank: int, item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rank": rank,
        "kol_pool_id": int(item.get("kol_pool_id") or 0),
        "handle": _clean_text(item.get("handle"), 100),
        "display_name": _clean_text(item.get("display_name"), 100),
        "platform": _clean_text(item.get("platform"), 30),
        "profile_url": _clean_text(item.get("profile_url"), 500),
        "avatar_url": _clean_text(item.get("avatar_url"), 500),
        "followers": _int_or_none(item.get("followers")),
        "country": _clean_text(item.get("country"), 60),
        "bio": _clean_text(item.get("bio"), 200),
        "profile_type": _clean_text(item.get("profile_type"), 20),
        "recall_score": round(_float(item.get("recall_score")), 6),
        "recall_basis": item.get("recall_basis") or "",
        "recall_parts": item.get("recall_parts") or {},
        "coarse_score": round(_float(item.get("coarse_score")), 6),
        "coarse_basis": item.get("coarse_basis") or "",
        "dims_cosine": item.get("dims_cosine"),
        "dims_count": int(item.get("dims_count") or 0),
    }
    if item.get("rerank_score") is not None:
        out["rerank_score"] = round(_float(item.get("rerank_score")), 6)
    if item.get("llm_relevance") is not None:
        out["llm_relevance"] = _float(item.get("llm_relevance"))
    if item.get("why_fit"):
        out["why_fit"] = _clean_text(item.get("why_fit"), 160)
    return out


# ── 主入口 ──────────────────────────────────────────────────────────


def semantic_recall(
    query_text: str,
    *,
    limit: int = DEFAULT_LIMIT,
    conn: Any = None,
    embed_fn: Callable[[str], tuple[list[float], dict[str, Any]]] | None = None,
    provider_free: bool = False,
) -> dict[str, Any]:
    """语义召回三段式:①召回(embedding 优先/决定性降级)②十一维粗排 ③LLM top-20 重排。

    embed_fn 仅供测试打桩;query 为空抛 ValueError(路由转 400)。
    payload 恒带 stages{recall:{method,candidates},coarse:{n},rerank:{status,cost_note}}。
    """
    from app.db.connection import get_conn

    query = _WS_RE.sub(" ", str(query_text or "").strip())
    if not query:
        raise ValueError("query_text is required")
    safe_limit = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
    db = conn or get_conn()

    # ── ① 召回段:embedding 优先,失败决定性降级(method 诚实标注)──
    recall_method = RECALL_METHOD_EMBEDDING
    degraded_reason = ""
    embedding_meta: dict[str, Any] = {}
    excluded_region = 0
    if provider_free:
        recall_method = RECALL_METHOD_FALLBACK
        degraded_reason = "provider_free_preview"
        candidates = _ngram_recall(db, query, RECALL_CANDIDATE_LIMIT)
    else:
        try:
            candidates, embedding_meta = _embedding_recall(db, query, RECALL_CANDIDATE_LIMIT, embed_fn=embed_fn)
        except Exception as exc:  # noqa: BLE001 — 无网/无 key/预算闸/Qdrant 缺,全走降级
            degraded_reason = _stable_failure_reason(exc, operation="embedding")
            logger.warning("semantic_recall embedding degraded reason=%s", degraded_reason, exc_info=True)
            recall_method = RECALL_METHOD_FALLBACK
            candidates = _ngram_recall(db, query, RECALL_CANDIDATE_LIMIT)

    # 地区排除(P0-6 口径:CN/HK/TW;country 为空放行)
    kept: list[dict[str, Any]] = []
    for cand in candidates:
        if _country_excluded(cand.get("country")):
            excluded_region += 1
            continue
        kept.append(cand)
    candidates = kept
    recall_candidates = len(candidates)

    stages: dict[str, Any] = {
        "recall": {
            "method": recall_method,
            "candidates": recall_candidates,
            **({"degraded_reason": degraded_reason} if degraded_reason else {}),
            **({"embedding": embedding_meta} if embedding_meta else {}),
        },
        "coarse": {"n": 0},
        "rerank": {"status": "skipped", "cost_note": "no_candidates_zero_cost"},
    }
    base = {
        "method": PIPELINE_METHOD,
        "query": {"query_text": _clean_text(query, 200), "limit": safe_limit},
        "generated_at": _utcnow(),
    }
    if not candidates:
        return {
            **base,
            "status": "empty",
            "reason": "召回零候选(词表/向量索引均未命中)— 换个更具体的英文描述试试。",
            "items": [],
            "stages": stages,
            "diagnostics": {"excluded_region_count": excluded_region},
        }

    # ── ② 粗排段:十一维余弦(video_similarity 口径)──
    candidates, coarse_meta = _coarse_rank(db, candidates)
    stages["coarse"] = coarse_meta

    # ── ③ 重排段:LLM top-20 + 当日缓存;预算不足诚实 skipped 保粗排序 ──
    if provider_free:
        stages["rerank"] = {
            "status": "skipped",
            "cost_note": "provider_free_preview_zero_cost",
        }
    else:
        stages["rerank"] = _rerank_stage(db, query, candidates)

    items = [_format_item(i + 1, item) for i, item in enumerate(candidates[:safe_limit])]
    return {
        **base,
        "status": "ready",
        "items": items,
        "stages": stages,
        "diagnostics": {
            "excluded_region_count": excluded_region,
            "returned_count": len(items),
            "note": (
                "Provider-free 预览:①本地 3-gram 召回 ②final_v1 十一维余弦粗排 "
                "③跳过 LLM 重排;零外部调用、零缓存写入。"
                if provider_free
                else "三段式语义召回:①embedding 优先/决定性 3-gram 降级 ②final_v1 十一维余弦粗排 "
                "③LLM top-20 重排(当日缓存)。纯读展示信号,不参与 V6 Fit 评分,零触 rule_v0。"
            ),
        },
    }
