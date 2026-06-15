"""KOL video full-scan planner (two-layer: cheap full metadata + limited deep).

Layer design:
  * Full (visible) layer  — all active ``vkpi_kol_video_evidence`` rows
    (metadata only; cheap, no provider calls).
  * Analyzed layer        — evidence that already has a ready final_v1 deep
    analysis (``vkpi_kol_llm_deep_analysis_results`` or ``vkpi_analysis_cache``).
  * top-N representatives — the highest-signal *pending* (not-yet-analyzed)
    videos to suggest for deep analysis.

This module is READ-ONLY: it plans, it does not blast jobs into the queue and
it does not touch ``apify_jobs_worker.py``. The real deep-analysis run is
delegated to the existing
``video_analysis_enqueue.enqueue_final_v1_video_analysis`` (which writes
apify_jobs under a viltrox_fit_score guard). This planner never touches
viltrox_fit_score / rule_v0.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn

FINAL_V1_ANALYSIS_KIND = "video_final_v1"
FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_analyzed(conn: Any, evidence_id: int) -> bool:
    # Path A: a ready final_v1 deep-analysis row referencing this evidence (102).
    row = conn.execute(
        """
        SELECT 1
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE source_evidence_id=?
          AND analysis_kind=?
          AND status='ready'
        LIMIT 1
        """,
        (int(evidence_id), FINAL_V1_ANALYSIS_KIND),
    ).fetchone()
    if row:
        return True
    # Path B: a ready analysis_cache entry for this video (mirrors _ready_cache).
    cache = conn.execute(
        """
        SELECT 1
        FROM vkpi_analysis_cache
        WHERE target_type='video'
          AND target_id=?
          AND derive_method=?
          AND status='ready'
        LIMIT 1
        """,
        (str(int(evidence_id)), FINAL_V1_DERIVE_METHOD),
    ).fetchone()
    return bool(cache)


def plan_kol_video_fullscan(kol_pool_id: int, *, top_n: int = 5) -> dict[str, Any]:
    """Plan the two-layer full-scan for one KOL. Read-only; no enqueue, no write."""

    kol_pool_id = int(kol_pool_id)
    safe_top_n = max(1, min(int(top_n or 5), 50))
    conn = get_conn()

    kol = conn.execute("SELECT id FROM vkpi_kol_pool WHERE id=?", (kol_pool_id,)).fetchone()
    if not kol:
        raise LookupError(f"kol_pool_id not found: {kol_pool_id}")

    # Full (visible/metadata) layer — all active evidence (085 columns).
    rows = conn.execute(
        """
        SELECT id, content_url, platform, video_title, posted_at,
               view_count, like_count, comment_count
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND is_active IS NOT FALSE
        ORDER BY view_count DESC NULLS LAST, posted_at DESC NULLS LAST, id DESC
        """,
        (kol_pool_id,),
    ).fetchall()
    evidence = [dict(row) for row in rows]

    analyzed_count = 0
    pending: list[dict[str, Any]] = []
    for item in evidence:
        evidence_id = _int_or_none(item.get("id"))
        if evidence_id is None:
            continue
        if _is_analyzed(conn, evidence_id):
            analyzed_count += 1
        else:
            pending.append(item)

    total_videos = len(evidence)
    pending_count = len(pending)

    # top-N representatives: highest-signal pending videos
    # (view_count DESC, posted_at DESC). The full query is already ordered by
    # view_count DESC, posted_at DESC, so `pending` preserves that order.
    top_candidates: list[dict[str, Any]] = []
    for item in pending[:safe_top_n]:
        top_candidates.append(
            {
                "evidence_id": _int_or_none(item.get("id")),
                "content_url": item.get("content_url"),
                "view_count": item.get("view_count"),
                "posted_at": _jsonable(item.get("posted_at")),
                "reason": "high_view_pending_not_yet_deep_analyzed",
            }
        )

    return {
        "kol_pool_id": kol_pool_id,
        "total_videos": total_videos,
        "analyzed_count": analyzed_count,
        "pending_count": pending_count,
        "top_candidates": top_candidates,
        "enqueue_hint": (
            "Run deep analysis per candidate via "
            "video_analysis_enqueue.enqueue_final_v1_video_analysis("
            "kol_pool_id=..., evidence_id=..., staff=...). This planner does not "
            "enqueue; it never touches apify_jobs_worker or viltrox_fit_score."
        ),
        "provider_calls": False,
        "write_db": False,
    }


def enqueue_kol_video_fullscan(
    kol_pool_id: int,
    *,
    top_n: int = 5,
    staff: dict | None = None,
) -> dict[str, Any]:
    """Materialize the plan's top-N candidates into final_v1 video deep-analysis
    jobs via the existing enqueuer (record-only budget, zero in-request LLM).

    Never touches viltrox_fit_score / rule_v0: the only DB write is the
    ``apify_jobs`` enqueue performed inside
    ``video_analysis_enqueue`` (which carries its own V6-Fit rollback guard).
    """

    plan = plan_kol_video_fullscan(int(kol_pool_id), top_n=int(top_n))
    candidates = plan.get("top_candidates") or []
    items = [
        {"kol_pool_id": int(kol_pool_id), "evidence_id": c["evidence_id"]}
        for c in candidates
        if c.get("evidence_id") is not None
    ]

    from app.domains.kol import video_analysis_enqueue

    if not items:
        return {
            "status": "no_candidates",
            "kol_pool_id": int(kol_pool_id),
            "plan": plan,
            "queued": 0,
            "provider_calls": False,
            "write_db": False,
        }

    enqueue_result = video_analysis_enqueue.enqueue_final_v1_video_analysis_batch(
        items=items,
        staff=staff,
    )
    return {
        "status": "enqueued",
        "kol_pool_id": int(kol_pool_id),
        "plan": plan,
        "enqueue": enqueue_result,
        "queued": enqueue_result.get("queued", 0),
        "evidence_ids": [it["evidence_id"] for it in items],
        "budget_gate": "record_only",
        "provider_calls": False,
        "write_db": enqueue_result.get("write_db", False),
        "writes": enqueue_result.get("writes", []),
    }
