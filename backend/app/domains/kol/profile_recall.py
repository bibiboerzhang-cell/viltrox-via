"""KOL profile vector recall, independent from KOL Pool ranking."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import re
from typing import Any

from app.db.connection import get_conn
from app.domains.costs.budget_guard import check_budget, record_cost


COLLECTION_NAME = "vkpi_kol_profile_index_v1"
METHOD = "vector_recall"
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536
PROJECT_ROOT = Path(__file__).resolve().parents[4]
QDRANT_LOCAL_PATH = PROJECT_ROOT / "runtime" / "vkpi_qdrant"
OPENAI_EMBEDDING_PRICE_PER_1M = Decimal("0.02")
MAX_CANDIDATE_LIMIT = 500
LENS_MENTION_RE = re.compile(
    r"\b(?:Viltrox[\s-]*)?(?:AF[\s-]*)?(?:1[356]5|90|85|75|56|55|50|35|28|27|25|24|16)\s*mm"
    r"(?:\s*[fF]/?\s*(?:1\.2|1\.4|1\.7|1\.8|2\.0|2|3\.5|4\.5))?"
    r"(?:\s*(?:Pro|LAB|EVO|AIR|DL|FE|Z|STM|VCM|APO))*\b",
    re.IGNORECASE,
)
PROFILE_REASON_KEYWORDS = (
    ("人像", ("portrait", "portraits", "人像", "model", "fashion", "bokeh")),
    ("街拍", ("street", "街拍", "street photography", "stranger")),
    ("婚礼", ("wedding", "engagement", "婚礼")),
    ("纪实", ("documentary", "storytelling", "cinematic", "film", "filmmaker", "video", "叙事", "电影感")),
    ("测评", ("review", "comparison", "test", "unboxing", "评测", "对比")),
    ("旅行", ("travel", "landscape", "city", "城市", "风光")),
)

# ── 召回展示辅助信号(why-fit 规则桥 + 视频/平面判据 + 新人优先展示降权):整簇搬到 sibling 模块
#    profile_recall_relevance.py(行为不变 move+re-export,函数体逐字不变)。re-export 在 _clean_text
#    定义之后(本文件下方)做,以打破 sibling 顶层 import _clean_text 的循环;下游照旧从本模块引用。

# ── 产品线 query 模板 / persona / SKU 别名 / query-text 解析:整体搬到 sibling 模块
#    profile_recall_product_queries.py(行为不变 move+re-export)。下游照旧从本模块引用。
from app.domains.kol.profile_recall_product_queries import (  # noqa: E402
    PRODUCT_LINE_PERSONAS,
    PRODUCT_QUERY_TEXTS,
    PRODUCT_SKU_ALIASES,
    _normalise_sku,
    resolve_query_text,
)

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class RecallHit:
    kol_pool_id: int
    vector_score: float
    qdrant_point_id: str


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


def _openai_client():
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"openai_sdk_unavailable: {exc}") from exc

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("missing_openai_api_key")
    # api.openai.com 在本网络下不可直连(SSL 握手超时,区域封锁);走与 yt-dlp 同一残留代理
    # (YTDLP_PROXY / OPENAI_PROXY)可达(实测直连 ConnectTimeout、走代理 HTTP200 2.9s)。
    proxy = (os.getenv("OPENAI_PROXY") or os.getenv("YTDLP_PROXY") or "").strip()
    if proxy:
        try:
            import httpx

            return OpenAI(api_key=api_key, http_client=httpx.Client(proxy=proxy, timeout=30.0))
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
    return OpenAI(api_key=api_key)


def _embed_query(query_text: str) -> tuple[list[float], dict[str, Any]]:
    # 护栏③ enforce(诊断 C-3③):embedding 调用前硬闸 + 调用后记账(此前裸奔零护栏零记账)。
    if not check_budget("provider:openai", 0.0, require_configured=True):
        raise RuntimeError("embedding_budget_exceeded")
    client = _openai_client()
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=[query_text])
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
    }


def _search_qdrant(query_vector: list[float], candidate_limit: int) -> list[RecallHit]:
    from qdrant_client.http import models as qdrant_models

    client = _qdrant_client()
    if not client.collection_exists(COLLECTION_NAME):
        raise RuntimeError(f"qdrant_collection_missing:{COLLECTION_NAME}")
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


def _entry_rows(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not kol_pool_ids:
        return {}
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    rows = get_conn().execute(
        f"""
        SELECT e.kol_pool_id,
               e.profile_type,
               e.creator_type_score,
               e.reviewer_type_score,
               e.type_reason,
               e.type_method,
               e.sufficiency,
               e.profile_text,
               p.handle,
               p.display_name,
               p.platform,
               p.profile_url,
               p.avatar_url,
               p.followers,
               p.bio,
               p.country,
               p.primary_topic,
               p.content_style,
               p.secondary_topics_json,
               p.brand_collaborations_json
        FROM vkpi_kol_profile_index_entries e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.collection_name = ?
          AND e.method = ?
          AND e.status = 'ready'
          AND e.profile_type IN ('creator', 'reviewer', 'mixed')
          AND p.duplicate_of_id IS NULL
          AND e.kol_pool_id IN ({placeholders})
        """,
        (COLLECTION_NAME, METHOD, *kol_pool_ids),
    ).fetchall()
    return {int(row["kol_pool_id"]): dict(row) for row in rows}


def _pool_rows_fallback(kol_pool_ids: list[int]) -> dict[int, dict]:
    """硬兜底(2026-07-02):索引表缺行(新 KOL 未入索引/表漂移)时按 pool 行合成可展示候选。

    背景:此前索引行缺失的命中被整批丢弃,索引表一旦空表整个文本搜索静默归零(本日事故)。
    合成行 profile_type 留空 -> 展示为「未分类」,type 分 0,profile_text 用 bio 顶,绝不触碰 fit。
    """
    if not kol_pool_ids:
        return {}
    placeholders = ", ".join(["?"] * len(kol_pool_ids))
    conn = get_conn()
    rows = conn.execute(
        f"""
        SELECT p.id AS kol_pool_id, p.platform, p.handle, p.display_name, p.profile_url,
               p.avatar_url, p.followers, p.bio, p.country, p.primary_topic, p.content_style,
               p.secondary_topics_json, p.brand_collaborations_json
        FROM vkpi_kol_pool p
        WHERE p.duplicate_of_id IS NULL AND p.id IN ({placeholders})
        """,
        tuple(int(x) for x in kol_pool_ids),
    ).fetchall()
    out: dict[int, dict] = {}
    for row in rows:
        d = dict(row)
        d.setdefault("profile_type", "")
        d.setdefault("profile_text", str(d.get("bio") or "")[:600])
        d.setdefault("type_reason", "索引未覆盖,按池内资料兜底展示")
        d.setdefault("creator_type_score", 0.0)
        d.setdefault("reviewer_type_score", 0.0)
        out[int(d["kol_pool_id"])] = d
    return out


def _pool_text_fallback_hits(query_text: str, candidate_limit: int) -> list[RecallHit]:
    """召回永不零红线兜底(记忆 vkpi-text-search-resurrection「recall 加 pool 兜底永不零结果」)。

    向量召回不可用时——本地内嵌 Qdrant 单实例文件锁被并发召回撞锁(实测 6 并发下有请求返
    'Storage folder ... already accessed by another instance')、库缺失、embedding 失败/预算
    超限,或该 query 向量库零命中——按 pool 文本(handle/display_name/bio/primary_topic/
    content_style)直接召回可展示候选,保证并发/降级下召回不返 0。

    兜底 hit vector_score=0(排在真向量命中之后),进与向量命中同一展示管线(索引缺行由
    _pool_rows_fallback 合成);绝不触 viltrox_fit_score。文本无命中(空 query / 生僻词)时退到
    池内头部行,红线永不零。
    """
    limit = max(1, min(MAX_CANDIDATE_LIMIT, int(candidate_limit or 50)))
    conn = get_conn()
    hits: list[RecallHit] = []
    seen: set[int] = set()

    def _collect(rows: list[Any]) -> None:
        for row in rows:
            try:
                kid = int(dict(row).get("kol_pool_id") or 0)
            except (TypeError, ValueError):
                kid = 0
            if kid > 0 and kid not in seen:
                seen.add(kid)
                hits.append(RecallHit(kol_pool_id=kid, vector_score=0.0, qdrant_point_id="pool_text_fallback"))

    text = " ".join(str(query_text or "").split()).strip()
    if text:
        like = f"%{text}%"
        _collect(
            conn.execute(
                """
                SELECT p.id AS kol_pool_id
                FROM vkpi_kol_pool p
                WHERE p.duplicate_of_id IS NULL
                  AND (
                        LOWER(COALESCE(p.handle, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.display_name, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.bio, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.primary_topic, '')) LIKE LOWER(?)
                     OR LOWER(COALESCE(p.content_style, '')) LIKE LOWER(?)
                  )
                ORDER BY COALESCE(p.followers, 0) DESC, p.id DESC
                LIMIT ?
                """,
                (like, like, like, like, like, limit),
            ).fetchall()
        )
    if hits:
        return hits
    _collect(
        conn.execute(
            """
            SELECT p.id AS kol_pool_id
            FROM vkpi_kol_pool p
            WHERE p.duplicate_of_id IS NULL
            ORDER BY COALESCE(p.followers, 0) DESC, p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    )
    return hits


def _adoption_profile() -> dict:
    """采纳回流(2026-07-02 用户令):收藏/入项目 KOL 的 platform 与主题词分布。
    行数极小(收藏12+分配5级别),每次召回一小查;采纳样本 <5 不学,防两三条记录带偏。"""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT p.platform, p.primary_topic, p.bio
            FROM vkpi_kol_pool p
            WHERE p.id IN (SELECT kol_pool_id FROM vkpi_kol_pool_favorites)
               OR p.id IN (SELECT kol_pool_id FROM vkpi_project_kol_assignments)
            """
        ).fetchall()
    except Exception:
        logger.debug("偏好画像读取失败,返回空画像(best-effort)", exc_info=True)
        return {}
    platforms: dict[str, int] = {}
    topic_words: dict[str, int] = {}
    n = 0
    for r in rows:
        d = dict(r)
        n += 1
        p = str(d.get("platform") or "").lower()
        if p:
            platforms[p] = platforms.get(p, 0) + 1
        blob = f"{d.get('primary_topic') or ''} {d.get('bio') or ''}".lower()
        for w in re.findall(r"[a-z]{4,}", blob)[:20]:
            topic_words[w] = topic_words.get(w, 0) + 1
    if n < 5:
        return {}
    top_words = {w for w, c in sorted(topic_words.items(), key=lambda kv: -kv[1])[:15] if c >= 2}
    return {"n": n, "platforms": platforms, "top_words": top_words}


def _adoption_boost_for(item: dict[str, Any], profile: dict) -> float:
    """展示层小幅上浮:平台份额 0.02 + 采纳主题词命中候选 bio/why_fit 0.03,封顶 0.05。"""
    n = int(profile.get("n") or 0)
    if not n:
        return 0.0
    boost = 0.0
    p = str(item.get("platform") or "").lower()
    if p and profile.get("platforms", {}).get(p):
        boost += 0.02 * (profile["platforms"][p] / n)
    blob = f"{item.get('bio') or ''} {item.get('why_fit') or ''}".lower()
    if any(w in blob for w in profile.get("top_words", set())):
        boost += 0.03
    return round(min(0.05, boost), 4)


def _llm_rerank_buckets(buckets: dict[str, list], query_text: str, persona_text: str, product_label: str) -> str:
    """头部候选 LLM 相关性重排(两桶各 top12 合一次 flash 调用,0.15 权重折进 display 分)。
    任何失败静默跳过(返回诊断短语);红线:绝不触 vector/recall/fit 分。"""
    try:
        from app.platform import llm_gateway
    except Exception as exc:
        return f"gateway_unavailable: {exc}"[:80]
    cands: list[dict[str, Any]] = []
    for bucket in ("creator", "reviewer"):
        cands.extend((buckets.get(bucket) or [])[:12])
    if len(cands) < 4:
        return "too_few_candidates"
    lines = []
    for i, it in enumerate(cands):
        blurb = " ".join(
            str(x) for x in (it.get("why_fit") or "", it.get("recall_reason") or "", it.get("bio") or "")
        ).replace("\n", " ")[:200]
        lines.append(f"{i + 1}. handle={str(it.get('handle'))[:30]} :: {blurb}")
    prompt = (
        "Task: rerank creator candidates for a marketing search.\nQuery: " + str(query_text)[:200]
        + ("\nTarget persona: " + str(persona_text)[:200] if persona_text else "")
        + ("\nProduct: " + str(product_label)[:80] if product_label else "")
        + "\nFor EACH numbered candidate output one object {\"i\": number, \"s\": relevance 0-100}."
        + " Output STRICTLY one JSON array, no prose, reply starts with [\n\n"
        + "\n".join(lines)
    )
    resp = llm_gateway.invoke(
        prompt,
        purpose="vkpi_recall_rerank_v1",
        preferred_provider="google",
        max_output_tokens=3000,
        cost_tag="kol_recall",
    )
    if str(resp.get("model") or "") == "rule_v0" or str(resp.get("status") or "") != "success":
        return "llm_unusable"
    text = str(resp.get("text") or "")
    # 截断救援:thinking 模型思考 token 吃掉输出预算是常态,复用 audience_stats 的
    # 残缺数组抢救解析(抢救到多少算多少,绝不编造)。
    try:
        from app.domains.kol.audience_stats import _extract_json_array

        arr = _extract_json_array(text)
    except Exception:
        arr = []
    if not arr:
        return "parse_failed"
    hits = 0
    for obj in arr if isinstance(arr, list) else []:
        try:
            idx = int(obj.get("i")) - 1
            score = max(0.0, min(100.0, float(obj.get("s"))))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= idx < len(cands):
            cands[idx]["display_rank_score"] = round(_float(cands[idx].get("display_rank_score")) + 0.15 * (score / 100.0), 6)
            cands[idx]["llm_rerank_score"] = score
            hits += 1
    return f"ok:{hits}"


def _clean_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


# ── re-export 召回展示辅助信号簇(行为不变 move):放在 _clean_text 定义之后,
#    因为 sibling 顶层 `from app.domains.kol.profile_recall import _clean_text` 会回看本模块;
#    此时 _clean_text 已绑定,循环被打破。下游(及私有下划线名)照旧从本模块引用。
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


# P0-6 地区口径:排除 {中国大陆 CN / 香港 HK / 台湾 TW} 三地区,其余所有国家放行
# (含海外中文博主)。同时匹配中文地名与 ISO 码;country 为空不命中(放行,预期)。
_EXCLUDED_REGION_RE = re.compile(
    r"中国|中國|大陆|大陸|香港|台湾|台灣|hong\s*kong|taiwan|china", re.IGNORECASE
)
_EXCLUDED_REGION_CODES = {"CN", "HK", "TW", "CHINA"}


def _country_in_excluded_region(value: Any) -> bool:
    """地区排除判据(P0-6 取代旧大陆/汉字双判据):country 命中 {CN/HK/TW} 之一即排除,
    同时匹配中文地名(中国/中國/大陆/大陸/香港/台湾/台灣/Hong Kong/Taiwan/China)与 ISO 码
    (CN/HK/TW/CHINA)。country 为空 → 不命中 → 放行(海外中文博主一律放行)。"""
    text = str(value or "").strip()
    if not text:
        return False
    if text.upper() in _EXCLUDED_REGION_CODES:
        return True
    return bool(_EXCLUDED_REGION_RE.search(text))


# P0-6 移除:旧汉字判据 `_looks_chinese` 已从过滤链下线(误杀三地区外日韩/海外中文号),
# 排除一律走 `_country_in_excluded_region` 地区判据。函数整体删除,不再保留死代码。


def _normalise_lens_mention(value: str) -> str:
    text = _clean_text(value, 80)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\b[Ff]\s*/?\s*", "F", text)
    text = re.sub(r"\b(viltrox)\b", "Viltrox", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(af)\b", "AF", text, flags=re.IGNORECASE)
    for word in ("pro", "lab", "evo", "air", "dl", "fe", "stm", "vcm", "apo"):
        text = re.sub(rf"\b{word}\b", word.upper() if word in {"lab", "evo", "air", "dl", "fe", "stm", "vcm", "apo"} else "Pro", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d)\s*mm", r"\1mm", text, flags=re.IGNORECASE)
    if "viltrox" not in text.lower():
        text = f"Viltrox {text}"
    return text


def _extract_lenses(*texts: Any, limit: int = 3) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        blob = _clean_text(raw, 1200)
        if not blob:
            continue
        for match in LENS_MENTION_RE.finditer(blob):
            start = max(0, match.start() - 50)
            end = min(len(blob), match.end() + 50)
            context = blob[start:end].lower()
            phrase = match.group(0)
            if "viltrox" not in context and not phrase.lower().strip().startswith("af"):
                continue
            normalised = _normalise_lens_mention(phrase)
            key = re.sub(r"[^a-z0-9]+", "", normalised.lower())
            if key in seen:
                continue
            seen.add(key)
            found.append(normalised)
            if len(found) >= limit:
                return found
    return found


def _reason_labels(*texts: Any, limit: int = 2) -> list[str]:
    blob = " ".join(_clean_text(text, 1200).lower() for text in texts if text)
    labels: list[str] = []
    for label, keywords in PROFILE_REASON_KEYWORDS:
        if any(keyword in blob for keyword in keywords):
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def _evidence_score(row: dict[str, Any]) -> tuple[int, int, int, int]:
    title = str(row.get("title") or "")
    product_blob = " ".join(str(row.get(key) or "") for key in ("product_presence", "brand_exposure"))
    blob = f"{title} {product_blob}".lower()
    lens_bonus = 1 if LENS_MENTION_RE.search(blob) else 0
    viltrox_bonus = 1 if "viltrox" in blob else 0
    thumbnail_bonus = 1 if _clean_text(row.get("thumbnail_url"), 500) else 0
    try:
        views = int(row.get("view_count") or 0)
    except (TypeError, ValueError):
        views = 0
    return lens_bonus, viltrox_bonus, thumbnail_bonus, views


def _evidence_summaries(kol_pool_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not kol_pool_ids:
        return {}
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    rows = get_conn().execute(
        f"""
        SELECT e.kol_pool_id,
               COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
               e.content_url,
               e.thumbnail_url,
               e.view_count,
               e.like_count,
               c.result #>> '{{layer1_visual_content,content_summary}}' AS content_summary,
               c.result #>> '{{layer1_visual_content,product_presence}}' AS product_presence,
               c.result #>> '{{layer1_visual_content,brand_exposure}}' AS brand_exposure
        FROM vkpi_kol_video_evidence e
        LEFT JOIN vkpi_analysis_cache c
          ON c.target_type = 'video'
         AND c.target_id = e.id::text
         AND c.derive_method = 'video_analysis_final_v1'
         AND c.status = 'ready'
        WHERE e.kol_pool_id IN ({placeholders})
          AND e.is_active IS NOT FALSE
        ORDER BY e.kol_pool_id, e.posted_at DESC NULLS LAST, e.id DESC
        """,
        tuple(kol_pool_ids),
    ).fetchall()
    by_id: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        by_id.setdefault(int(item["kol_pool_id"]), []).append(item)

    summaries: dict[int, dict[str, Any]] = {}
    for kol_id, items in by_id.items():
        ranked = sorted(items, key=_evidence_score, reverse=True)
        representative: list[dict[str, Any]] = []
        for item in ranked:
            title = _clean_text(item.get("title"), 220)
            url = _clean_text(item.get("content_url"), 500)
            if not title and not url:
                continue
            representative.append(
                {
                    "title": title or url,
                    "content_url": url,
                    "thumbnail_url": _clean_text(item.get("thumbnail_url"), 500),
                    "view_count": item.get("view_count"),
                    "like_count": item.get("like_count"),
                }
            )
            if len(representative) >= 3:
                break

        texts: list[str] = []
        for item in ranked[:6]:
            texts.extend(
                [
                    _clean_text(item.get("title"), 500),
                    _clean_text(item.get("product_presence"), 500),
                    _clean_text(item.get("brand_exposure"), 500),
                ]
            )
        summaries[kol_id] = {
            "representative_evidence": representative,
            "used_lenses": _extract_lenses(*texts),
            "reason_labels": _reason_labels(
                *(texts + [_clean_text(item.get("content_summary"), 500) for item in ranked[:3]])
            ),
        }
    return summaries


def _persona_text_for_query(query_meta: dict[str, Any], product_focus: Any, target_persona: Any) -> str:
    """拼出"本次 query 面向的人群"文本(供 why-fit 人群侧关键词匹配)。
    优先级:planner 的 product_focus/target_persona + 产品线 persona/label + 原始 query_text。
    纯展示用文本,绝不参与评分。"""
    parts: list[str] = []
    profile_key = str((query_meta or {}).get("query_profile") or "")
    persona_meta = PRODUCT_LINE_PERSONAS.get(profile_key) or {}
    if persona_meta:
        parts.append(str(persona_meta.get("persona") or ""))
        parts.append(str(persona_meta.get("label") or ""))
    if isinstance(product_focus, (list, tuple)):
        parts.extend(str(item) for item in product_focus if item)
    elif product_focus:
        parts.append(str(product_focus))
    if target_persona:
        parts.append(str(target_persona))
    if (query_meta or {}).get("query_text_provided"):
        parts.append(str((query_meta or {}).get("query_text") or ""))
    return _clean_text(" ".join(p for p in parts if p), 600).lower()


def _why_fit(
    row: dict[str, Any],
    evidence: dict[str, Any],
    persona_text: str,
    product_label: str,
) -> str:
    """一句话「为何这个人适合本次产品/人群」:把 ① 本次 query 人群 与 ② KOL 真实信号做规则匹配。
    命中即拼可读理由(无 LLM 也有理由);纯展示文本,绝不写/影响 viltrox_fit_score。"""
    persona_blob = str(persona_text or "")
    # KOL 真实信号 blob:画像文本 / 类型理由 / 已用器材 / 垂类标签 / bio。
    kol_blob = " ".join(
        _clean_text(value, 600).lower()
        for value in (
            row.get("profile_text"),
            row.get("type_reason"),
            row.get("bio"),
            " ".join(str(lens) for lens in (evidence.get("used_lenses") or [])),
            " ".join(str(label) for label in (evidence.get("reason_labels") or [])),
        )
        if value
    )
    matched: list[str] = []
    seen: set[str] = set()
    for _persona_key, persona_words, kol_words, phrase in WHY_FIT_RULES:
        if phrase in seen:
            continue
        persona_hit = (not persona_blob) or any(word.lower() in persona_blob for word in persona_words)
        kol_hit = any(word.lower() in kol_blob for word in kol_words)
        if persona_hit and kol_hit:
            matched.append(phrase)
            seen.add(phrase)
        if len(matched) >= 2:
            break
    target = str(product_label or "").strip()
    if matched:
        signal = " + ".join(matched)
        return f"{signal} → 适合{target}" if target else f"适合理由:{signal}"
    # 无规则命中时的可读兜底:用画像标签说人话,不假装精准匹配。
    fallback_labels = list(evidence.get("reason_labels") or []) or _reason_labels(
        row.get("profile_text"), row.get("type_reason")
    )
    label_text = "/".join(fallback_labels[:2]) if fallback_labels else "内容画像"
    return f"{label_text}画像与{target}人群相近" if target else f"{label_text}画像与产品人群相近"


def _recall_reason(row: dict[str, Any], evidence: dict[str, Any]) -> str:
    lenses = list(evidence.get("used_lenses") or [])
    reason_labels = list(evidence.get("reason_labels") or [])
    if not reason_labels:
        reason_labels = _reason_labels(row.get("profile_text"), row.get("type_reason"))
    profile_part = "/".join(reason_labels[:2]) if reason_labels else "索引画像"
    if lenses:
        return f"作品证据:{lenses[0]}相关内容 + {profile_part}画像匹配"
    representative = list(evidence.get("representative_evidence") or [])
    if representative:
        title = _clean_text(representative[0].get("title"), 42)
        return f"作品证据:{title} + {profile_part}画像匹配"
    return f"画像匹配:{profile_part}内容画像与产品 query 相近"


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bucket_for(row: dict[str, Any], mixed_policy: str) -> str:
    profile_type = str(row.get("profile_type") or "").strip().lower()
    if profile_type == "creator":
        return "creator"
    if profile_type == "reviewer":
        return "reviewer"
    if profile_type == "mixed" and mixed_policy == "dominant":
        return "creator" if _float(row.get("creator_type_score")) >= _float(row.get("reviewer_type_score")) else "reviewer"
    return "reviewer"


def _type_label(row: dict[str, Any]) -> str:
    profile_type = str(row.get("profile_type") or "").strip().lower()
    if profile_type == "mixed":
        return "双修"
    if profile_type == "creator":
        return "创作者"
    if profile_type == "reviewer":
        return "测评号"
    return "未分类"


def _type_score_for_bucket(row: dict[str, Any], bucket: str) -> float:
    if bucket == "creator":
        return _float(row.get("creator_type_score"))
    return _float(row.get("reviewer_type_score"))


def _recall_rank_score(
    *,
    vector_score: float,
    type_score: float,
    vector_weight: float,
    type_weight: float,
    type_boost_enabled: bool,
) -> float:
    if not type_boost_enabled:
        return float(vector_score)
    return float(vector_score) * float(vector_weight) + (float(type_score) / 100.0) * float(type_weight)


def _format_item(
    hit: RecallHit,
    row: dict[str, Any],
    bucket: str,
    *,
    vector_weight: float,
    type_weight: float,
    type_boost_enabled: bool,
    evidence: dict[str, Any],
    persona_text: str = "",
    product_label: str = "",
    video_leaning: bool = False,
) -> dict[str, Any]:
    type_rank_score = _type_score_for_bucket(row, bucket)
    rank_score = _recall_rank_score(
        vector_score=float(hit.vector_score),
        type_score=type_rank_score,
        vector_weight=vector_weight,
        type_weight=type_weight,
        type_boost_enabled=type_boost_enabled,
    )
    # 独立展示信号(绝不并入 viltrox_fit_score / recall_rank_score / vector_score / rule_v0)。
    relevance_adjust, relevance_flags, relevance_notes, tier_hint = _relevance_signals(
        row, evidence, video_leaning=video_leaning
    )
    why_fit_text = _why_fit(row, evidence, persona_text, product_label)
    if relevance_notes:
        why_fit_text = f"{why_fit_text}({';'.join(relevance_notes)})"
    item = {
        "kol_pool_id": int(row.get("kol_pool_id") or hit.kol_pool_id),
        "handle": row.get("handle") or "",
        "display_name": row.get("display_name") or "",
        "platform": row.get("platform") or "",
        "profile_url": row.get("profile_url") or "",
        "avatar_url": row.get("avatar_url") or "",
        "followers": row.get("followers"),
        "bio": row.get("bio") or "",
        "vector_score": round(float(hit.vector_score), 6),
        "type_rank_score": round(type_rank_score, 1),
        "recall_rank_score": round(rank_score, 6),
        "recall_rank_score_method": "vector_type_weighted" if type_boost_enabled else "vector_only",
        # 独立展示分:= recall_rank_score + 展示用 adjust;仅供前端展示排序/分档,
        # 绝不回写任何评分字段(recall_rank_score 原值不变,供审计)。
        "display_rank_score": round(rank_score + relevance_adjust, 6),
        "display_relevance_adjust": round(relevance_adjust, 6),
        "relevance_flags": relevance_flags,
        "relevance_tier_hint": tier_hint,
        "profile_type": row.get("profile_type") or "",
        "bucket": bucket,
        "type_label": _type_label(row),
        "creator_type_score": _float(row.get("creator_type_score")),
        "reviewer_type_score": _float(row.get("reviewer_type_score")),
        "type_reason": row.get("type_reason") or "",
        "type_method": row.get("type_method") or "",
        "recall_reason": _recall_reason(row, evidence),
        "why_fit": why_fit_text,
        "source_fields": {
            "vector_method": METHOD,
            "type_method": row.get("type_method") or "",
            "qdrant_point_id": hit.qdrant_point_id,
            "sufficiency": row.get("sufficiency") or "",
            "ranking_method": "vector_type_weighted" if type_boost_enabled else "vector_only",
        },
    }
    representative = list(evidence.get("representative_evidence") or [])
    if representative:
        item["representative_evidence"] = representative
    used_lenses = list(evidence.get("used_lenses") or [])
    if used_lenses:
        item["used_lenses"] = used_lenses
        item["used_lenses_note"] = "从作品标题和视频分析中提取的镜头提及,不是确证拥有"
    return item


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
) -> dict[str, Any]:
    if ratio_policy != "soft":
        raise ValueError("only ratio_policy=soft is supported")
    if mixed_policy != "dominant":
        raise ValueError("only mixed_policy=dominant is supported")

    safe_candidate_limit = max(1, min(MAX_CANDIDATE_LIMIT, int(candidate_limit or 50)))
    safe_limit = max(1, min(50, int(limit or 10)))
    safe_creator_quota = max(0, min(50, int(creator_quota or 0)))
    safe_reviewer_quota = max(0, min(50, int(reviewer_quota or 0)))
    safe_vector_weight = max(0.0, min(1.0, _float(vector_weight)))
    safe_type_weight = max(0.0, min(1.0, _float(type_weight)))
    if safe_creator_quota + safe_reviewer_quota <= 0:
        raise ValueError("creator_quota + reviewer_quota must be greater than 0")
    if type_boost_enabled and safe_vector_weight + safe_type_weight <= 0:
        raise ValueError("vector_weight + type_weight must be greater than 0 when type_boost_enabled=true")

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
    # 优雅降级(稳定优先):Qdrant 向量库缺失 / embedding 失败 / 预算超限 → 返回空召回而非 503。
    # 智能搜索仍可继续走线上发现等路径;不再因向量库不可用整条链崩。
    recall_degraded = ""
    try:
        query_vector, embedding_meta = _embed_query(resolved_text)
        hits = _search_qdrant(query_vector, safe_candidate_limit)
    except Exception as exc:  # noqa: BLE001 — 召回不可用时降级,不让 500/503 冒泡
        recall_degraded = (str(exc).split(":", 1)[0] or "recall_unavailable")[:80]
        try:
            from app.core.logging import get_logger
            get_logger(__name__).warning("recall_degraded", extra={"reason": str(exc)[:160]})
        except Exception:
            logger.warning("suppressed exception (hardening: was silent)", exc_info=True)
            pass
        query_vector, embedding_meta, hits = [], {}, []

    # 红线「召回永不零」(记忆 vkpi-text-search-resurrection):向量命中为空——无论是并发撞
    # Qdrant 文件锁 / 库缺失 / embedding 失败 / 预算超限(recall_degraded 已置),还是该 query
    # 向量库零命中——都回退到 pool 文本兜底,确保并发/降级下召回不返 0。兜底候选进同一展示管线。
    pool_text_fallback_count = 0
    if not hits:
        hits = _pool_text_fallback_hits(resolved_text, safe_candidate_limit)
        pool_text_fallback_count = len(hits)

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
    buckets: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": []}
    fallback_rows = _pool_rows_fallback([h.kol_pool_id for h in ordered_hits if h.kol_pool_id not in rows_by_id])
    fallback_used_count = 0
    missing_type_count = 0
    excluded_chinese_count = 0
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
        bucket = _bucket_for(row, mixed_policy)
        buckets[bucket].append(
            _format_item(
                hit,
                row,
                bucket,
                vector_weight=safe_vector_weight,
                type_weight=safe_type_weight,
                type_boost_enabled=bool(type_boost_enabled),
                evidence=evidence_by_id.get(hit.kol_pool_id, {}),
                persona_text=persona_text,
                product_label=product_label,
                video_leaning=video_leaning,
            )
        )

    for bucket_items in buckets.values():
        # 展示排序用独立的 display_rank_score(= recall_rank_score + 展示 adjust);
        # recall_rank_score / vector_score 原值保留不变,仅作 tie-break 与审计。
        bucket_items.sort(
            key=lambda item: (
                _float(item.get("display_rank_score")),
                _float(item.get("recall_rank_score")),
                _float(item.get("vector_score")),
            ),
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
        if os.environ.get("RECALL_LLM_RERANK_ENABLED", "1").strip().lower() not in {"0", "false", "no"}:
            _rerank_note = _llm_rerank_buckets(buckets, resolved_text, persona_text, product_label)
        if _boosted or _rerank_note.startswith("ok"):
            for _bucket_items in buckets.values():
                _bucket_items.sort(
                    key=lambda item: (
                        _float(item.get("display_rank_score")),
                        _float(item.get("recall_rank_score")),
                        _float(item.get("vector_score")),
                    ),
                    reverse=True,
                )
        _rerank_note = (_rerank_note or "off") + f" boost:{_boosted}"
    except Exception as _rr_exc:
        _rerank_note = f"stage_skipped: {str(_rr_exc)[:80]}"

    creator_take = min(safe_creator_quota, safe_limit)
    reviewer_take = min(safe_reviewer_quota, max(0, safe_limit - creator_take))
    selected_creator = buckets["creator"][:creator_take]
    selected_reviewer = buckets["reviewer"][:reviewer_take]
    items = [*selected_creator, *selected_reviewer]

    return {
        "method": METHOD,
        "query": {
            **query_meta,
            "query_text": resolved_text,
            "collection_name": COLLECTION_NAME,
            "candidate_limit": safe_candidate_limit,
            "limit": safe_limit,
            "product_label": product_label,
            "product_persona": str(persona_meta.get("persona") or ""),
            "video_leaning_product": bool(video_leaning),
        },
        "ratio": {
            "creator_quota": safe_creator_quota,
            "reviewer_quota": safe_reviewer_quota,
            "policy": ratio_policy,
            "mixed_policy": mixed_policy,
            "dedupe": bool(dedupe),
        },
        "ranking": {
            "type_boost_enabled": bool(type_boost_enabled),
            "vector_weight": safe_vector_weight,
            "type_weight": safe_type_weight,
            "score_formula": (
                "vector_score * vector_weight + (type_rank_score / 100) * type_weight"
                if type_boost_enabled
                else "vector_score"
            ),
        },
        "items": items,
        "buckets": {
            "creator": selected_creator,
            "reviewer": selected_reviewer,
        },
        "diagnostics": {
            "candidate_count": len(hits),
            "deduped_candidate_count": len(ordered_hits),
            "duplicate_count": duplicate_count,
            "typed_candidate_count": len(buckets["creator"]) + len(buckets["reviewer"]),
            "missing_type_count": missing_type_count,
            "fallback_pool_rows": fallback_used_count,
            "pool_text_fallback_count": pool_text_fallback_count,
            "display_rerank": _rerank_note,
            "creator_candidate_count": len(buckets["creator"]),
            "reviewer_candidate_count": len(buckets["reviewer"]),
            "creator_returned": len(selected_creator),
            "reviewer_returned": len(selected_reviewer),
            "returned_count": len(items),
            "recall_degraded": recall_degraded,
            **embedding_meta,
        },
    }
