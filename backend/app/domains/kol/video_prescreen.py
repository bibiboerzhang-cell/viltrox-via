"""Cheap rule-only Viltrox prescreen over a KOL's cached video evidence.

Read-only by design:
- no provider/LLM calls;
- no enqueue;
- no writes (never touches viltrox_fit_score / rule_v0 scoring fields).

It reuses the cheap heuristic primitives from
``app.domains.market.brand_signal_detector`` (VILTROX_TERMS / PRODUCT_TERMS /
``_keyword_match``) against the text we actually have on the evidence row.

Note: ``vkpi_kol_video_evidence`` has no ``description`` column. The text blob
is built from ``COALESCE(NULLIF(title,''), NULLIF(video_title,''))`` plus
``content_url`` and ``channel_name`` only.
"""
from __future__ import annotations

from typing import Any

from app.db.connection import get_conn
from app.domains.market.brand_signal_detector import (
    VILTROX_TERMS,
    PRODUCT_TERMS,
    _keyword_match,
)


def prescreen_kol_videos(kol_pool_id: int) -> dict[str, Any]:
    """Rule-only keyword prescreen for one KOL. Read-only; no LLM, no write."""

    kol_pool_id = int(kol_pool_id)
    conn = get_conn()

    kol = conn.execute(
        "SELECT id FROM vkpi_kol_pool WHERE id=?",
        (kol_pool_id,),
    ).fetchone()
    if not kol:
        raise LookupError(f"kol_pool_id not found: {kol_pool_id}")

    rows = conn.execute(
        """
        SELECT id, content_url, platform,
               COALESCE(NULLIF(title, ''), NULLIF(video_title, '')) AS title,
               channel_name, view_count, posted_at
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND is_active IS NOT FALSE
        ORDER BY view_count DESC NULLS LAST, posted_at DESC NULLS LAST, id DESC
        """,
        (kol_pool_id,),
    ).fetchall()

    results: list[dict[str, Any]] = []
    hit_count = 0
    for raw in rows:
        r = dict(raw)
        blob = " ".join(
            str(r.get(key) or "") for key in ("title", "content_url", "channel_name")
        )
        matched_brand = [term for term in VILTROX_TERMS if _keyword_match(blob, term)]
        matched_skus = [
            sku
            for sku, terms in PRODUCT_TERMS.items()
            if any(_keyword_match(blob, term) for term in terms)
        ]
        viltrox_hit = bool(matched_brand or matched_skus)
        if viltrox_hit:
            hit_count += 1
        if matched_brand and matched_skus:
            reason = "brand_and_model_keyword"
        elif matched_brand:
            reason = "brand_keyword"
        elif matched_skus:
            reason = "lens_model_keyword"
        else:
            reason = "no_keyword_match"
        results.append(
            {
                "evidence_id": int(r["id"]),
                "viltrox_hit": viltrox_hit,
                "reason": reason,
                "matched_terms": (matched_brand + matched_skus)[:8],
            }
        )

    return {
        "kol_pool_id": kol_pool_id,
        "total_videos": len(results),
        # Full-scan visibility (P0-1): N = already-materialized evidence rows.
        # Same meaning as plan_kol_video_fullscan.materialized_visible_n.
        "materialized_visible_n": len(results),
        "viltrox_hit_count": hit_count,
        "results": results,
        "method": "rule_v0_keyword_prescreen",
        "provider_calls": False,
        "write_db": False,
    }
