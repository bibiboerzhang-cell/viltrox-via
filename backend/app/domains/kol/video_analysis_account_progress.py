"""Read-only account-level progress projection for final_v1 video analysis."""
from __future__ import annotations

from typing import Any, Callable

from app.domains.analysis.cache_reuse import canonical_final_v1_cache_reuse
from app.domains.kol import video_analysis_progress_reasons as progress_reasons


FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
PROGRESS_ACTIVE_STATES = ("queued", "running")
PROGRESS_FAILED_STATES = ("failed", "blocked", "triage")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def video_concurrency_hint() -> int:
    return progress_reasons.env_video_lane_hint()


def recent_final_v1_duration_p50_ms(conn: Any) -> tuple[int | None, str]:
    for hours, basis in ((24, "done_jobs_24h_p50"), (24 * 7, "done_jobs_7d_p50")):
        row = conn.execute(
            """
            SELECT percentile_cont(0.5) WITHIN GROUP (
                     ORDER BY EXTRACT(EPOCH FROM (updated_at - started_at)) * 1000
                   ) AS p50_ms,
                   COUNT(*) AS n
            FROM apify_jobs
            WHERE job_type='video'
              AND status='done'
              AND payload->>'derive_method'=?
              AND started_at IS NOT NULL
              AND updated_at >= NOW() - make_interval(hours => ?)
              AND updated_at > started_at
            """,
            (FINAL_V1_DERIVE_METHOD, int(hours)),
        ).fetchone()
        data = dict(row) if row else {}
        if _int_or_none(data.get("n")) and data.get("p50_ms") is not None:
            return int(float(data["p50_ms"])), basis
    return None, "no_sample"


def account_video_analysis_progress(
    conn: Any,
    kol_pool_id: int,
    *,
    limit: int,
    include_items: bool,
    list_evidence_ids: Callable[[Any, int], list[int]],
) -> dict[str, Any]:
    """Aggregate canonical, legacy, active and failed states without writes."""
    pool_id = _int_or_none(kol_pool_id)
    if not pool_id:
        raise ValueError("kol_pool_id required")
    cap = max(1, int(limit))
    all_ids = list_evidence_ids(conn, pool_id)
    scope_ids = all_ids[:cap]
    counts = {
        "ready": 0,
        "legacy_unverified": 0,
        "running": 0,
        "queued": 0,
        "failed": 0,
        "blocked": 0,
        "triage": 0,
        "not_requested": 0,
    }
    items: list[dict[str, Any]] = []
    earliest_queued_created_at: Any = None
    if scope_ids:
        placeholders = ", ".join("?" for _ in scope_ids)
        id_params = tuple(int(eid) for eid in scope_ids)
        text_params = tuple(str(eid) for eid in scope_ids)
        evidence_rows = conn.execute(
            f"""
            SELECT id, content_url, platform,
                   COALESCE(NULLIF(title, ''), NULLIF(video_title, ''), '') AS title
            FROM vkpi_kol_video_evidence
            WHERE id IN ({placeholders})
            """,
            id_params,
        ).fetchall()
        evidence_by_id = {int(dict(row)["id"]): dict(row) for row in evidence_rows}
        cache_rows = conn.execute(
            f"""
            SELECT id, target_type, target_id, derive_method, model,
                   prompt_version, result, status, updated_at
            FROM vkpi_analysis_cache
            WHERE target_type='video'
              AND derive_method=?
              AND status='ready'
              AND target_id IN ({placeholders})
            """,
            (FINAL_V1_DERIVE_METHOD, *text_params),
        ).fetchall()
        cache_by_id: dict[str, dict[str, Any]] = {}
        for raw_cache in cache_rows:
            cache = dict(raw_cache)
            cache.update(
                canonical_final_v1_cache_reuse(
                    cache,
                    target_type="video",
                    target_id=str(cache.get("target_id") or ""),
                    derive_method=FINAL_V1_DERIVE_METHOD,
                )
            )
            cache.pop("result", None)
            cache_by_id[str(cache["target_id"])] = cache
        job_rows = conn.execute(
            f"""
            SELECT DISTINCT ON (payload->>'target_id')
                   id, status, attempts, last_error_category, last_error,
                   created_at, started_at, updated_at,
                   payload->>'target_id' AS target_id,
                   payload->'diagnostics'->>'child_stderr_tail' AS child_stderr_tail
            FROM apify_jobs
            WHERE job_type='video'
              AND payload->>'derive_method'=?
              AND payload->>'target_type'='video'
              AND payload->>'target_id' IN ({placeholders})
            ORDER BY payload->>'target_id', created_at DESC, id DESC
            """,
            (FINAL_V1_DERIVE_METHOD, *text_params),
        ).fetchall()
        job_by_id = {str(dict(row)["target_id"]): dict(row) for row in job_rows}
        earliest_queued_created_at = min(
            (
                job.get("created_at")
                for job in job_by_id.values()
                if str(job.get("status") or "").lower() == "queued" and job.get("created_at")
            ),
            default=None,
        )
        for evidence_id in scope_ids:
            key = str(evidence_id)
            cache = cache_by_id.get(key)
            job = job_by_id.get(key) or {}
            job_status = str(job.get("status") or "").lower()
            if cache and cache.get("reusable") is True:
                state = "ready"
            elif cache:
                state = "legacy_unverified"
            elif job_status in PROGRESS_ACTIVE_STATES:
                state = job_status
            elif job_status in PROGRESS_FAILED_STATES:
                state = job_status
            else:
                state = "not_requested"
            counts[state] += 1
            if include_items:
                evidence = evidence_by_id.get(int(evidence_id), {})
                items.append(
                    {
                        "evidence_id": int(evidence_id),
                        "content_url": evidence.get("content_url"),
                        "platform": evidence.get("platform"),
                        "title": evidence.get("title"),
                        "state": state,
                        "job_id": _int_or_none(job.get("id")),
                        "job_status": job_status or None,
                        "attempts": _int_or_none(job.get("attempts")),
                        "last_error_category": job.get("last_error_category"),
                        "cache_updated_at": cache.get("updated_at") if cache else None,
                        "cache_reuse_status": cache.get("cache_reuse_status") if cache else None,
                        "revalidation_required": bool(cache and cache.get("revalidation_required")),
                        "job_updated_at": job.get("updated_at"),
                        **progress_reasons.failure_fields(
                            status=job_status,
                            last_error_category=job.get("last_error_category"),
                            last_error=job.get("last_error"),
                            stderr_tail=job.get("child_stderr_tail"),
                        ),
                    }
                )
    completed = counts["ready"]
    in_progress = counts["running"] + counts["queued"]
    failed = counts["failed"] + counts["blocked"] + counts["triage"]
    legacy_unverified = counts["legacy_unverified"]
    scope_total = len(scope_ids)
    if scope_total == 0:
        state = "no_evidence"
    elif in_progress > 0:
        state = "running"
    elif completed == scope_total:
        state = "done"
    elif completed + failed == scope_total:
        state = "partial_failed"
    elif legacy_unverified:
        state = "partial"
    elif completed == 0 and failed == 0:
        state = "idle"
    else:
        state = "partial"
    p50_ms, basis = recent_final_v1_duration_p50_ms(conn) if in_progress else (None, "not_needed")
    lanes, lanes_basis = progress_reasons.active_lane_count(conn) if in_progress else (0, "not_needed")
    queue_ahead = (
        progress_reasons.queue_ahead_count(
            conn,
            derive_method=FINAL_V1_DERIVE_METHOD,
            earliest_queued_created_at=earliest_queued_created_at,
        )
        if in_progress
        else 0
    )
    eta_seconds = progress_reasons.estimate_eta_seconds(
        in_progress=in_progress, queue_ahead=queue_ahead, lanes=lanes, p50_ms=p50_ms
    )
    return {
        "kol_pool_id": pool_id,
        "derive_method": FINAL_V1_DERIVE_METHOD,
        "scope": {"limit": cap, "evidence_total": len(all_ids), "scope_total": scope_total},
        "state": state,
        "completed": completed,
        "in_progress": in_progress,
        "failed": failed,
        "legacy_unverified": legacy_unverified,
        "not_requested": counts["not_requested"],
        "percent": int(round(100.0 * completed / scope_total)) if scope_total else 0,
        "counts": counts,
        "eta_seconds": eta_seconds,
        "eta": {
            "remaining": in_progress,
            "queue_ahead": queue_ahead,
            "recent_p50_ms": p50_ms,
            "basis": basis,
            "active_lanes": lanes,
            "lanes_basis": lanes_basis,
            "effective_parallelism": min(lanes, in_progress + queue_ahead) if in_progress else 0,
            "estimated_remaining_seconds": eta_seconds,
        },
        "items": items if include_items else [],
        "write_db": False,
        "provider_calls": False,
    }


__all__ = [
    "PROGRESS_ACTIVE_STATES",
    "PROGRESS_FAILED_STATES",
    "account_video_analysis_progress",
    "recent_final_v1_duration_p50_ms",
    "video_concurrency_hint",
]
