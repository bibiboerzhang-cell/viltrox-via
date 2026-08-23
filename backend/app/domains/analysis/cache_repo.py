"""Thin read repository for unified analysis cache results.

优化波 B·C5(迁移 289):``vkpi_analysis_cache`` 多两列 ``prompt_version`` / ``model_family``。
本模块同时是视频 worker 写路径的单一 SQL 真源(``VIDEO_ANALYSIS_CACHE_UPSERT_SQL`` +
``video_analysis_cache_upsert_params``),两列在写入时就填好;唯一键
(target_type, target_id, derive_method) 不动,避免同一目标重复分析。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from app.db.connection import close_standalone_conn, open_standalone_conn
from app.platform.llm_local_evaluation import (
    LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
    LOCAL_EVALUATION_DERIVE_METHOD,
    LOCAL_EVALUATION_EXECUTION_CLASS,
)
from app.platform.llm_runtime_errors import normalise_job_error


AnalysisCacheEntry = dict[str, Any]
ProjectVideoAnalysisCache = dict[str, Any]

_ACTIVE_VIDEO_JOB_STATUSES = {"queued", "running", "retrying", "processing"}
_FAILED_VIDEO_JOB_STATUSES = {"failed", "blocked", "triage", "cancelled", "void", "timeout"}
_FINAL_V1_METHOD = "video_analysis_final_v1"
_FINAL_V1_QA_METHOD = "video_analysis_final_v1_keyframe_qa"


_MODEL_FAMILY_PATTERNS = (
    re.compile(r"(gemini-[0-9]+(?:\.[0-9]+)*)"),
    re.compile(r"(gpt-[0-9]+(?:\.[0-9]+)*)"),
    re.compile(r"(claude-[a-z]+-[0-9]+)"),
)


def model_family(model: Any) -> str | None:
    """模型家族 = model 列前缀(与迁移 289 回填 SQL 同口径):gemini-3.6-flash → gemini-3.6,
    gemini-2.5-flash → gemini-2.5,gpt-5.5-xxx → gpt-5.5,claude-opus-4-1 → claude-opus-4,
    其余取首段(mock → mock);空 → None。"""

    text = str(model or "").strip().lower()
    if not text:
        return None
    for pattern in _MODEL_FAMILY_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    head = text.split("-", 1)[0].strip()
    return head or None


# 视频 worker 写路径(psycopg 直连,%s 占位);两条 worker 路径共用,防 SQL 漂移。
VIDEO_ANALYSIS_CACHE_UPSERT_SQL = """
    INSERT INTO vkpi_analysis_cache (
      target_type, target_id, model, derive_method, result, cost,
      status, triggered_by_user_id, prompt_version, model_family, created_at, updated_at
    )
    VALUES (%s, %s, %s, %s, %s::jsonb, %s, 'ready', %s, %s, %s, NOW(), NOW())
    ON CONFLICT (target_type, target_id, derive_method)
    DO UPDATE SET
      model = EXCLUDED.model,
      result = EXCLUDED.result,
      cost = EXCLUDED.cost,
      status = 'ready',
      triggered_by_user_id = EXCLUDED.triggered_by_user_id,
      prompt_version = EXCLUDED.prompt_version,
      model_family = EXCLUDED.model_family,
      updated_at = NOW()
    RETURNING id
"""


def video_analysis_cache_upsert_params(
    *,
    target_type: str,
    target_id: str,
    model: str,
    derive_method: str,
    result_json: str,
    cost: Any,
    triggered_by_user_id: int | None,
    prompt_version: str | None,
) -> tuple[Any, ...]:
    """与 ``VIDEO_ANALYSIS_CACHE_UPSERT_SQL`` 一一对应的参数元组;model_family 自动派生。"""

    exact_model = str(model or "")
    return (
        str(target_type),
        str(target_id),
        exact_model,
        str(derive_method),
        result_json,
        cost,
        triggered_by_user_id,
        (str(prompt_version).strip() or None) if prompt_version else None,
        model_family(exact_model),
    )


def upsert_video_analysis_cache(cur: Any, **fields: Any) -> int | None:
    """在调用方的 psycopg cursor 上执行视频缓存 upsert,返回行 id(字段同 video_analysis_cache_upsert_params)。"""

    cur.execute(VIDEO_ANALYSIS_CACHE_UPSERT_SQL, video_analysis_cache_upsert_params(**fields))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _loads_json(value: Any) -> Any:
    if value in (None, "", b""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return value


def _number_or_none(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        numeric = Decimal(str(value))
    except Exception:
        return None
    return int(numeric) if numeric == numeric.to_integral_value() else float(numeric)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _video_platform(platform: Any, content_url: Any) -> str:
    host = (urlparse(str(content_url or "")).netloc or "").lower()
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if host:
        return "unsupported"
    normalized = str(platform or "").strip().lower()
    if normalized in {"youtube", "instagram", "tiktok"}:
        return normalized
    return "unsupported"


def _video_job_snapshot(row: Any) -> dict[str, Any] | None:
    data = dict(row)
    if not data.get("job_id"):
        return None
    return {
        "id": _int_or_none(data.get("job_id")),
        "status": str(data.get("job_status") or "").lower(),
        "last_error": data.get("job_last_error") or None,
        "created_at": data.get("job_created_at") or None,
        "started_at": data.get("job_started_at") or None,
        "updated_at": data.get("job_updated_at") or None,
    }


def _project_video_item_state(
    *,
    derive_method: str,
    platform: str,
    entry: AnalysisCacheEntry | None,
    job: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Return a truthful display state; missing cache alone is never treated as queued."""
    if entry:
        return "ready", None
    if derive_method == _FINAL_V1_QA_METHOD and platform != "youtube":
        return "unsupported", "keyframe_qa_youtube_only"
    if derive_method == _FINAL_V1_METHOD and platform not in {"youtube", "instagram", "tiktok"}:
        return "unsupported", "unsupported_video_platform"

    job_status = str((job or {}).get("status") or "").lower()
    if job_status in _ACTIVE_VIDEO_JOB_STATUSES:
        return job_status, None
    if job_status in _FAILED_VIDEO_JOB_STATUSES:
        return "failed", str((job or {}).get("last_error") or f"job_{job_status}")
    if job_status == "done":
        return "failed", "job_done_without_ready_cache"
    return "not_requested", "analysis_not_requested"


def _row_get(row: Any, key: str) -> Any:
    """迁移 289 新列的容错读:旧快照/假行没有该键时返回 None。"""

    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _row_to_entry(row: Any) -> AnalysisCacheEntry:
    model = row["model"] or None
    family = _row_get(row, "model_family")
    return {
        "target_type": str(row["target_type"] or ""),
        "target_id": str(row["target_id"] or ""),
        "derive_method": str(row["derive_method"] or ""),
        "model": model,
        "model_family": str(family) if family else model_family(model),
        "prompt_version": (str(_row_get(row, "prompt_version")) or None) if _row_get(row, "prompt_version") else None,
        "cost": _number_or_none(row["cost"]),
        "status": str(row["status"] or ""),
        "triggered_by_user_id": _int_or_none(row["triggered_by_user_id"]),
        "result": _loads_json(row["result"]),
        "created_at": row["created_at"] or None,
        "updated_at": row["updated_at"] or None,
    }


def _with_connection(conn: Any | None) -> tuple[Any, bool]:
    if conn is not None:
        return conn, False
    return open_standalone_conn(), True


def get_analysis_cache_entry(
    target_type: str,
    target_id: str,
    *,
    derive_method: str | None = None,
    allow_local_evaluation_fallback: bool = False,
    conn: Any | None = None,
) -> AnalysisCacheEntry | None:
    """Return one cache entry, keeping production authoritative.

    Local evaluation is never included by default.  The authenticated UI read
    endpoint may opt into a final_v1-only fallback; even then production
    ``video_analysis_final_v1`` is ordered first and wins regardless of age.
    """
    active_conn, should_close = _with_connection(conn)
    try:
        clauses = ["target_type=?", "target_id=?"]
        params: list[Any] = [target_type, str(target_id)]
        use_eval_fallback = bool(
            allow_local_evaluation_fallback
            and derive_method == LOCAL_EVALUATION_DERIVE_METHOD
        )
        if use_eval_fallback:
            clauses.append("derive_method IN (?, ?)")
            params.extend(
                [
                    LOCAL_EVALUATION_DERIVE_METHOD,
                    LOCAL_EVALUATION_CACHE_DERIVE_METHOD,
                ]
            )
        elif derive_method:
            clauses.append("derive_method=?")
            params.append(derive_method)
        order_prefix = (
            "CASE WHEN derive_method=? THEN 0 ELSE 1 END, "
            if use_eval_fallback
            else ""
        )
        if use_eval_fallback:
            params.append(LOCAL_EVALUATION_DERIVE_METHOD)
        row = active_conn.execute(
            f"""
            SELECT target_type, target_id, derive_method, model, cost, status,
                   triggered_by_user_id, result, created_at, updated_at,
                   prompt_version, model_family
            FROM vkpi_analysis_cache
            WHERE {" AND ".join(clauses)}
            ORDER BY {order_prefix}updated_at DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return _row_to_entry(row) if row else None
    finally:
        if should_close:
            close_standalone_conn(active_conn)


def get_analysis_cache_entries_for_targets(
    target_type: str,
    target_ids: list[str | int] | tuple[str | int, ...],
    *,
    derive_methods: list[str] | tuple[str, ...],
    conn: Any | None = None,
) -> dict[tuple[str, str], AnalysisCacheEntry]:
    """Read a bounded target/method matrix in one query.

    ``vkpi_analysis_cache`` has one authoritative row per
    ``(target_type, target_id, derive_method)``.  Detail views previously opened
    two database reads for every video; this helper keeps the same truth model
    while making the query count constant.  Empty/duplicate inputs are removed
    before SQL construction and both dimensions are hard-bounded so this helper
    cannot become an unbounded response path.
    """
    normalized_ids = list(dict.fromkeys(str(value).strip() for value in target_ids if str(value).strip()))[:200]
    normalized_methods = list(
        dict.fromkeys(str(value).strip() for value in derive_methods if str(value).strip())
    )[:20]
    if not normalized_ids or not normalized_methods:
        return {}

    active_conn, should_close = _with_connection(conn)
    try:
        id_placeholders = ",".join("?" for _ in normalized_ids)
        method_placeholders = ",".join("?" for _ in normalized_methods)
        rows = active_conn.execute(
            f"""
            SELECT target_type, target_id, derive_method, model, cost, status,
                   triggered_by_user_id, result, created_at, updated_at,
                   prompt_version, model_family
            FROM vkpi_analysis_cache
            WHERE target_type=?
              AND target_id IN ({id_placeholders})
              AND derive_method IN ({method_placeholders})
            """,
            (str(target_type).strip(), *normalized_ids, *normalized_methods),
        ).fetchall()
        return {
            (str(row["target_id"]), str(row["derive_method"])): _row_to_entry(row)
            for row in rows
        }
    finally:
        if should_close:
            close_standalone_conn(active_conn)


def _safe_job_error(last_error: Any) -> dict[str, Any]:
    """Return the small, non-sensitive subset of a persisted worker error for UI state."""
    parsed = _loads_json(last_error)
    if not isinstance(parsed, dict):
        return {"reason": str(parsed or "").strip()[:160] or None}
    normalised = normalise_job_error(
        parsed.get("reason"),
        parsed.get("reason_detail"),
    )
    return {
        **normalised,
        "provider": str(parsed.get("provider") or "").strip()[:60] or None,
        "stage": str(parsed.get("stage") or "").strip()[:100] or None,
    }


def get_latest_analysis_job(
    target_type: str,
    target_id: str,
    *,
    derive_method: str | None = None,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    """Return the latest relevant worker state for one analysis target.

    Analysis cache stores successful results only.  Without the job ledger a blocked or
    failed analysis therefore looks "pending" forever.  This read model intentionally
    exposes only bounded display fields and never returns the full payload/error blob.
    """
    active_conn, should_close = _with_connection(conn)
    try:
        clauses = ["payload->>'target_type'=?", "payload->>'target_id'=?"]
        params: list[Any] = [str(target_type).strip(), str(target_id).strip()]
        if derive_method:
            clauses.append("payload->>'derive_method'=?")
            params.append(str(derive_method).strip())
        row = active_conn.execute(
            f"""
            SELECT id, status, last_error, last_error_category,
                   payload->>'execution_class' AS execution_class,
                   created_at, started_at, updated_at
            FROM apify_jobs
            WHERE {" AND ".join(clauses)}
            ORDER BY
                (status IN ('queued', 'running', 'retrying', 'processing')) DESC,
                updated_at DESC,
                id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        status = str(data.get("status") or "").strip().lower()
        if status in _ACTIVE_VIDEO_JOB_STATUSES:
            state = status
        elif status == "blocked":
            state = "blocked"
        elif status in _FAILED_VIDEO_JOB_STATUSES or status == "done":
            state = "failed"
        else:
            state = "pending"
        result = {
            "id": _int_or_none(data.get("id")),
            "status": status or None,
            "state": state,
            "error_category": str(data.get("last_error_category") or "").strip()[:80] or None,
            **_safe_job_error(data.get("last_error")),
            "created_at": data.get("created_at") or None,
            "started_at": data.get("started_at") or None,
            "updated_at": data.get("updated_at") or None,
        }
        if (
            str(data.get("execution_class") or "").strip().lower()
            == LOCAL_EVALUATION_EXECUTION_CLASS
        ):
            result.update(
                {
                    "evaluation_only": True,
                    "production_authorized": False,
                    "claim_status": "descriptive_only",
                    "model_readiness_status": "evaluation_only_not_production_ready",
                }
            )
        return result
    finally:
        if should_close:
            close_standalone_conn(active_conn)


def get_latest_analysis_jobs_for_targets(
    target_type: str,
    target_ids: list[str | int] | tuple[str | int, ...],
    *,
    derive_method: str,
    conn: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the latest durable job for each target with one bounded query."""
    normalized_ids = list(dict.fromkeys(str(value).strip() for value in target_ids if str(value).strip()))[:200]
    method = str(derive_method or "").strip()
    if not normalized_ids or not method:
        return {}
    placeholders = ",".join("?" for _ in normalized_ids)
    active_conn, should_close = _with_connection(conn)
    try:
        rows = active_conn.execute(
            f"""
            WITH ranked AS (
              SELECT id, status, last_error, last_error_category,
                     payload->>'target_id' AS target_id,
                     payload->>'execution_class' AS execution_class,
                     created_at, started_at, updated_at,
                     ROW_NUMBER() OVER (
                       PARTITION BY payload->>'target_id'
                       ORDER BY
                         (status IN ('queued', 'running', 'retrying', 'processing')) DESC,
                         updated_at DESC,
                         id DESC
                     ) AS rn
              FROM apify_jobs
              WHERE payload->>'target_type'=?
                AND payload->>'target_id' IN ({placeholders})
                AND payload->>'derive_method'=?
            )
            SELECT id, status, last_error, last_error_category, target_id,
                   execution_class, created_at, started_at, updated_at
            FROM ranked WHERE rn=1
            """,
            (str(target_type).strip(), *normalized_ids, method),
        ).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            data = dict(row)
            status = str(data.get("status") or "").strip().lower()
            if status in _ACTIVE_VIDEO_JOB_STATUSES:
                state = status
            elif status == "blocked":
                state = "blocked"
            elif status in _FAILED_VIDEO_JOB_STATUSES or status == "done":
                state = "failed"
            else:
                state = "pending"
            item = {
                "id": _int_or_none(data.get("id")),
                "status": status or None,
                "state": state,
                "error_category": str(data.get("last_error_category") or "").strip()[:80] or None,
                **_safe_job_error(data.get("last_error")),
                "created_at": data.get("created_at") or None,
                "started_at": data.get("started_at") or None,
                "updated_at": data.get("updated_at") or None,
            }
            if str(data.get("execution_class") or "").strip().lower() == LOCAL_EVALUATION_EXECUTION_CLASS:
                item.update(
                    {
                        "evaluation_only": True,
                        "production_authorized": False,
                        "claim_status": "descriptive_only",
                        "model_readiness_status": "evaluation_only_not_production_ready",
                    }
                )
            result[str(data.get("target_id") or "")] = item
        return result
    finally:
        if should_close:
            close_standalone_conn(active_conn)


def list_analysis_cache_entries(
    *,
    target_type: str | None = None,
    target_id: str | None = None,
    derive_method: str | None = None,
    status: str | None = None,
    limit: int = 50,
    conn: Any | None = None,
) -> list[AnalysisCacheEntry]:
    """Return cache entries matching simple read filters for UI/review/training use."""
    active_conn, should_close = _with_connection(conn)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("target_type", target_type),
            ("target_id", str(target_id) if target_id is not None else None),
            ("derive_method", derive_method),
            ("status", status),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = active_conn.execute(
            f"""
            SELECT target_type, target_id, derive_method, model, cost, status,
                   triggered_by_user_id, result, created_at, updated_at,
                   prompt_version, model_family
            FROM vkpi_analysis_cache
            {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit or 50), 500))),
        ).fetchall()
        return [_row_to_entry(row) for row in rows]
    finally:
        if should_close:
            close_standalone_conn(active_conn)


def list_project_video_analysis_cache(
    project_id: int,
    *,
    derive_method: str = "video_analysis_final_v1",
    conn: Any | None = None,
) -> ProjectVideoAnalysisCache:
    """Return all project video evidence rows with their ready analysis cache entry."""
    active_conn, should_close = _with_connection(conn)
    try:
        rows = active_conn.execute(
            """
            WITH cache AS (
                SELECT DISTINCT ON (target_id)
                       target_type, target_id, derive_method, model, cost, status,
                       triggered_by_user_id, result, created_at, updated_at
                FROM vkpi_analysis_cache
                WHERE target_type='video'
                  AND derive_method=?
                  AND status='ready'
                ORDER BY target_id, updated_at DESC, id DESC
            ), jobs AS (
                SELECT DISTINCT ON (payload->>'target_id')
                       id,
                       payload->>'target_id' AS target_id,
                       status,
                       last_error,
                       created_at,
                       started_at,
                       updated_at
                FROM apify_jobs
                WHERE job_type='video'
                  AND payload->>'target_type'='video'
                  AND payload->>'derive_method'=?
                ORDER BY
                    payload->>'target_id',
                    (status IN ('queued', 'running', 'retrying', 'processing')) DESC,
                    updated_at DESC,
                    id DESC
            )
            SELECT
                a.id AS assignment_id,
                e.kol_pool_id,
                COALESCE(k.display_name, k.handle, '') AS kol_name,
                k.handle,
                COALESCE(e.platform, k.platform, '') AS platform,
                e.id AS evidence_id,
                e.content_url,
                COALESCE(e.title, e.video_title, e.content_url) AS title,
                e.thumbnail_url,
                e.view_count,
                e.like_count,
                e.comment_count,
                e.publish_date,
                cache.target_type,
                cache.target_id,
                cache.derive_method,
                cache.model,
                cache.cost,
                cache.status,
                cache.triggered_by_user_id,
                cache.result,
                cache.created_at,
                cache.updated_at,
                jobs.id AS job_id,
                jobs.status AS job_status,
                jobs.last_error AS job_last_error,
                jobs.created_at AS job_created_at,
                jobs.started_at AS job_started_at,
                jobs.updated_at AS job_updated_at
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_project_kol_assignments a
              ON a.project_id=e.project_id AND a.kol_pool_id=e.kol_pool_id
            LEFT JOIN vkpi_kol_pool k ON k.id=e.kol_pool_id
            LEFT JOIN cache ON cache.target_id=e.id::text
            LEFT JOIN jobs ON jobs.target_id=e.id::text
            WHERE e.project_id=?
              AND e.is_active IS NOT FALSE
              AND COALESCE(e.evidence_type, 'video')='video'
            ORDER BY
                COALESCE(e.publish_date, e.updated_at, e.created_at) DESC NULLS LAST,
                COALESCE(e.view_count, 0) DESC,
                e.id DESC
            """,
            (derive_method, derive_method, int(project_id)),
        ).fetchall()
        items: list[dict[str, Any]] = []
        state_counts: dict[str, int] = {}
        for row in rows:
            entry = _row_to_entry(row) if row["target_id"] else None
            platform = _video_platform(row["platform"], row["content_url"])
            last_job = _video_job_snapshot(row)
            state, terminal_reason = _project_video_item_state(
                derive_method=derive_method,
                platform=platform,
                entry=entry,
                job=last_job,
            )
            state_counts[state] = state_counts.get(state, 0) + 1
            items.append(
                {
                    "assignment_id": _int_or_none(row["assignment_id"]),
                    "kol_pool_id": _int_or_none(row["kol_pool_id"]),
                    "kol_name": row["kol_name"] or None,
                    "handle": row["handle"] or None,
                    "platform": row["platform"] or platform or None,
                    "evidence_id": _int_or_none(row["evidence_id"]),
                    "content_url": row["content_url"] or None,
                    "title": row["title"] or None,
                    "thumbnail_url": row["thumbnail_url"] or None,
                    "view_count": _int_or_none(row["view_count"]),
                    "like_count": _int_or_none(row["like_count"]),
                    "comment_count": _int_or_none(row["comment_count"]),
                    "publish_date": row["publish_date"] or None,
                    "state": state,
                    "entry": entry,
                    "active_job": last_job if state in _ACTIVE_VIDEO_JOB_STATUSES else None,
                    "last_job": last_job,
                    "terminal_reason": terminal_reason,
                }
            )
        ready_count = state_counts.get("ready", 0)
        active_count = sum(state_counts.get(status, 0) for status in _ACTIVE_VIDEO_JOB_STATUSES)
        return {
            "project_id": int(project_id),
            "derive_method": derive_method,
            "items": items,
            "summary": {
                "evidence_count": len(items),
                "ready_count": ready_count,
                # Backward-compatible name, now truthful: only real active jobs are pending.
                "pending_count": active_count,
                "active_count": active_count,
                "not_requested_count": state_counts.get("not_requested", 0),
                "failed_count": state_counts.get("failed", 0),
                "unsupported_count": state_counts.get("unsupported", 0),
                "state_counts": state_counts,
            },
        }
    finally:
        if should_close:
            close_standalone_conn(active_conn)
