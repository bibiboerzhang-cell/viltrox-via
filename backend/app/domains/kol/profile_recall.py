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

PRODUCT_QUERY_TEXTS = {
    "35mm_f12_lab": """Product query profile: Viltrox AF 35mm F1.2 LAB.
Creator use cases: environmental portrait, street photography, documentary storytelling, wedding and engagement photography, low-light portrait, editorial fashion, hybrid photo and video, premium full-frame storyteller.
Desired creator profile: high quality people and scene storytelling, strong portrait or street portfolio, visible lens or camera review credibility, Viltrox or mirrorless lens experience, cinematic natural-light style, premium full-frame audience.""",
    "35mm_f17_air": """Product query profile: Viltrox AF 35mm F1.7 AIR.
Creator use cases: lightweight everyday carry, travel photography, vlog and creator kit, casual portrait, street walkaround, budget APS-C creator, compact Sony Fuji Nikon setup.
Desired creator profile: mobile everyday creator, travel or street shooter, beginner-friendly gear reviewer, light kit advocate, practical budget lens audience, casual hybrid photo and video workflow.""",
}

PRODUCT_SKU_ALIASES = {
    "35MMF12LAB": "35mm_f12_lab",
    "35MMF12": "35mm_f12_lab",
    "AF35MMF12LAB": "35mm_f12_lab",
    "AF35MMF12LABFE": "35mm_f12_lab",
    "AF35MMF12LABZ": "35mm_f12_lab",
    "35MMF17AIR": "35mm_f17_air",
    "35MMF17": "35mm_f17_air",
    "AF35MMF17AIR": "35mm_f17_air",
    "AF35MMF17AIRFE": "35mm_f17_air",
    "AF35MMF17AIRX": "35mm_f17_air",
    "AF35MMF17AIRZ": "35mm_f17_air",
}


@dataclass(frozen=True)
class RecallHit:
    kol_pool_id: int
    vector_score: float
    qdrant_point_id: str


def _normalise_sku(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


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


def resolve_query_text(*, query_text: str = "", product_sku: str = "") -> tuple[str, dict[str, Any]]:
    explicit = str(query_text or "").strip()
    if explicit:
        return explicit, {"query_text_provided": True, "product_sku": str(product_sku or "").strip()}
    raw_sku = str(product_sku or "").strip()
    alias = PRODUCT_SKU_ALIASES.get(_normalise_sku(raw_sku), "")
    if not alias and raw_sku in PRODUCT_QUERY_TEXTS:
        alias = raw_sku
    if not alias:
        raise ValueError("query_text or supported product_sku is required")
    return PRODUCT_QUERY_TEXTS[alias], {"query_text_provided": False, "product_sku": raw_sku, "query_profile": alias}


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
               p.country
        FROM vkpi_kol_profile_index_entries e
        JOIN vkpi_kol_pool p ON p.id = e.kol_pool_id
        WHERE e.collection_name = ?
          AND e.method = ?
          AND e.status = 'ready'
          AND e.profile_type IN ('creator', 'reviewer', 'mixed')
          AND e.kol_pool_id IN ({placeholders})
        """,
        (COLLECTION_NAME, METHOD, *kol_pool_ids),
    ).fetchall()
    return {int(row["kol_pool_id"]): dict(row) for row in rows}


def _clean_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


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
) -> dict[str, Any]:
    type_rank_score = _type_score_for_bucket(row, bucket)
    rank_score = _recall_rank_score(
        vector_score=float(hit.vector_score),
        type_score=type_rank_score,
        vector_weight=vector_weight,
        type_weight=type_weight,
        type_boost_enabled=type_boost_enabled,
    )
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
        "profile_type": row.get("profile_type") or "",
        "bucket": bucket,
        "type_label": _type_label(row),
        "creator_type_score": _float(row.get("creator_type_score")),
        "reviewer_type_score": _float(row.get("reviewer_type_score")),
        "type_reason": row.get("type_reason") or "",
        "type_method": row.get("type_method") or "",
        "recall_reason": _recall_reason(row, evidence),
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
    query_vector, embedding_meta = _embed_query(resolved_text)
    hits = _search_qdrant(query_vector, safe_candidate_limit)

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
    missing_type_count = 0
    excluded_chinese_count = 0
    for hit in ordered_hits:
        row = rows_by_id.get(hit.kol_pool_id)
        if not row:
            missing_type_count += 1
            continue
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
            )
        )

    for bucket_items in buckets.values():
        bucket_items.sort(
            key=lambda item: (
                _float(item.get("recall_rank_score")),
                _float(item.get("vector_score")),
            ),
            reverse=True,
        )

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
            "creator_candidate_count": len(buckets["creator"]),
            "reviewer_candidate_count": len(buckets["reviewer"]),
            "creator_returned": len(selected_creator),
            "reviewer_returned": len(selected_reviewer),
            "returned_count": len(items),
            **embedding_meta,
        },
    }
