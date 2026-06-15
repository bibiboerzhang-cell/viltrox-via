"""KOL long-term memory aggregator (v1: pure aggregate, no LLM, no v6_fit).

This module builds a read-only "memory" snapshot of a KOL from existing
signals (deep analysis results, video evidence, content posts, assignments,
failed jobs, lifecycle timeline). It is physically isolated from KOL scoring:

  * It never reads or writes ``vkpi_kol_pool.viltrox_fit_score`` into the
    snapshot, and it never touches ``rule_v0``.
  * ``llm_v6_fit`` (LLM-only deep-fit signal) is deliberately excluded from the
    snapshot — it is adjacent-to-scoring semantics.
  * ``rebuild_kol_memory_snapshot`` wraps its INSERT with a viltrox_fit_score
    snapshot guard (mirrors video_evidence.py) and rolls back + raises if any
    score changed, proving zero score touches.

No LLM provider calls. No write to scoring tables.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import lifecycle as kol_lifecycle

MEMORY_METHOD = "kol_memory_pure_aggregate_v1"
SCORE_FIELDS = ("viltrox_fit_score", "viltrox_fit_reason")

_SHIPPED_STAGES = ("content_posted", "reviewed")
_PUBLISHED_STAGES = ("published",)
_PUBLISHED_POST_STATUSES = ("matched", "retrospective_ready")


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or "")
    except Exception:
        return default
    return parsed if parsed is not None else default


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dim(item: Any) -> dict[str, Any]:
    dims = _loads(item, {})
    return dims if isinstance(dims, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _dedup_strings(values: list[Any]) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.append(text)
    return seen


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── source readers (all SELECT, never read/write viltrox_fit_score) ───

def _load_kol(conn: Any, kol_pool_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, content_style, recommended_product_lines_json, created_at
        FROM vkpi_kol_pool
        WHERE id=?
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return dict(row) if row else None


def _load_deep_results(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, analysis_kind, source_evidence_id, llm_dimensions_11, created_at
        FROM vkpi_kol_llm_deep_analysis_results
        WHERE kol_pool_id=?
          AND status='ready'
        ORDER BY created_at DESC, id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_video_evidence(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, content_url, platform, video_title, posted_at,
               view_count, like_count, comment_count
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND is_active IS NOT FALSE
        ORDER BY posted_at DESC NULLS LAST, id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_content_posts(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, project_id, platform, content_url, published_at,
               view_count, like_count, comment_count, status
        FROM vkpi_project_content_posts
        WHERE kol_pool_id=?
        ORDER BY published_at DESC NULLS LAST, id DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_assignments(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, project_id, stage, stage_status, tracking_number, created_at, updated_at
        FROM vkpi_project_kol_assignments
        WHERE kol_pool_id=?
        ORDER BY created_at DESC
        """,
        (int(kol_pool_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_failed_jobs(conn: Any, kol_pool_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, job_type, last_error, updated_at
        FROM apify_jobs
        WHERE status='failed'
          AND payload->>'kol_pool_id' = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (str(int(kol_pool_id)),),
    ).fetchall()
    return [dict(row) for row in rows]


# ─── inference helpers (pure aggregate; never derive scores) ───

def _infer_content_style(deep_results: list[dict[str, Any]], kol: dict[str, Any]) -> str:
    summaries: list[str] = []
    observations: list[str] = []
    for item in deep_results:
        dims = _dim(item.get("llm_dimensions_11"))
        layer1 = dims.get("layer1_summary") if isinstance(dims.get("layer1_summary"), dict) else {}
        content_summary = _text(layer1.get("content_summary"))
        if content_summary:
            summaries.append(content_summary)
        prod_obs = _text(layer1.get("production_observations"))
        if prod_obs:
            observations.append(prod_obs)
    parts = _dedup_strings(summaries + observations)
    if parts:
        return " | ".join(parts[:3])
    # fallback: read-only kol_pool.content_style string (never written).
    return _text(kol.get("content_style"))


def _infer_recommended_product_lines(deep_results: list[dict[str, Any]], kol: dict[str, Any]) -> list[str]:
    collected: list[Any] = []
    for item in deep_results:
        dims = _dim(item.get("llm_dimensions_11"))
        layer1 = dims.get("layer1_summary") if isinstance(dims.get("layer1_summary"), dict) else {}
        recs = dims.get("recommendations") if isinstance(dims.get("recommendations"), dict) else {}
        collected.extend(_as_list(layer1.get("product_presence")))
        collected.extend(_as_list(layer1.get("brand_exposure")))
        collected.extend(_as_list(recs.get("cooperation_recommendation")))
    lines = _dedup_strings(collected)
    if lines:
        return lines
    # fallback: read-only kol_pool.recommended_product_lines_json (TEXT json).
    fallback = _loads(kol.get("recommended_product_lines_json"), [])
    return _dedup_strings(_as_list(fallback))


def _infer_risk(deep_results: list[dict[str, Any]]) -> dict[str, Any]:
    flags: list[Any] = []
    final_verdict: str | None = None
    for item in deep_results:
        dims = _dim(item.get("llm_dimensions_11"))
        risk = dims.get("risk") if isinstance(dims.get("risk"), dict) else {}
        flags.extend(_as_list(risk.get("risk_flags")))
        verdict = _text(risk.get("final_verdict"))
        if verdict and final_verdict is None:
            final_verdict = verdict
    return {"risk_flags": _dedup_strings(flags), "final_verdict": final_verdict}


def _compute_fulfillment(
    assignments: list[dict[str, Any]],
    content_posts: list[dict[str, Any]],
    failed_jobs: list[dict[str, Any]],
) -> dict[str, int]:
    assigned_count = len(assignments)
    shipped_count = 0
    published_from_stage = 0
    for item in assignments:
        stage = _text(item.get("stage"))
        tracking = _text(item.get("tracking_number"))
        if stage in _SHIPPED_STAGES or tracking:
            shipped_count += 1
        if stage in _PUBLISHED_STAGES:
            published_from_stage += 1
    published_from_posts = sum(
        1 for item in content_posts if _text(item.get("status")) in _PUBLISHED_POST_STATUSES
    )
    published_count = published_from_posts + published_from_stage
    return {
        "assigned_count": assigned_count,
        "shipped_count": shipped_count,
        "published_count": published_count,
        "failed_jobs_count": len(failed_jobs),
    }


# ─── public API ───

def build_kol_memory_snapshot(kol_pool_id: int) -> dict[str, Any]:
    """Build the pure-aggregate memory snapshot (no LLM, no v6_fit, no score)."""

    kol_pool_id = int(kol_pool_id)
    conn = get_conn()
    kol = _load_kol(conn, kol_pool_id)
    if not kol:
        return {
            "status": "missing",
            "kol_pool_id": kol_pool_id,
            "snapshot": {
                "content_style": "",
                "recommended_product_lines": [],
                "risk": {"risk_flags": [], "final_verdict": None},
                "fulfillment": {
                    "assigned_count": 0,
                    "shipped_count": 0,
                    "published_count": 0,
                    "failed_jobs_count": 0,
                },
                "timeline": [],
                "note": "pure_aggregate,no_llm,no_v6_fit",
            },
            "source_counts": {
                "deep_results": 0,
                "video_evidence": 0,
                "content_posts": 0,
                "assignments": 0,
                "failed_jobs": 0,
            },
            "computed_at": _utcnow(),
            "note": "pure_aggregate, no_llm, no_v6_fit",
        }

    deep_results = _load_deep_results(conn, kol_pool_id)
    video_evidence = _load_video_evidence(conn, kol_pool_id)
    content_posts = _load_content_posts(conn, kol_pool_id)
    assignments = _load_assignments(conn, kol_pool_id)
    failed_jobs = _load_failed_jobs(conn, kol_pool_id)
    timeline = kol_lifecycle.collect_lifecycle_events(kol_pool_id)

    snapshot = {
        "content_style": _infer_content_style(deep_results, kol),
        "recommended_product_lines": _infer_recommended_product_lines(deep_results, kol),
        "risk": _infer_risk(deep_results),
        "fulfillment": _compute_fulfillment(assignments, content_posts, failed_jobs),
        "timeline": timeline,
        "note": "pure_aggregate,no_llm,no_v6_fit",
    }
    source_counts = {
        "deep_results": len(deep_results),
        "video_evidence": len(video_evidence),
        "content_posts": len(content_posts),
        "assignments": len(assignments),
        "failed_jobs": len(failed_jobs),
    }
    return {
        "status": "ready",
        "kol_pool_id": kol_pool_id,
        "snapshot": _jsonable(snapshot),
        "source_counts": source_counts,
        "computed_at": _utcnow(),
        "note": "pure_aggregate, no_llm, no_v6_fit",
    }


def _score_snapshot(conn: Any, kol_pool_id: int) -> dict[int, dict[str, Any]]:
    row = conn.execute(
        "SELECT id, viltrox_fit_score, viltrox_fit_reason FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        return {}
    item = dict(row)
    return {
        int(item["id"]): {
            "viltrox_fit_score": item.get("viltrox_fit_score"),
            "viltrox_fit_reason": item.get("viltrox_fit_reason"),
        }
    }


def _changed_score_ids(before: dict[int, dict[str, Any]], after: dict[int, dict[str, Any]]) -> list[int]:
    changed: list[int] = []
    for kol_id, before_item in before.items():
        after_item = after.get(kol_id, {})
        if any(before_item.get(field) != after_item.get(field) for field in SCORE_FIELDS):
            changed.append(kol_id)
    return changed


def rebuild_kol_memory_snapshot(kol_pool_id: int) -> dict[str, Any]:
    """Rebuild and persist a memory snapshot (pure aggregate, no LLM).

    Wraps the INSERT in a viltrox_fit_score snapshot guard to prove the rebuild
    touches zero scoring fields; rolls back + raises if any score changed.
    """

    kol_pool_id = int(kol_pool_id)
    built = build_kol_memory_snapshot(kol_pool_id)
    if built.get("status") == "missing":
        return {
            "written": False,
            "snapshot_id": None,
            "kol_pool_id": kol_pool_id,
            "snapshot": built.get("snapshot"),
            "source_counts": built.get("source_counts"),
            "llm_calls": False,
            "viltrox_fit_score_changed_ids": [],
            "computed_at": built.get("computed_at"),
            "status": "missing",
        }

    conn = get_conn()
    before_scores = _score_snapshot(conn, kol_pool_id)
    try:
        row = conn.execute(
            """
            INSERT INTO vkpi_kol_memory_snapshots
                (kol_pool_id, snapshot_json, source_counts, computed_at)
            VALUES (?, ?::jsonb, ?::jsonb, NOW())
            RETURNING id
            """,
            (
                kol_pool_id,
                json.dumps(built.get("snapshot") or {}, ensure_ascii=False, default=str),
                json.dumps(built.get("source_counts") or {}, ensure_ascii=False, default=str),
            ),
        ).fetchone()
        after_scores = _score_snapshot(conn, kol_pool_id)
        changed_ids = _changed_score_ids(before_scores, after_scores)
        if changed_ids:
            conn.rollback()
            raise RuntimeError(f"viltrox_fit_score changed unexpectedly: {changed_ids}; rolled back")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise

    snapshot_id = int(dict(row)["id"]) if row else None
    return {
        "written": snapshot_id is not None,
        "snapshot_id": snapshot_id,
        "kol_pool_id": kol_pool_id,
        "snapshot": built.get("snapshot"),
        "source_counts": built.get("source_counts"),
        "llm_calls": False,
        "viltrox_fit_score_changed_ids": [],
        "computed_at": built.get("computed_at"),
        "status": "ready",
    }


def get_latest_kol_memory_snapshot(kol_pool_id: int) -> dict[str, Any] | None:
    """Read the most recent persisted snapshot for one KOL, or None."""

    kol_pool_id = int(kol_pool_id)
    row = get_conn().execute(
        """
        SELECT id, kol_pool_id, snapshot_json, source_counts, computed_at
        FROM vkpi_kol_memory_snapshots
        WHERE kol_pool_id=?
        ORDER BY computed_at DESC, id DESC
        LIMIT 1
        """,
        (kol_pool_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    return {
        "status": "ready",
        "snapshot_id": item.get("id"),
        "kol_pool_id": int(item.get("kol_pool_id")) if item.get("kol_pool_id") is not None else kol_pool_id,
        "snapshot": _loads(item.get("snapshot_json"), {}),
        "source_counts": _loads(item.get("source_counts"), {}),
        "computed_at": _jsonable(item.get("computed_at")),
        "note": "pure_aggregate, no_llm, no_v6_fit",
    }
