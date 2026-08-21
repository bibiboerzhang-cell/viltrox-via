"""Bounded, read-only recovery state for one scoped MY KOL video library.

This module deliberately keeps durable job state separate from persisted data
freshness.  It never infers that a newly requested crawl/refresh/analysis is
complete from older evidence or cache rows, and it never exposes job payloads,
provider names, prompts, or raw worker errors.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Iterable


MAX_PAGE_SIZE = 200
MAX_CURSOR_OFFSET = 100_000
FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
PROFILE_JOB_TYPE = "kol_profile_deep_crawl"
METRIC_JOB_TYPE = "kol_video_metric_refresh"
VIDEO_JOB_TYPE = "video"

_ACTIVE_JOB_STATES = frozenset({"queued", "running", "retrying"})


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _stamp(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_sqlite(conn: Any) -> bool:
    return callable(getattr(conn, "executescript", None))


def _table_available(conn: Any, table_name: str) -> bool:
    try:
        if _is_sqlite(conn):
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (str(table_name),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name=? AND table_schema=ANY(current_schemas(FALSE))
                LIMIT 1
                """,
                (str(table_name),),
            ).fetchone()
        return row is not None
    except Exception:
        # Pre-migration local mirrors degrade to an honest empty state.  No
        # fallback creates tables or performs any other write.
        return False


def encode_cursor(offset: int, snapshot_boundary_id: int) -> str:
    normalized = max(0, min(MAX_CURSOR_OFFSET, int(offset or 0)))
    boundary = max(0, int(snapshot_boundary_id or 0))
    raw = f"v2:{normalized}:{boundary}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: Any) -> tuple[int, int]:
    raw = str(value or "").strip()
    if not raw:
        return 0, 0
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        version, offset_text, boundary_text = decoded.split(":", 2)
        offset = int(offset_text)
        boundary = int(boundary_text)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid videos cursor") from exc
    if version != "v2" or offset < 0 or offset > MAX_CURSOR_OFFSET or boundary <= 0:
        raise ValueError("invalid videos cursor")
    if encode_cursor(offset, boundary) != raw:
        # base64 silently absorbs trailing garbage bits; only the canonical
        # encoding of the decoded pair is accepted.
        raise ValueError("invalid videos cursor")
    return offset, boundary


def resolve_snapshot_boundary(conn: Any, kol_pool_id: int, requested: int = 0) -> int:
    """Freeze one evidence-id boundary for all pages in this cursor chain."""

    if int(requested or 0) > 0:
        return int(requested)
    if not _table_available(conn, "vkpi_kol_video_evidence"):
        return 0
    row = conn.execute(
        """
        SELECT COALESCE(MAX(id), 0) AS max_id
        FROM vkpi_kol_video_evidence
        WHERE kol_pool_id=?
          AND COALESCE(is_active, TRUE) != FALSE
          AND COALESCE(evidence_type, 'video') IN ('video', 'image')
        """,
        (int(kol_pool_id),),
    ).fetchone()
    return max(0, _int(dict(row).get("max_id") if row else 0))


def _job_state(row: dict[str, Any]) -> str:
    raw = str(row.get("status") or "").strip().lower()
    if raw == "queued":
        if _int(row.get("attempts")) > 0 and row.get("next_retry_at") not in (None, ""):
            return "retrying"
        return "queued"
    if raw == "retrying":
        return "retrying"
    if raw in {"running", "processing", "in_progress", "started"}:
        return "running"
    if raw in {"done", "success", "completed", "complete"}:
        return "done"
    if raw == "blocked":
        return "blocked"
    # triage/cancelled/timeout and unknown terminal values are intentionally
    # collapsed to one safe display state; raw worker text is never returned.
    return "failed"


def _project_job(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    job_id = _int(item.get("id"))
    if job_id <= 0:
        return None
    return {
        "job_id": job_id,
        "status": _job_state(item),
        "created_at": _stamp(item.get("created_at")),
        "updated_at": _stamp(item.get("updated_at")),
    }


def _latest_jobs_for_targets(
    conn: Any,
    *,
    job_type: str,
    target_ids: Iterable[int],
    derive_method: str | None = None,
) -> dict[int, dict[str, Any]]:
    ids = list(dict.fromkeys(_int(value) for value in target_ids if _int(value) > 0))[:MAX_PAGE_SIZE]
    if not ids or not _table_available(conn, "apify_jobs"):
        return {}
    placeholders = ",".join("?" for _ in ids)
    if _is_sqlite(conn):
        target_expr = "CAST(json_extract(payload, '$.target_id') AS TEXT)"
        method_expr = "CAST(json_extract(payload, '$.derive_method') AS TEXT)"
    else:
        target_expr = "payload->>'target_id'"
        method_expr = "payload->>'derive_method'"
    method_clause = f" AND {method_expr}=?" if derive_method else ""
    params: tuple[Any, ...] = (
        str(job_type),
        *(str(value) for value in ids),
        *((str(derive_method),) if derive_method else ()),
    )
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT id, status, attempts, next_retry_at, created_at, updated_at,
                   {target_expr} AS target_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY {target_expr}
                       ORDER BY id DESC
                   ) AS row_num
            FROM apify_jobs
            WHERE job_type=?
              AND {target_expr} IN ({placeholders})
              {method_clause}
        )
        SELECT id, status, attempts, next_retry_at, created_at, updated_at, target_id
        FROM ranked
        WHERE row_num=1
        """,
        params,
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        target_id = _int(item.get("target_id"))
        projected = _project_job(item)
        if target_id > 0 and projected:
            result[target_id] = projected
    return result


def _latest_profile_job(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    job = _latest_jobs_for_targets(
        conn,
        job_type=PROFILE_JOB_TYPE,
        target_ids=(int(kol_pool_id),),
    ).get(int(kol_pool_id))
    return job or {
        "job_id": None,
        "status": "not_requested",
        "created_at": None,
        "updated_at": None,
    }


def _final_v1_caches(conn: Any, evidence_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not evidence_ids or not _table_available(conn, "vkpi_analysis_cache"):
        return {}
    placeholders = ",".join("?" for _ in evidence_ids)
    rows = conn.execute(
        f"""
        SELECT id, target_id, status, updated_at
        FROM vkpi_analysis_cache
        WHERE target_type='video'
          AND target_id IN ({placeholders})
          AND derive_method=?
        """,
        (*(str(value) for value in evidence_ids), FINAL_V1_DERIVE_METHOD),
    ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        evidence_id = _int(item.get("target_id"))
        status = str(item.get("status") or "").strip().lower()
        if evidence_id <= 0 or status not in {"ready", "stale"}:
            continue
        result[evidence_id] = {
            "status": status,
            "updated_at": _stamp(item.get("updated_at")),
        }
    return result


def _snapshot_projection(video: dict[str, Any]) -> dict[str, Any]:
    last_attempt = video.get("last_attempt") if isinstance(video.get("last_attempt"), dict) else {}
    last_success = video.get("last_success") if isinstance(video.get("last_success"), dict) else {}
    freshness = str(video.get("freshness") or "unavailable").strip().lower()
    if freshness not in {"fresh", "stale", "never", "unavailable"}:
        freshness = "unavailable"
    status = str(video.get("tracking_status") or "unavailable").strip().lower()
    if status not in {"tracked", "failed", "stale", "insufficient_history", "unavailable"}:
        status = "unavailable"
    return {
        "status": status,
        "freshness": freshness,
        "last_attempt_at": _stamp(last_attempt.get("fetched_at")),
        "last_success_at": _stamp(last_success.get("fetched_at")),
        "sample_count": max(0, _int(video.get("sample_count"))),
        "attempt_count": max(0, _int(video.get("attempt_count"))),
    }


def _final_v1_projection(
    cache: dict[str, Any] | None,
    latest_job: dict[str, Any] | None,
) -> dict[str, Any]:
    cache_status = str((cache or {}).get("status") or "").strip().lower()
    job_status = str((latest_job or {}).get("status") or "").strip().lower()
    cache_at = _moment((cache or {}).get("updated_at"))
    job_at = _moment((latest_job or {}).get("created_at") or (latest_job or {}).get("updated_at"))
    active_is_newer = job_status in _ACTIVE_JOB_STATES and (
        cache_status != "ready"
        or cache_at is None
        or job_at is None
        or job_at > cache_at
    )
    if active_is_newer:
        state = "active"
    elif cache_status == "ready":
        state = "ready"
    elif job_status == "blocked":
        state = "blocked"
    elif job_status in {"failed", "done"}:
        # A done job without a ready cache did not produce the promised result.
        state = "failed"
    elif cache_status == "stale":
        state = "stale"
    else:
        state = "not_requested"
    return {
        "state": state,
        "cache": cache,
        "latest_job": latest_job,
    }


def _library_summary(conn: Any, kol_pool_id: int, snapshot_boundary_id: int) -> dict[str, int]:
    if not _table_available(conn, "vkpi_kol_video_evidence"):
        return {"total": 0, "views_total": 0, "views_measured": 0, "final_v1_ready": 0}
    cache_available = _table_available(conn, "vkpi_analysis_cache")
    analyzed_sql = (
        """
        SUM(CASE WHEN EXISTS (
            SELECT 1 FROM vkpi_analysis_cache c
            WHERE c.target_type='video' AND c.target_id=CAST(e.id AS TEXT)
              AND c.derive_method='video_analysis_final_v1' AND c.status='ready'
        ) THEN 1 ELSE 0 END)
        """
        if cache_available
        else "0"
    )
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(CASE WHEN e.view_count IS NOT NULL THEN 1 ELSE 0 END), 0) AS views_measured,
               COALESCE(SUM(e.view_count), 0) AS views_total,
               COALESCE({analyzed_sql}, 0) AS final_v1_ready
        FROM vkpi_kol_video_evidence e
        WHERE e.kol_pool_id=?
          AND (? <= 0 OR e.id <= ?)
          AND COALESCE(e.is_active, TRUE) != FALSE
          AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
        """,
        (int(kol_pool_id), int(snapshot_boundary_id), int(snapshot_boundary_id)),
    ).fetchone()
    item = dict(row) if row else {}
    return {
        "total": max(0, _int(item.get("total"))),
        "views_total": max(0, _int(item.get("views_total"))),
        "views_measured": max(0, _int(item.get("views_measured"))),
        "final_v1_ready": max(0, _int(item.get("final_v1_ready"))),
    }


def build_video_recovery_page(
    conn: Any,
    *,
    kol_pool_id: int,
    videos: list[dict[str, Any]],
    offset: int,
    limit: int,
    snapshot_boundary_id: int,
) -> dict[str, Any]:
    """Attach bounded job/cache truth to one already-scoped evidence page."""

    page_limit = max(1, min(MAX_PAGE_SIZE, int(limit or 1)))
    page_offset = max(0, min(MAX_CURSOR_OFFSET, int(offset or 0)))
    evidence_ids = list(
        dict.fromkeys(
            _int(video.get("evidence_id") or video.get("id"))
            for video in videos
            if _int(video.get("evidence_id") or video.get("id")) > 0
        )
    )[:page_limit]
    caches = _final_v1_caches(conn, evidence_ids)
    final_jobs = _latest_jobs_for_targets(
        conn,
        job_type=VIDEO_JOB_TYPE,
        target_ids=evidence_ids,
        derive_method=FINAL_V1_DERIVE_METHOD,
    )
    metric_jobs = _latest_jobs_for_targets(
        conn,
        job_type=METRIC_JOB_TYPE,
        target_ids=evidence_ids,
    )
    projected_videos: list[dict[str, Any]] = []
    for raw in videos[:page_limit]:
        video = dict(raw)
        evidence_id = _int(video.get("evidence_id") or video.get("id"))
        latest_metric_job = metric_jobs.get(evidence_id)
        video["metric_refresh"] = {
            "latest_job": latest_metric_job,
            "snapshot": _snapshot_projection(video),
        }
        video["final_v1"] = _final_v1_projection(
            caches.get(evidence_id),
            final_jobs.get(evidence_id),
        )
        projected_videos.append(video)

    boundary = max(0, int(snapshot_boundary_id or 0))
    summary = _library_summary(conn, int(kol_pool_id), boundary)
    returned = len(projected_videos)
    has_more = page_offset + returned < summary["total"]
    return {
        "kol_pool_id": int(kol_pool_id),
        "items": projected_videos,
        "profile_crawl": _latest_profile_job(conn, int(kol_pool_id)),
        "summary": summary,
        "total": summary["total"],
        "returned": returned,
        "has_more": has_more,
        "next_cursor": encode_cursor(page_offset + returned, boundary) if has_more and returned else None,
        "snapshot_boundary_id": boundary,
        "cursor_stable": True,
        "page_limit": page_limit,
        "read_only": True,
    }


__all__ = [
    "MAX_PAGE_SIZE",
    "build_video_recovery_page",
    "decode_cursor",
    "encode_cursor",
    "resolve_snapshot_boundary",
]
