#!/usr/bin/env python3
"""Remove project-scoped metadata from global final_v1 cache results.

Dry-run is the default. ``--commit`` updates only
``vkpi_analysis_cache.result.source`` and removes exactly ``project_id``,
``project_name`` and ``product_name``. The script never calls a provider,
enqueues work, changes cache freshness timestamps, or updates KOL fit fields.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stdout_utils import out  # noqa: E402

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local dependency guard
    load_dotenv = None  # type: ignore[assignment]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402


DERIVE_METHOD = "video_analysis_final_v1"
SOURCE_KEYS = ("project_id", "project_name", "product_name")
BATCH_LIMIT = 5000


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")


def _database_url(key: str) -> str:
    return str(os.environ.get(str(key or "DATABASE_URL"), "")).strip()


def _parse_cache_ids(values: list[str]) -> list[int]:
    parsed: list[int] = []
    for value in values:
        for raw in str(value or "").split(","):
            item = raw.strip()
            if item:
                parsed.append(int(item))
    return sorted({item for item in parsed if item > 0})


def _result_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def clean_result(value: Any) -> tuple[dict[str, Any], list[str]]:
    """Return a copied result with only the three forbidden source keys removed."""

    result = copy.deepcopy(_result_dict(value))
    source = result.get("source")
    if not isinstance(source, dict):
        return result, []
    removed = [key for key in SOURCE_KEYS if key in source]
    if not removed:
        return result, []
    result["source"] = {key: item for key, item in source.items() if key not in SOURCE_KEYS}
    return result, removed


def fetch_rows(conn: Any, *, cache_ids: list[int] | None = None) -> list[dict[str, Any]]:
    ids = sorted({int(item) for item in (cache_ids or []) if int(item) > 0})
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_id, status, result, COUNT(*) OVER() AS total_matching
            FROM vkpi_analysis_cache
            WHERE target_type IN ('video', 'cn_platform_video')
              AND derive_method=%s
              AND jsonb_typeof(result->'source')='object'
              AND (
                    result->'source' ? 'project_id'
                 OR result->'source' ? 'project_name'
                 OR result->'source' ? 'product_name'
              )
              AND (%s::bigint[] IS NULL OR id=ANY(%s::bigint[]))
            ORDER BY id
            LIMIT %s
            """,
            (DERIVE_METHOD, ids or None, ids or None, BATCH_LIMIT),
        )
        return [dict(row) for row in cur.fetchall()]


def build_plan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for row in rows:
        cleaned, removed = clean_result(row.get("result"))
        if not removed:
            continue
        plan.append(
            {
                "cache_id": int(row["id"]),
                "target_id": str(row.get("target_id") or ""),
                "original": _result_dict(row.get("result")),
                "cleaned": cleaned,
                "removed_keys": removed,
            }
        )
    return plan


def _fit_snapshot(conn: Any, cache_ids: list[int]) -> dict[int, Any]:
    if not cache_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT DISTINCT e.kol_pool_id, p.viltrox_fit_score
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e ON e.id::text=c.target_id
            JOIN vkpi_kol_pool p ON p.id=e.kol_pool_id
            WHERE c.id=ANY(%s::bigint[])
            ORDER BY e.kol_pool_id
            """,
            (cache_ids,),
        )
        return {
            int(row["kol_pool_id"]): row.get("viltrox_fit_score")
            for row in cur.fetchall()
        }


def write_plan(conn: Any, plan: list[dict[str, Any]]) -> dict[str, Any]:
    cache_ids = [int(item["cache_id"]) for item in plan]
    cleaned = 0
    concurrent_skipped = 0
    with conn.transaction():
        before = _fit_snapshot(conn, cache_ids)
        with conn.cursor() as cur:
            for item in plan:
                cur.execute(
                    """
                    UPDATE vkpi_analysis_cache
                    SET result=%s
                    WHERE id=%s
                      AND target_type IN ('video', 'cn_platform_video')
                      AND derive_method=%s
                      AND result=%s
                    """,
                    (
                        Jsonb(item["cleaned"]),
                        int(item["cache_id"]),
                        DERIVE_METHOD,
                        Jsonb(item["original"]),
                    ),
                )
                if int(cur.rowcount or 0) == 1:
                    cleaned += 1
                else:
                    concurrent_skipped += 1
        after = _fit_snapshot(conn, cache_ids)
        changed_ids = sorted(
            kol_id for kol_id in set(before) | set(after) if before.get(kol_id) != after.get(kol_id)
        )
        if changed_ids:
            raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    return {
        "cleaned": cleaned,
        "concurrent_skipped": concurrent_skipped,
        "viltrox_fit_score_changed_ids": [],
    }


def run(conn: Any, *, cache_ids: list[int] | None = None, commit: bool = False) -> dict[str, Any]:
    rows = fetch_rows(conn, cache_ids=cache_ids)
    plan = build_plan(rows)
    matching_rows = int(rows[0].get("total_matching") or len(rows)) if rows else 0
    summary: dict[str, Any] = {
        "mode": "commit" if commit else "dry_run",
        "candidates": len(rows),
        "matching_rows": matching_rows,
        "batch_limit": BATCH_LIMIT,
        "has_more": matching_rows > len(rows),
        "would_clean": len(plan),
        "candidate_cache_ids_sample": [int(item["cache_id"]) for item in plan[:50]],
        "candidate_cache_ids_truncated": len(plan) > 50,
        "removed_keys": list(SOURCE_KEYS),
        "cleaned": 0,
        "concurrent_skipped": 0,
        "viltrox_fit_score_changed_ids": [],
        "provider_calls_performed": False,
    }
    if not commit or not plan:
        return summary
    return {**summary, **write_plan(conn, plan)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove project-scoped source metadata from final_v1 cache rows."
    )
    parser.add_argument("--commit", action="store_true", help="Write the cleanup. Omit for dry-run.")
    parser.add_argument(
        "--cache-id",
        action="append",
        default=[],
        help="Limit to comma-separated or repeated cache ids.",
    )
    parser.add_argument(
        "--database-url-key",
        default="DATABASE_URL",
        help="Environment variable holding the database URL.",
    )
    args = parser.parse_args()
    _load_env()
    database_url = _database_url(args.database_url_key)
    if not database_url:
        raise SystemExit(f"missing database URL in env key: {args.database_url_key}")
    with psycopg.connect(database_url, row_factory=dict_row, autocommit=True) as conn:
        summary = run(
            conn,
            cache_ids=_parse_cache_ids(args.cache_id),
            commit=bool(args.commit),
        )
    out(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
