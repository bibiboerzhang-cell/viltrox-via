"""Unified, read-only task-state recovery for one scoped MY KOL video library.

Contract ``my_kol_video_recovery_v1`` (served by
``GET /api/admin/vkpi/my-kol/{kol_pool_id}/videos``)::

    {
      "contract": "my_kol_video_recovery_v1",
      "kol_pool_id": 88,
      "read_only": true,
      "profile_crawl": TaskState,                # account crawl (kol_profile_deep_crawl)
      "items": [
        { ...video row from _video_evidence_for_kol...,
          "evidence_id": 701,
          "published_at": "2026-08-01T10:00:00+00:00" | null,
          "tasks": {
            "metric_refresh": TaskState,        # kol_video_metric_refresh + metric snapshot
            "final_v1": TaskState               # Gemini final_v1 job + analysis cache
          }
        }
      ],
      "summary": {"total", "views_total", "views_measured", "final_v1_ready"},
      "page": {
        "limit": 60, "returned": 60, "has_more": true,
        "next_cursor": "<opaque>" | null,
        "cursor_kind": "published_at_id",       # keyset: (published_at DESC, id DESC)
        "order": "published_at_desc_id_desc"
      },
      "total": <summary.total>, "returned": <page.returned>,
      "has_more": <page.has_more>, "next_cursor": <page.next_cursor>
    }

    TaskState = {
      "status": "queued" | "running" | "retrying" | "blocked" | "failed"
                | "ready" | "not_requested",     # durable job truth (apify_jobs)
      "job_id": int | null,
      "requested_at": iso | null,                # job created_at
      "updated_at": iso | null,                  # job updated_at
      "data": {                                  # persisted output, independent of the job
        "status": "ready" | "stale" | "none",
        "freshness": "fresh" | "stale" | "never" | "unavailable",
        "updated_at": iso | null,
        "superseded_by_job": bool                # an active job is newer than this data
      }
    }

Rules the contract guarantees:

* **Task state != data freshness.**  ``status`` is the latest durable job for
  that target; ``data`` describes what is persisted right now.  A page reopen
  therefore restores queued / running / retrying work exactly as the worker
  ledger sees it, and a ``ready`` job whose promised output is missing reports
  ``failed`` rather than pretending.
* **Old results never mask a newer request.**  When an active job was created
  after the persisted data, ``data.superseded_by_job`` is true and ``status``
  is the active job state; the previous ``data`` stays visible as history.
* **Stable keyset paging.**  Ordering is ``published_at DESC, id DESC`` where
  ``published_at = COALESCE(publish_date, posted_at, created_at)`` (never
  ``updated_at`` / ``view_count``, which drift with every metric refresh).  The
  cursor encodes the last row's ``(published_at, id)``; offsets are gone.
* The response never includes provider payloads, prompts, or raw worker errors.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.logging import get_logger

logger = get_logger("viltrox.domains.kol.my_kol_video_recovery")

CONTRACT = "my_kol_video_recovery_v1"
CURSOR_KIND = "published_at_id"
ORDER = "published_at_desc_id_desc"
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 60
FINAL_V1_DERIVE_METHOD = "video_analysis_final_v1"
PROFILE_JOB_TYPE = "kol_profile_deep_crawl"
METRIC_JOB_TYPE = "kol_video_metric_refresh"
VIDEO_JOB_TYPE = "video"
PROFILE_FRESH_HOURS = 24

TASK_STATUSES = frozenset({"queued", "running", "retrying", "blocked", "failed", "ready", "not_requested"})
ACTIVE_TASK_STATUSES = frozenset({"queued", "running", "retrying"})
DATA_STATUSES = frozenset({"ready", "stale", "none"})
FRESHNESS_VALUES = frozenset({"fresh", "stale", "never", "unavailable"})


# ── small helpers ───────────────────────────────────────────────────────


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
        logger.debug("table availability probe failed table=%s", table_name, exc_info=True)
        return False


# ── cursor: keyset (published_at, id) ───────────────────────────────────


def encode_cursor(published_at: Any, evidence_id: int) -> str:
    """Opaque keyset cursor for the row *after* ``(published_at, evidence_id)``."""
    payload = {"k": CURSOR_KIND, "p": _stamp(published_at), "i": int(evidence_id)}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: Any) -> tuple[str | None, int] | None:
    """Return ``(published_at, evidence_id)`` or None for an empty cursor.

    Raises ``ValueError`` for anything that is not the canonical encoding of a
    ``published_at_id`` cursor (wrong kind, offset-era cursors, tampered text).
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid videos cursor") from exc
    if not isinstance(payload, dict) or payload.get("k") != CURSOR_KIND:
        raise ValueError("invalid videos cursor")
    published_at = payload.get("p")
    evidence_id = payload.get("i")
    if published_at is not None and not isinstance(published_at, str):
        raise ValueError("invalid videos cursor")
    if not isinstance(evidence_id, int) or isinstance(evidence_id, bool) or evidence_id <= 0:
        raise ValueError("invalid videos cursor")
    if encode_cursor(published_at, evidence_id) != raw:
        raise ValueError("invalid videos cursor")
    return published_at, int(evidence_id)


# ── durable job projection ──────────────────────────────────────────────


def _job_status(row: dict[str, Any]) -> str:
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
        return "ready"
    if raw == "blocked":
        return "blocked"
    # triage / cancelled / timeout and unknown terminal values collapse to one
    # safe display state; raw worker text is never returned.
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
        "status": _job_status(item),
        "requested_at": _stamp(item.get("created_at")),
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


# ── TaskState assembly ──────────────────────────────────────────────────


def _data_block(
    *,
    status: str,
    freshness: str,
    updated_at: Any,
    superseded_by_job: bool,
) -> dict[str, Any]:
    return {
        "status": status if status in DATA_STATUSES else "none",
        "freshness": freshness if freshness in FRESHNESS_VALUES else "unavailable",
        "updated_at": _stamp(updated_at),
        "superseded_by_job": bool(superseded_by_job),
    }


def _task_state(job: dict[str, Any] | None, data: dict[str, Any]) -> dict[str, Any]:
    """Merge the latest durable job with persisted-data truth into one TaskState."""
    status = str((job or {}).get("status") or "not_requested")
    if status not in TASK_STATUSES:
        status = "failed"
    return {
        "status": status,
        "job_id": (job or {}).get("job_id"),
        "requested_at": (job or {}).get("requested_at"),
        "updated_at": (job or {}).get("updated_at"),
        "data": data,
    }


def _job_is_newer(job: dict[str, Any] | None, data_updated_at: Any) -> bool:
    if not job or job.get("status") not in ACTIVE_TASK_STATUSES:
        return False
    job_at = _moment(job.get("requested_at") or job.get("updated_at"))
    data_at = _moment(data_updated_at)
    return data_at is None or job_at is None or job_at > data_at


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
        result[evidence_id] = {"status": status, "updated_at": _stamp(item.get("updated_at"))}
    return result


def final_v1_task_state(cache: dict[str, Any] | None, job: dict[str, Any] | None) -> dict[str, Any]:
    """Gemini final_v1: job ledger vs. analysis cache row.

    * active job newer than the cache  -> status = job state, data.superseded_by_job = true
    * finished job but no ready cache  -> status = failed (the promise was not kept)
    * ready cache without any job row  -> status = ready (legacy / pruned ledger)
    """
    cache_status = str((cache or {}).get("status") or "").strip().lower()
    cache_at = (cache or {}).get("updated_at")
    if cache_status == "ready":
        data_status, freshness = "ready", "fresh"
    elif cache_status == "stale":
        data_status, freshness = "stale", "stale"
    else:
        data_status, freshness = "none", "never"
    data = _data_block(
        status=data_status,
        freshness=freshness,
        updated_at=cache_at,
        superseded_by_job=_job_is_newer(job, cache_at),
    )
    state = _task_state(job, data)
    if state["status"] == "ready" and data["status"] != "ready":
        state["status"] = "failed"
    elif state["status"] == "not_requested" and data["status"] == "ready":
        state["status"] = "ready"
    return state


def metric_refresh_task_state(video: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    """Metric refresh: job ledger vs. persisted metric snapshot (tracking layer)."""
    last_success = video.get("last_success") if isinstance(video.get("last_success"), dict) else {}
    freshness = str(video.get("freshness") or "unavailable").strip().lower()
    if freshness not in FRESHNESS_VALUES:
        freshness = "unavailable"
    snapshot_at = last_success.get("fetched_at") or video.get("metrics_scraped_at")
    data_status = "ready" if freshness == "fresh" else "stale" if freshness == "stale" else "none"
    data = _data_block(
        status=data_status,
        freshness=freshness,
        updated_at=snapshot_at,
        superseded_by_job=_job_is_newer(job, snapshot_at),
    )
    data["tracking_status"] = str(video.get("tracking_status") or "unavailable")
    data["sample_count"] = max(0, _int(video.get("sample_count")))
    data["attempt_count"] = max(0, _int(video.get("attempt_count")))
    return _task_state(job, data)


def _profile_crawl_data(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    if not _table_available(conn, "vkpi_kol_url_deep_crawl_runs"):
        return _data_block(status="none", freshness="unavailable", updated_at=None, superseded_by_job=False)
    row = conn.execute(
        """
        SELECT created_at
        FROM vkpi_kol_url_deep_crawl_runs
        WHERE kol_pool_id=? AND status='ready'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(kol_pool_id),),
    ).fetchone()
    crawled_at = dict(row).get("created_at") if row else None
    if not crawled_at:
        return _data_block(status="none", freshness="never", updated_at=None, superseded_by_job=False)
    fresh_cutoff = datetime.now(timezone.utc) - timedelta(hours=PROFILE_FRESH_HOURS)
    at = _moment(crawled_at)
    freshness = "fresh" if at is not None and at >= fresh_cutoff else "stale"
    return _data_block(
        status="ready" if freshness == "fresh" else "stale",
        freshness=freshness,
        updated_at=crawled_at,
        superseded_by_job=False,
    )


def profile_crawl_task_state(conn: Any, kol_pool_id: int) -> dict[str, Any]:
    """Account crawl: job ledger vs. latest ready deep-crawl run (24h freshness)."""
    job = _latest_jobs_for_targets(conn, job_type=PROFILE_JOB_TYPE, target_ids=(int(kol_pool_id),)).get(
        int(kol_pool_id)
    )
    data = _profile_crawl_data(conn, int(kol_pool_id))
    data["superseded_by_job"] = _job_is_newer(job, data.get("updated_at"))
    return _task_state(job, data)


# ── summary + page ──────────────────────────────────────────────────────


def _library_summary(conn: Any, kol_pool_id: int) -> dict[str, int]:
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
          AND COALESCE(e.is_active, TRUE) != FALSE
          AND COALESCE(e.evidence_type, 'video') IN ('video', 'image')
        """,
        (int(kol_pool_id),),
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
    limit: int,
) -> dict[str, Any]:
    """Attach unified TaskState truth to one keyset page of evidence rows.

    ``videos`` must already be ordered ``published_at DESC, id DESC`` and may
    contain up to ``limit + 1`` rows; the extra row only signals ``has_more``.
    """
    page_limit = max(1, min(MAX_PAGE_SIZE, int(limit or 1)))
    rows = [dict(video) for video in videos[: page_limit + 1]]
    has_more = len(rows) > page_limit
    rows = rows[:page_limit]
    evidence_ids = list(
        dict.fromkeys(
            _int(video.get("evidence_id") or video.get("id"))
            for video in rows
            if _int(video.get("evidence_id") or video.get("id")) > 0
        )
    )
    caches = _final_v1_caches(conn, evidence_ids)
    final_jobs = _latest_jobs_for_targets(
        conn, job_type=VIDEO_JOB_TYPE, target_ids=evidence_ids, derive_method=FINAL_V1_DERIVE_METHOD
    )
    metric_jobs = _latest_jobs_for_targets(conn, job_type=METRIC_JOB_TYPE, target_ids=evidence_ids)
    items: list[dict[str, Any]] = []
    for video in rows:
        evidence_id = _int(video.get("evidence_id") or video.get("id"))
        video["evidence_id"] = evidence_id
        video["published_at"] = _stamp(video.get("published_at"))
        video["tasks"] = {
            "metric_refresh": metric_refresh_task_state(video, metric_jobs.get(evidence_id)),
            "final_v1": final_v1_task_state(caches.get(evidence_id), final_jobs.get(evidence_id)),
        }
        items.append(video)

    summary = _library_summary(conn, int(kol_pool_id))
    last = items[-1] if items else None
    next_cursor = encode_cursor(last.get("published_at"), last["evidence_id"]) if has_more and last else None
    page = {
        "limit": page_limit,
        "returned": len(items),
        "has_more": bool(has_more),
        "next_cursor": next_cursor,
        "cursor_kind": CURSOR_KIND,
        "order": ORDER,
    }
    return {
        "contract": CONTRACT,
        "kol_pool_id": int(kol_pool_id),
        "read_only": True,
        "profile_crawl": profile_crawl_task_state(conn, int(kol_pool_id)),
        "items": items,
        "summary": summary,
        "page": page,
        "total": summary["total"],
        "returned": page["returned"],
        "has_more": page["has_more"],
        "next_cursor": page["next_cursor"],
    }


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "CONTRACT",
    "CURSOR_KIND",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "TASK_STATUSES",
    "build_video_recovery_page",
    "decode_cursor",
    "encode_cursor",
    "final_v1_task_state",
    "metric_refresh_task_state",
    "profile_crawl_task_state",
]
