#!/usr/bin/env python3
"""Read-only V-KPI ops health skeleton.

This is intentionally not wired to any scheduler or alert sink. It prints the
three S2/N3 alert primitives for human review:
- worker heartbeat
- recent failure rate
- daily cost
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any


import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

DB_RUNTIME_URL = os.getenv("DATABASE_URL", "").strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _status(*, danger: bool = False, warning: bool = False) -> str:
    if danger:
        return "danger"
    if warning:
        return "warning"
    return "ok"


def _fetch_one(conn: psycopg.Connection[Any], sql: str, params: dict[str, Any]) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row or {})


def _fetch_all(conn: psycopg.Connection[Any], sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def worker_heartbeat_section(row: dict[str, Any], *, stale_minutes: int) -> dict[str, Any]:
    running = _as_int(row.get("running_count"))
    stale = _as_int(row.get("stale_running_count"))
    max_age_seconds = _as_float(row.get("max_running_age_seconds"))
    stale_seconds = max(1, stale_minutes) * 60
    return {
        "status": _status(danger=stale > 0, warning=running > 0 and max_age_seconds > stale_seconds * 0.8),
        "running_count": running,
        "stale_running_count": stale,
        "max_running_age_seconds": round(max_age_seconds, 3),
        "stale_after_seconds": stale_seconds,
    }


def failure_rate_section(
    row: dict[str, Any],
    categories: list[dict[str, Any]],
    *,
    warning_rate: float,
    danger_rate: float,
) -> dict[str, Any]:
    done = _as_int(row.get("done_count"))
    failed = _as_int(row.get("failed_count"))
    blocked = _as_int(row.get("blocked_count"))
    provider_pressure = _as_int(row.get("provider_pressure_count"))
    total = done + failed + blocked
    rate = (failed + blocked) / total if total else 0.0
    return {
        "status": _status(danger=total > 0 and rate >= danger_rate, warning=total > 0 and rate >= warning_rate),
        "window_terminal_count": total,
        "done_count": done,
        "failed_count": failed,
        "blocked_count": blocked,
        "failure_rate": round(rate, 6),
        "warning_rate": warning_rate,
        "danger_rate": danger_rate,
        "provider_pressure_count": provider_pressure,
        "by_error_category": categories,
    }


def cost_daily_section(row: dict[str, Any], *, warning_usd: float, danger_usd: float) -> dict[str, Any]:
    ai_ledger_cost = _as_float(row.get("ai_ledger_cost_usd"))
    llm_calls_cost = _as_float(row.get("llm_calls_cost_usd"))
    analysis_cache_cost = _as_float(row.get("analysis_cache_cost_usd"))
    visible_total = ai_ledger_cost + analysis_cache_cost
    return {
        "status": _status(danger=visible_total >= danger_usd, warning=visible_total >= warning_usd),
        "ai_ledger_cost_usd": round(ai_ledger_cost, 6),
        "llm_calls_cost_usd": round(llm_calls_cost, 6),
        "analysis_cache_cost_usd": round(analysis_cache_cost, 6),
        "visible_total_usd": round(visible_total, 6),
        "warning_usd": warning_usd,
        "danger_usd": danger_usd,
    }


def overall_status(sections: dict[str, dict[str, Any]]) -> str:
    statuses = {str(section.get("status") or "ok") for section in sections.values()}
    if "danger" in statuses:
        return "danger"
    if "warning" in statuses:
        return "warning"
    return "ok"


def build_payload(
    *,
    worker_row: dict[str, Any],
    failure_row: dict[str, Any],
    error_categories: list[dict[str, Any]],
    cost_row: dict[str, Any],
    hours: int,
    stale_minutes: int,
    failure_warning_rate: float,
    failure_danger_rate: float,
    cost_warning_usd: float,
    cost_danger_usd: float,
) -> dict[str, Any]:
    sections = {
        "worker_heartbeat": worker_heartbeat_section(worker_row, stale_minutes=stale_minutes),
        "failure_rate": failure_rate_section(
            failure_row,
            error_categories,
            warning_rate=failure_warning_rate,
            danger_rate=failure_danger_rate,
        ),
        "cost_daily": cost_daily_section(cost_row, warning_usd=cost_warning_usd, danger_usd=cost_danger_usd),
    }
    return {
        "status": overall_status(sections),
        "method": "vkpi_ops_health_readonly_v1",
        "window_hours": hours,
        "sections": sections,
        "diagnostics": {
            "provider_calls": False,
            "write_db": False,
            "deploy": False,
            "tables_read": ["apify_jobs", "vkpi_ai_cost_ledger", "vkpi_llm_calls", "vkpi_analysis_cache"],
        },
    }


def read_health(
    *,
    hours: int = 24,
    stale_minutes: int = 20,
    failure_warning_rate: float = 0.15,
    failure_danger_rate: float = 0.35,
    cost_warning_usd: float = 10.0,
    cost_danger_usd: float = 30.0,
) -> dict[str, Any]:
    if not DB_RUNTIME_URL:
        raise SystemExit("DATABASE_URL is required")
    params = {"hours": max(1, int(hours)), "stale_minutes": max(1, int(stale_minutes))}
    with psycopg.connect(DB_RUNTIME_URL) as conn:
        worker_row = _fetch_one(
            conn,
            """
            SELECT
              COUNT(*) FILTER (WHERE status='running') AS running_count,
              COUNT(*) FILTER (
                WHERE status='running'
                  AND updated_at < NOW() - make_interval(mins => %(stale_minutes)s)
              ) AS stale_running_count,
              COALESCE(MAX(EXTRACT(EPOCH FROM (NOW() - updated_at))) FILTER (WHERE status='running'), 0) AS max_running_age_seconds
            FROM apify_jobs
            """,
            params,
        )
        failure_row = _fetch_one(
            conn,
            """
            SELECT
              COUNT(*) FILTER (WHERE status='done') AS done_count,
              COUNT(*) FILTER (WHERE status='failed') AS failed_count,
              COUNT(*) FILTER (WHERE status='blocked') AS blocked_count,
              COUNT(*) FILTER (WHERE last_error_category='provider_pressure') AS provider_pressure_count
            FROM apify_jobs
            WHERE updated_at >= NOW() - make_interval(hours => %(hours)s)
              AND status IN ('done', 'failed', 'blocked')
            """,
            params,
        )
        error_categories = _fetch_all(
            conn,
            """
            SELECT COALESCE(NULLIF(last_error_category, ''), 'unknown') AS category,
                   COUNT(*) AS count
            FROM apify_jobs
            WHERE updated_at >= NOW() - make_interval(hours => %(hours)s)
              AND status IN ('failed', 'blocked')
            GROUP BY 1
            ORDER BY count DESC, category ASC
            """,
            params,
        )
        cost_row = _fetch_one(
            conn,
            """
            SELECT
              (SELECT COALESCE(SUM(cost_usd), 0)
                 FROM vkpi_ai_cost_ledger
                WHERE occurred_at >= NOW() - make_interval(hours => %(hours)s)) AS ai_ledger_cost_usd,
              (SELECT COALESCE(SUM(cost_cents), 0)::numeric / 100.0
                 FROM vkpi_llm_calls
                WHERE created_at >= NOW() - make_interval(hours => %(hours)s)) AS llm_calls_cost_usd,
              (SELECT COALESCE(SUM(cost), 0)
                 FROM vkpi_analysis_cache
                WHERE updated_at >= NOW() - make_interval(hours => %(hours)s)
                  AND status='ready') AS analysis_cache_cost_usd
            """,
            params,
        )
    return build_payload(
        worker_row=worker_row,
        failure_row=failure_row,
        error_categories=error_categories,
        cost_row=cost_row,
        hours=max(1, int(hours)),
        stale_minutes=max(1, int(stale_minutes)),
        failure_warning_rate=failure_warning_rate,
        failure_danger_rate=failure_danger_rate,
        cost_warning_usd=cost_warning_usd,
        cost_danger_usd=cost_danger_usd,
    )


def format_markdown(payload: dict[str, Any]) -> str:
    sections = payload.get("sections") if isinstance(payload.get("sections"), dict) else {}
    worker = sections.get("worker_heartbeat") if isinstance(sections.get("worker_heartbeat"), dict) else {}
    failure = sections.get("failure_rate") if isinstance(sections.get("failure_rate"), dict) else {}
    cost = sections.get("cost_daily") if isinstance(sections.get("cost_daily"), dict) else {}
    return "\n".join(
        [
            "# V-KPI Ops Health",
            "",
            "```text",
            f"status={payload.get('status')}",
            f"window_hours={payload.get('window_hours')}",
            f"worker.status={worker.get('status')}",
            f"worker.running={worker.get('running_count')}",
            f"worker.stale_running={worker.get('stale_running_count')}",
            f"failure.status={failure.get('status')}",
            f"failure.rate={failure.get('failure_rate')}",
            f"failure.provider_pressure={failure.get('provider_pressure_count')}",
            f"cost.status={cost.get('status')}",
            f"cost.visible_total_usd={cost.get('visible_total_usd')}",
            "```",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print read-only V-KPI ops health.")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--stale-minutes", type=int, default=20)
    parser.add_argument("--failure-warning-rate", type=float, default=0.15)
    parser.add_argument("--failure-danger-rate", type=float, default=0.35)
    parser.add_argument("--cost-warning-usd", type=float, default=10.0)
    parser.add_argument("--cost-danger-usd", type=float, default=30.0)
    parser.add_argument("--markdown", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = read_health(
        hours=args.hours,
        stale_minutes=args.stale_minutes,
        failure_warning_rate=args.failure_warning_rate,
        failure_danger_rate=args.failure_danger_rate,
        cost_warning_usd=args.cost_warning_usd,
        cost_danger_usd=args.cost_danger_usd,
    )
    if args.markdown:
        print(format_markdown(payload), end="")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
