#!/usr/bin/env python3
"""Read final_v1 worker queue/cache/cost status."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.core.config import DB_RUNTIME_URL  # noqa: E402


DERIVE_METHOD = "video_analysis_final_v1"


def _fetch_all(conn: psycopg.Connection[Any], sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or {})
        return [dict(row) for row in cur.fetchall()]


def _print_table(title: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n{title}")
    if not rows:
        print("(none)")
        return
    keys = list(rows[0].keys())
    print("\t".join(keys))
    for row in rows:
        print("\t".join(str(row.get(key) if row.get(key) is not None else "") for key in keys))


def main() -> None:
    parser = argparse.ArgumentParser(description="Show final_v1 analysis worker status.")
    parser.add_argument("--recent-failures", type=int, default=12)
    args = parser.parse_args()
    if not DB_RUNTIME_URL:
        raise SystemExit("DATABASE_URL is required")
    with psycopg.connect(DB_RUNTIME_URL) as conn:
        queue_counts = _fetch_all(
            conn,
            """
            SELECT status, COUNT(*) AS n
            FROM apify_jobs
            WHERE payload->>'derive_method'=%(derive_method)s
            GROUP BY status
            ORDER BY status
            """,
            {"derive_method": DERIVE_METHOD},
        )
        queue_platforms = _fetch_all(
            conn,
            """
            SELECT
              COALESCE(payload->>'platform_by_host', payload->>'platform', 'unknown') AS platform,
              status,
              COUNT(*) AS n
            FROM apify_jobs
            WHERE payload->>'derive_method'=%(derive_method)s
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            {"derive_method": DERIVE_METHOD},
        )
        cache_ready = _fetch_all(
            conn,
            """
            SELECT
              CASE
                WHEN e.content_url ILIKE '%%youtube.com%%' OR e.content_url ILIKE '%%youtu.be%%' THEN 'youtube'
                WHEN e.content_url ILIKE '%%instagram.com%%' THEN 'instagram'
                WHEN e.content_url ILIKE '%%tiktok.com%%' THEN 'tiktok'
                ELSE 'unsupported'
              END AS platform,
              COUNT(*) AS ready_cache_count,
              ROUND(COALESCE(SUM(c.cost), 0), 6) AS cache_cost_usd
            FROM vkpi_analysis_cache c
            LEFT JOIN vkpi_kol_video_evidence e ON e.id::text=c.target_id AND c.target_type='video'
            WHERE c.target_type='video'
              AND c.derive_method=%(derive_method)s
              AND c.status='ready'
            GROUP BY 1
            ORDER BY 1
            """,
            {"derive_method": DERIVE_METHOD},
        )
        score_distribution = _fetch_all(
            conn,
            """
            WITH scored AS (
              SELECT
                c.target_id,
                CASE
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,scores,marketing_value_score}') = 'number'
                    THEN (c.result #>> '{layer6_flags_and_scores,scores,marketing_value_score}')::numeric
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,scores,marketing_value_score}') = 'object'
                    THEN (c.result #>> '{layer6_flags_and_scores,scores,marketing_value_score,score}')::numeric
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,marketing_value_score}') = 'number'
                    THEN (c.result #>> '{layer6_flags_and_scores,marketing_value_score}')::numeric
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,marketing_value_score}') = 'object'
                    THEN (c.result #>> '{layer6_flags_and_scores,marketing_value_score,score}')::numeric
                  ELSE NULL
                END AS marketing_value_score,
                COALESCE(kp.handle, kp.display_name, e.channel_name, '') AS kol,
                CASE
                  WHEN e.content_url ILIKE '%%youtube.com%%' OR e.content_url ILIKE '%%youtu.be%%' THEN 'youtube'
                  WHEN e.content_url ILIKE '%%instagram.com%%' THEN 'instagram'
                  WHEN e.content_url ILIKE '%%tiktok.com%%' THEN 'tiktok'
                  ELSE 'unsupported'
                END AS platform
              FROM vkpi_analysis_cache c
              LEFT JOIN vkpi_kol_video_evidence e ON e.id::text=c.target_id AND c.target_type='video'
              LEFT JOIN vkpi_kol_pool kp ON kp.id=e.kol_pool_id
              WHERE c.target_type='video'
                AND c.derive_method=%(derive_method)s
                AND c.status='ready'
            )
            SELECT COUNT(*) FILTER (WHERE marketing_value_score IS NOT NULL) AS scored_count,
                   MIN(marketing_value_score) AS min_score,
                   MAX(marketing_value_score) AS max_score,
                   ROUND(AVG(marketing_value_score), 2) AS avg_score,
                   COUNT(*) FILTER (WHERE marketing_value_score < 60) AS lt_60,
                   COUNT(*) FILTER (WHERE marketing_value_score >= 60 AND marketing_value_score < 75) AS s60_74,
                   COUNT(*) FILTER (WHERE marketing_value_score >= 75 AND marketing_value_score < 90) AS s75_89,
                   COUNT(*) FILTER (WHERE marketing_value_score >= 90) AS gte_90
            FROM scored
            """,
            {"derive_method": DERIVE_METHOD},
        )
        score_extremes = _fetch_all(
            conn,
            """
            WITH scored AS (
              SELECT
                c.target_id,
                CASE
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,scores,marketing_value_score}') = 'number'
                    THEN (c.result #>> '{layer6_flags_and_scores,scores,marketing_value_score}')::numeric
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,scores,marketing_value_score}') = 'object'
                    THEN (c.result #>> '{layer6_flags_and_scores,scores,marketing_value_score,score}')::numeric
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,marketing_value_score}') = 'number'
                    THEN (c.result #>> '{layer6_flags_and_scores,marketing_value_score}')::numeric
                  WHEN jsonb_typeof(c.result #> '{layer6_flags_and_scores,marketing_value_score}') = 'object'
                    THEN (c.result #>> '{layer6_flags_and_scores,marketing_value_score,score}')::numeric
                  ELSE NULL
                END AS marketing_value_score,
                COALESCE(kp.handle, kp.display_name, e.channel_name, '') AS kol,
                CASE
                  WHEN e.content_url ILIKE '%%youtube.com%%' OR e.content_url ILIKE '%%youtu.be%%' THEN 'youtube'
                  WHEN e.content_url ILIKE '%%instagram.com%%' THEN 'instagram'
                  WHEN e.content_url ILIKE '%%tiktok.com%%' THEN 'tiktok'
                  ELSE 'unsupported'
                END AS platform
              FROM vkpi_analysis_cache c
              LEFT JOIN vkpi_kol_video_evidence e ON e.id::text=c.target_id AND c.target_type='video'
              LEFT JOIN vkpi_kol_pool kp ON kp.id=e.kol_pool_id
              WHERE c.target_type='video'
                AND c.derive_method=%(derive_method)s
                AND c.status='ready'
            ),
            ranked AS (
              SELECT 'lowest' AS rank_type, 1 AS rank_order, marketing_value_score AS sort_score, *
              FROM scored
              WHERE marketing_value_score IS NOT NULL
              ORDER BY marketing_value_score ASC, target_id
              LIMIT 5
            ),
            high AS (
              SELECT 'highest' AS rank_type, 2 AS rank_order, -marketing_value_score AS sort_score, *
              FROM scored
              WHERE marketing_value_score IS NOT NULL
              ORDER BY marketing_value_score DESC, target_id
              LIMIT 5
            )
            SELECT rank_type, target_id, platform, kol, marketing_value_score
            FROM (
              SELECT rank_order, sort_score, rank_type, target_id, platform, kol, marketing_value_score
              FROM ranked
              UNION ALL
              SELECT rank_order, sort_score, rank_type, target_id, platform, kol, marketing_value_score
              FROM high
            ) ordered_scores
            ORDER BY rank_order, sort_score, target_id
            """,
            {"derive_method": DERIVE_METHOD},
        )
        cost_summary = _fetch_all(
            conn,
            """
            SELECT
              COUNT(*) AS ledger_rows,
              ROUND(COALESCE(SUM(cost_usd), 0), 6) AS ledger_cost_usd,
              ROUND(COALESCE(SUM(tokens_in), 0), 0) AS tokens_in,
              ROUND(COALESCE(SUM(tokens_out), 0), 0) AS tokens_out
            FROM vkpi_ai_cost_ledger
            WHERE cron_task='vkpi_analysis_worker'
            """,
        )
        budget = _fetch_all(
            conn,
            """
            SELECT scope, cap_usd, current_spend, warning_at, hard_stop_at, fallback_action
            FROM vkpi_provider_budget_caps
            WHERE scope IN ('cron:vkpi_analysis_worker', 'single_call', 'provider:gemini', 'monthly_total')
            ORDER BY scope
            """,
        )
        recent_failures = _fetch_all(
            conn,
            """
            SELECT id, status, attempts, payload->>'target_id' AS target_id,
                   COALESCE(payload->>'platform_by_host', payload->>'platform', '') AS platform,
                   LEFT(COALESCE(last_error, ''), 500) AS last_error,
                   updated_at
            FROM apify_jobs
            WHERE payload->>'derive_method'=%(derive_method)s
              AND status IN ('failed', 'blocked')
            ORDER BY updated_at DESC, id DESC
            LIMIT %(limit)s
            """,
            {"derive_method": DERIVE_METHOD, "limit": max(1, int(args.recent_failures or 12))},
        )
    _print_table("Queue Status", queue_counts)
    _print_table("Queue By Platform", queue_platforms)
    _print_table("Ready Cache", cache_ready)
    _print_table("Marketing Value Score Distribution", score_distribution)
    _print_table("Marketing Value Score Extremes", score_extremes)
    _print_table("Cost Ledger", cost_summary)
    _print_table("Budget Caps", budget)
    _print_table("Recent Failed/Blocked", recent_failures)


if __name__ == "__main__":
    main()
