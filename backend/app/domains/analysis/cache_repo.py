"""Thin read repository for unified analysis cache results."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.db.connection import close_standalone_conn, open_standalone_conn


AnalysisCacheEntry = dict[str, Any]
ProjectVideoAnalysisCache = dict[str, Any]


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


def _row_to_entry(row: Any) -> AnalysisCacheEntry:
    return {
        "target_type": str(row["target_type"] or ""),
        "target_id": str(row["target_id"] or ""),
        "derive_method": str(row["derive_method"] or ""),
        "model": row["model"] or None,
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
    conn: Any | None = None,
) -> AnalysisCacheEntry | None:
    """Return the newest cache entry for one target, optionally scoped to a method."""
    active_conn, should_close = _with_connection(conn)
    try:
        clauses = ["target_type=?", "target_id=?"]
        params: list[Any] = [target_type, str(target_id)]
        if derive_method:
            clauses.append("derive_method=?")
            params.append(derive_method)
        row = active_conn.execute(
            f"""
            SELECT target_type, target_id, derive_method, model, cost, status,
                   triggered_by_user_id, result, created_at, updated_at
            FROM vkpi_analysis_cache
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return _row_to_entry(row) if row else None
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
                   triggered_by_user_id, result, created_at, updated_at
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
                cache.updated_at
            FROM vkpi_kol_video_evidence e
            LEFT JOIN vkpi_project_kol_assignments a
              ON a.project_id=e.project_id AND a.kol_pool_id=e.kol_pool_id
            LEFT JOIN vkpi_kol_pool k ON k.id=e.kol_pool_id
            LEFT JOIN cache ON cache.target_id=e.id::text
            WHERE e.project_id=?
              AND e.is_active IS NOT FALSE
              AND COALESCE(e.evidence_type, 'video')='video'
            ORDER BY
                COALESCE(e.publish_date, e.updated_at, e.created_at) DESC NULLS LAST,
                COALESCE(e.view_count, 0) DESC,
                e.id DESC
            """,
            (derive_method, int(project_id)),
        ).fetchall()
        items: list[dict[str, Any]] = []
        ready_count = 0
        for row in rows:
            entry = _row_to_entry(row) if row["target_id"] else None
            if entry:
                ready_count += 1
            items.append(
                {
                    "assignment_id": _int_or_none(row["assignment_id"]),
                    "kol_pool_id": _int_or_none(row["kol_pool_id"]),
                    "kol_name": row["kol_name"] or None,
                    "handle": row["handle"] or None,
                    "platform": row["platform"] or None,
                    "evidence_id": _int_or_none(row["evidence_id"]),
                    "content_url": row["content_url"] or None,
                    "title": row["title"] or None,
                    "thumbnail_url": row["thumbnail_url"] or None,
                    "view_count": _int_or_none(row["view_count"]),
                    "like_count": _int_or_none(row["like_count"]),
                    "comment_count": _int_or_none(row["comment_count"]),
                    "publish_date": row["publish_date"] or None,
                    "state": "ready" if entry else "pending",
                    "entry": entry,
                }
            )
        return {
            "project_id": int(project_id),
            "derive_method": derive_method,
            "items": items,
            "summary": {
                "evidence_count": len(items),
                "ready_count": ready_count,
                "pending_count": len(items) - ready_count,
            },
        }
    finally:
        if should_close:
            close_standalone_conn(active_conn)
