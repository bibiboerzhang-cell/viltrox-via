"""Best-effort AI/provider usage recording for SystemTab."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.core.model_pricing import estimate_cost_usd
from app.db.connection import get_conn, is_postgres_runtime

logger = get_logger(__name__)

_SQLITE_SCHEMA_READY = False


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_sqlite_schema() -> None:
    """Create usage tables for local SQLite demo runs.

    Production Postgres uses migrations/016_ai_usage_log.sql. SQLite does not
    run that migration sequence, so the admin surface needs a local guard.
    """
    global _SQLITE_SCHEMA_READY
    if _SQLITE_SCHEMA_READY or is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT DEFAULT '',
            request_id TEXT DEFAULT '',
            tokens_in INTEGER NOT NULL DEFAULT 0,
            tokens_out INTEGER NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            success INTEGER NOT NULL DEFAULT 1,
            error_code TEXT DEFAULT '',
            called_at TEXT NOT NULL,
            triggered_by TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_ai_usage_provider_called
            ON ai_usage_log(provider, called_at DESC);
        CREATE INDEX IF NOT EXISTS idx_ai_usage_triggered
            ON ai_usage_log(triggered_by, called_at DESC);
        """
    )
    conn.commit()
    _SQLITE_SCHEMA_READY = True


def _tokens_from_payload(payload: Any) -> tuple[int, int]:
    if not isinstance(payload, dict):
        return 0, 0
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else payload
    in_keys = ("tokens_in", "input_tokens", "prompt_tokens", "input_token_count")
    out_keys = ("tokens_out", "output_tokens", "completion_tokens", "output_token_count")
    tokens_in = next((usage.get(key) for key in in_keys if usage.get(key) is not None), 0)
    tokens_out = next((usage.get(key) for key in out_keys if usage.get(key) is not None), 0)
    try:
        in_value = int(tokens_in or 0)
    except (TypeError, ValueError):
        in_value = 0
    try:
        out_value = int(tokens_out or 0)
    except (TypeError, ValueError):
        out_value = 0
    return max(0, in_value), max(0, out_value)


def record_ai_usage(
    *,
    provider: str,
    model: str = "",
    request_id: str = "",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    payload: Any = None,
    latency_ms: int = 0,
    success: bool = True,
    error_code: str = "",
    triggered_by: str = "",
) -> None:
    """Record one provider call without letting observability break product flow."""
    provider_clean = str(provider or "").strip().lower()
    if not provider_clean:
        return
    payload_in, payload_out = _tokens_from_payload(payload)
    in_value = payload_in if tokens_in is None else max(0, int(tokens_in or 0))
    out_value = payload_out if tokens_out is None else max(0, int(tokens_out or 0))
    model_clean = str(model or "").strip()
    model_name = model_clean.split("/", 1)[-1] if "/" in model_clean else model_clean
    cost_usd = estimate_cost_usd(model_name, in_value, out_value)
    try:
        _ensure_sqlite_schema()
        conn = get_conn()
        conn.execute(
            """
            INSERT INTO ai_usage_log
                (provider, model, request_id, tokens_in, tokens_out, cost_usd,
                 latency_ms, success, error_code, called_at, triggered_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_clean,
                model_clean,
                str(request_id or "")[:160],
                in_value,
                out_value,
                cost_usd,
                max(0, int(latency_ms or 0)),
                1 if success else 0,
                str(error_code or "")[:160],
                _utcnow(),
                str(triggered_by or "")[:80],
            ),
        )
        conn.commit()
    except Exception:
        logger.warning("ai_usage.record_failed", exc_info=True, extra={"provider": provider_clean})


def usage_summary(days: int = 7) -> dict[str, Any]:
    _ensure_sqlite_schema()
    days_i = min(90, max(1, int(days or 7)))
    now = datetime.utcnow()
    today_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    range_cutoff = (now - timedelta(days=days_i)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_conn()
    today = conn.execute(
        """
        SELECT
            COUNT(*) AS calls,
            COALESCE(SUM(cost_usd), 0) AS cost_usd,
            COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
            COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS errors
        FROM ai_usage_log
        WHERE called_at >= ?
        """,
        (today_cutoff,),
    ).fetchone()
    by_provider = conn.execute(
        """
        SELECT provider,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               COALESCE(AVG(latency_ms), 0) AS avg_latency_ms,
               COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0) AS errors
        FROM ai_usage_log
        WHERE called_at >= ?
        GROUP BY provider
        ORDER BY calls DESC
        """,
        (range_cutoff,),
    ).fetchall()
    by_task = conn.execute(
        """
        SELECT triggered_by,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               COALESCE(AVG(latency_ms), 0) AS avg_latency_ms
        FROM ai_usage_log
        WHERE called_at >= ?
        GROUP BY triggered_by
        ORDER BY cost_usd DESC
        LIMIT 20
        """,
        (range_cutoff,),
    ).fetchall()
    daily = conn.execute(
        """
        SELECT SUBSTR(called_at, 1, 10) AS day,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd
        FROM ai_usage_log
        WHERE called_at >= ?
        GROUP BY SUBSTR(called_at, 1, 10)
        ORDER BY day ASC
        """,
        (range_cutoff,),
    ).fetchall()
    return {
        "window_days": days_i,
        "today": dict(today) if today else {"calls": 0, "cost_usd": 0, "avg_latency_ms": 0, "errors": 0},
        "by_provider": [dict(row) for row in by_provider],
        "by_task": [dict(row) for row in by_task],
        "daily": [dict(row) for row in daily],
        "cost_basis": "estimated_from_local_ai_usage_log",
    }
