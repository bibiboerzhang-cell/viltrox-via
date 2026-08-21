"""Mechanical helper implementations for :mod:`profile_recall`.

The public compatibility wrappers remain in ``profile_recall`` so existing
imports and tests can monkeypatch its connection, transport, and ranking
seams.  This module only holds the extracted implementation bodies.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

from app.core.logging import get_logger
from app.domains.kol import profile_recall_projection as _projection
from app.domains.kol.profile_recall_contract import RecallHit, _clean_text
from app.domains.kol.profile_recall_precision import HYBRID_METHOD
from app.domains.kol.profile_recall_projection import (
    _evidence_score,
    _extract_lenses,
    _reason_labels,
)


logger = get_logger(__name__)


def openai_client(
    *,
    proxy_override: str | None = None,
    direct: bool = False,
    timeout: float = 30.0,
):
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError(f"openai_sdk_unavailable: {exc}") from exc

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("missing_openai_api_key")
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
    return OpenAI(api_key=api_key)


def assert_collection_dim(
    client: Any,
    *,
    collection_name: str,
    vector_size: int,
) -> bool:
    """Validate the collection vector size and return whether it was verified."""

    size: Any = None
    try:
        info = client.get_collection(collection_name)
        vectors = info.config.params.vectors
        size = getattr(vectors, "size", None)
        if size is None and isinstance(vectors, dict) and vectors:
            size = getattr(next(iter(vectors.values())), "size", None)
    except Exception:
        logger.debug("collection_dim_probe_failed", exc_info=True)
        return False
    if size is not None and int(size) != vector_size:
        raise RuntimeError(f"qdrant_collection_dim_mismatch:{size}!={vector_size}")
    return True


def proxy_rotation_candidates(
    proxy: str,
    *,
    ports_env: str,
    default_ports: str,
) -> list[str]:
    """Build alternate sticky proxy URLs by replacing the trailing port."""

    match = re.match(r"^(?P<base>.+):(?P<port>\d+)/?$", str(proxy or "").strip())
    if not match:
        return []
    base, current_port = match.group("base"), match.group("port")
    raw = os.getenv(ports_env, default_ports)
    output: list[str] = []
    for token in str(raw).split(","):
        token = token.strip()
        if token.isdigit() and token != current_port and f"{base}:{token}" not in output:
            output.append(f"{base}:{token}")
    return output


def should_failover(exc: Exception) -> bool:
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


def search_qdrant(
    query_vector: list[float],
    candidate_limit: int,
    *,
    qdrant_client_factory: Callable[[], Any],
    assert_collection_dim: Callable[[Any], None],
    collection_name: str,
    method: str,
) -> list[RecallHit]:
    from qdrant_client.http import models as qdrant_models

    client = qdrant_client_factory()
    if not client.collection_exists(collection_name):
        raise RuntimeError(f"qdrant_collection_missing:{collection_name}")
    assert_collection_dim(client)
    query_filter = qdrant_models.Filter(
        must=[
            qdrant_models.FieldCondition(
                key="method",
                match=qdrant_models.MatchValue(value=method),
            )
        ]
    )
    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=query_filter,
            limit=max(1, int(candidate_limit)),
            with_payload=True,
        )
        points = list(getattr(response, "points", []) or [])
    else:  # pragma: no cover - older qdrant-client fallback
        points = client.search(
            collection_name=collection_name,
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


def dedupe_retrieval_hits(hits: list[RecallHit]) -> list[RecallHit]:
    output: list[RecallHit] = []
    seen: set[int] = set()
    for hit in hits:
        if hit.kol_pool_id <= 0 or hit.kol_pool_id in seen:
            continue
        seen.add(hit.kol_pool_id)
        output.append(hit)
    return output


def hybrid_fuse_hits(
    vector_hits: list[RecallHit],
    lexical_hits: list[RecallHit],
    *,
    limit: int,
    factual_anchor_required: bool,
    dedupe_hits: Callable[[list[RecallHit]], list[RecallHit]],
) -> list[RecallHit]:
    """Deterministic weighted RRF; missing sources are absent, never score zero."""

    vectors = dedupe_hits(vector_hits)
    lexicals = dedupe_hits(lexical_hits)
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


def recall_table_columns(
    conn: Any,
    table_name: str,
    *,
    fallback_table_columns: Callable[[Any, str], set[str]],
) -> set[str]:
    """Inspect PostgreSQL through read-only SQL before the legacy SQLite probe."""

    try:
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        columns = {
            str(dict(row).get("column_name") or "")
            for row in rows
            if str(dict(row).get("column_name") or "")
        }
        if columns:
            return columns
    except Exception:
        pass
    return fallback_table_columns(conn, table_name)


def evidence_summaries(
    kol_pool_ids: list[int],
    *,
    get_connection: Callable[[], Any],
) -> dict[int, dict[str, Any]]:
    summaries = _projection._evidence_summaries(
        kol_pool_ids,
        get_connection=get_connection,
    )
    if not kol_pool_ids:
        return summaries
    # Strict qualification additionally needs the identity of the newest
    # persisted video; merge that fact into the richer explainable projection.
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    try:
        rows = get_connection().execute(
            f"""
            SELECT e.kol_pool_id,
                   e.posted_at,
                   COALESCE(NULLIF(e.evidence_type, ''), 'video') AS evidence_type,
                   e.content_url,
                   COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title
            FROM vkpi_kol_video_evidence e
            WHERE e.kol_pool_id IN ({placeholders})
              AND e.posted_at IS NOT NULL
              AND e.is_active IS NOT FALSE
              AND (e.evidence_type IS NULL OR LOWER(TRIM(e.evidence_type)) = 'video')
            ORDER BY e.kol_pool_id, e.posted_at DESC NULLS LAST, e.id DESC
            """,
            tuple(kol_pool_ids),
        ).fetchall()
        seen: set[int] = set()
        for raw in rows:
            row = dict(raw)
            kol_id = int(row.get("kol_pool_id") or 0)
            if kol_id <= 0 or kol_id in seen:
                continue
            seen.add(kol_id)
            summaries.setdefault(kol_id, {})["latest_real_video"] = {
                "posted_at": row.get("posted_at"),
                "evidence_type": row.get("evidence_type") or "video",
                "content_url": _clean_text(row.get("content_url"), 500),
                "title": _clean_text(row.get("title"), 220),
                "is_active": True,
                "source": "vkpi_kol_video_evidence.posted_at",
            }
    except Exception:
        # Legacy snapshots may not expose the identity fields.  Recall remains
        # available while strict qualification stays fail-closed.
        logger.warning("latest real-video identity projection unavailable", exc_info=True)
    return summaries


def smart_local_evidence_summaries(
    kol_pool_ids: list[int],
    *,
    get_connection: Callable[[], Any],
) -> dict[int, dict[str, Any]]:
    """Load the minimal factual evidence used by the strict local gate."""

    if not kol_pool_ids:
        return {}
    placeholders = ",".join(["?"] * len(kol_pool_ids))
    rows = get_connection().execute(
        f"""
        SELECT e.kol_pool_id,
               COALESCE(NULLIF(e.title, ''), NULLIF(e.video_title, ''), NULLIF(e.content_url, '')) AS title,
               e.content_url,
               e.thumbnail_url,
               e.view_count,
               e.like_count,
               e.posted_at,
               COALESCE(NULLIF(e.evidence_type, ''), 'video') AS evidence_type,
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
    for raw in rows:
        row = dict(raw)
        by_id.setdefault(int(row["kol_pool_id"]), []).append(row)

    summaries: dict[int, dict[str, Any]] = {}
    for kol_id, evidence_rows in by_id.items():
        ranked = sorted(evidence_rows, key=_evidence_score, reverse=True)
        representative: list[dict[str, Any]] = []
        for row in ranked:
            title = _clean_text(row.get("title"), 220)
            url = _clean_text(row.get("content_url"), 500)
            if not title and not url:
                continue
            representative.append(
                {
                    "title": title or url,
                    "content_url": url,
                    "thumbnail_url": _clean_text(row.get("thumbnail_url"), 500),
                    "view_count": row.get("view_count"),
                    "like_count": row.get("like_count"),
                }
            )
            if len(representative) >= 3:
                break
        texts: list[str] = []
        for row in ranked[:6]:
            texts.extend(
                [
                    _clean_text(row.get("title"), 500),
                    _clean_text(row.get("product_presence"), 500),
                    _clean_text(row.get("brand_exposure"), 500),
                ]
            )
        latest = next(
            (
                row
                for row in evidence_rows
                if str(row.get("evidence_type") or "video").strip().lower() == "video"
                and row.get("posted_at")
            ),
            {},
        )
        summaries[kol_id] = {
            "representative_evidence": representative,
            "used_lenses": _extract_lenses(*texts),
            "reason_labels": _reason_labels(
                *(texts + [_clean_text(row.get("content_summary"), 500) for row in ranked[:3]])
            ),
            "video_evidence_count": len(evidence_rows),
            "latest_real_video": (
                {
                    "posted_at": latest.get("posted_at"),
                    "evidence_type": latest.get("evidence_type") or "video",
                    "content_url": _clean_text(latest.get("content_url"), 500),
                    "title": _clean_text(latest.get("title"), 220),
                    "is_active": True,
                    "source": "vkpi_kol_video_evidence.posted_at",
                }
                if latest
                else {}
            ),
        }
    return summaries


def smart_local_qualification_context(
    kol_pool_ids: list[int],
    *,
    rows_by_id: dict[int, dict[str, Any]],
    evidence_by_id: dict[int, dict[str, Any]],
    get_connection: Callable[[], Any],
    table_columns: Callable[[Any, str], set[str]],
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Load the extra server facts required by the strict local gate."""

    row_context = {kol_id: dict(row) for kol_id, row in rows_by_id.items()}
    evidence_context = {
        kol_id: dict(evidence)
        for kol_id, evidence in evidence_by_id.items()
    }
    if not kol_pool_ids:
        return row_context, evidence_context

    placeholders = ",".join(["?"] * len(kol_pool_ids))
    try:
        conn = get_connection()
        pool_columns = table_columns(conn, "vkpi_kol_pool")
        if "raw_platform_data" in pool_columns:
            rows = conn.execute(
                f"""
                SELECT id AS kol_pool_id, raw_platform_data
                FROM vkpi_kol_pool
                WHERE duplicate_of_id IS NULL
                  AND id IN ({placeholders})
                """,
                tuple(kol_pool_ids),
            ).fetchall()
            for raw in rows:
                row = dict(raw)
                kol_id = int(row.get("kol_pool_id") or 0)
                if kol_id in row_context:
                    row_context[kol_id]["raw_platform_data"] = row.get("raw_platform_data")
    except Exception:
        logger.warning("smart_local market annotation context unavailable", exc_info=True)

    try:
        conn = get_connection()
        evidence_columns = table_columns(conn, "vkpi_kol_video_evidence")
        if "posted_at" not in evidence_columns:
            return row_context, evidence_context
        evidence_type_select = (
            "COALESCE(NULLIF(evidence_type, ''), 'video') AS evidence_type"
            if "evidence_type" in evidence_columns
            else "'video' AS evidence_type"
        )
        if "title" in evidence_columns and "video_title" in evidence_columns:
            title_select = (
                "COALESCE(NULLIF(title, ''), NULLIF(video_title, ''), "
                "NULLIF(content_url, '')) AS title"
                if "content_url" in evidence_columns
                else "COALESCE(NULLIF(title, ''), NULLIF(video_title, '')) AS title"
            )
        elif "title" in evidence_columns:
            title_select = "NULLIF(title, '') AS title"
        elif "video_title" in evidence_columns:
            title_select = "NULLIF(video_title, '') AS title"
        else:
            title_select = "'' AS title"
        content_url_select = (
            "content_url" if "content_url" in evidence_columns else "'' AS content_url"
        )
        active_select = (
            "is_active" if "is_active" in evidence_columns else "TRUE AS is_active"
        )
        active_clause = "AND is_active IS NOT FALSE" if "is_active" in evidence_columns else ""
        type_clause = (
            "AND (evidence_type IS NULL OR LOWER(TRIM(evidence_type)) = 'video')"
            if "evidence_type" in evidence_columns
            else ""
        )
        rows = conn.execute(
            f"""
            SELECT kol_pool_id, posted_at, {evidence_type_select},
                   {content_url_select}, {title_select}, {active_select}
            FROM vkpi_kol_video_evidence
            WHERE kol_pool_id IN ({placeholders})
              AND posted_at IS NOT NULL
              {active_clause}
              {type_clause}
            ORDER BY kol_pool_id, posted_at DESC NULLS LAST, id DESC
            """,
            tuple(kol_pool_ids),
        ).fetchall()
        seen: set[int] = set()
        for raw in rows:
            row = dict(raw)
            kol_id = int(row.get("kol_pool_id") or 0)
            if kol_id <= 0 or kol_id in seen:
                continue
            seen.add(kol_id)
            evidence_context.setdefault(kol_id, {})["latest_real_video"] = {
                "posted_at": row.get("posted_at"),
                "evidence_type": row.get("evidence_type") or "video",
                "content_url": _clean_text(row.get("content_url"), 500),
                "title": _clean_text(row.get("title"), 220),
                "is_active": row.get("is_active") is not False,
                "source": "vkpi_kol_video_evidence.posted_at",
            }
    except Exception:
        logger.warning("smart_local latest-video context unavailable", exc_info=True)
    return row_context, evidence_context
