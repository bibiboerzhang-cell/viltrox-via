"""KOL profile vector recall, independent from KOL Pool ranking."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
from typing import Any

from app.db.connection import get_conn


COLLECTION_NAME = "vkpi_kol_profile_index_v1"
METHOD = "vector_recall"
EMBEDDING_MODEL = "text-embedding-3-small"
VECTOR_SIZE = 1536
PROJECT_ROOT = Path(__file__).resolve().parents[4]
QDRANT_LOCAL_PATH = PROJECT_ROOT / "runtime" / "vkpi_qdrant"
OPENAI_EMBEDDING_PRICE_PER_1M = Decimal("0.02")
MAX_CANDIDATE_LIMIT = 500

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
    return vector, {
        "embedding_model": EMBEDDING_MODEL,
        "query_embedding_tokens": tokens,
        "query_embedding_cost_usd_estimate": float(_cost_for_tokens(tokens)),
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
               p.handle,
               p.display_name,
               p.platform,
               p.profile_url,
               p.avatar_url
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
) -> dict[str, Any]:
    type_rank_score = _type_score_for_bucket(row, bucket)
    rank_score = _recall_rank_score(
        vector_score=float(hit.vector_score),
        type_score=type_rank_score,
        vector_weight=vector_weight,
        type_weight=type_weight,
        type_boost_enabled=type_boost_enabled,
    )
    return {
        "kol_pool_id": int(row.get("kol_pool_id") or hit.kol_pool_id),
        "handle": row.get("handle") or "",
        "display_name": row.get("display_name") or "",
        "platform": row.get("platform") or "",
        "profile_url": row.get("profile_url") or "",
        "avatar_url": row.get("avatar_url") or "",
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
        "source_fields": {
            "vector_method": METHOD,
            "type_method": row.get("type_method") or "",
            "qdrant_point_id": hit.qdrant_point_id,
            "sufficiency": row.get("sufficiency") or "",
            "ranking_method": "vector_type_weighted" if type_boost_enabled else "vector_only",
        },
    }


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
    vector_weight: float = 0.7,
    type_weight: float = 0.3,
    type_boost_enabled: bool = True,
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
    buckets: dict[str, list[dict[str, Any]]] = {"creator": [], "reviewer": []}
    missing_type_count = 0
    for hit in ordered_hits:
        row = rows_by_id.get(hit.kol_pool_id)
        if not row:
            missing_type_count += 1
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
