"""Thin read repository for unified analysis cache results."""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.db.connection import close_standalone_conn, open_standalone_conn


AnalysisCacheEntry = dict[str, Any]


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
