#!/usr/bin/env python3
"""Materialize account-level profile_llm extracts from local KOL dossier data.

Dry-run is the default. Use --commit to write.

This script only reads local KOL Pool, video evidence, analysis cache, deep
results, and crawl history. When committed, it writes independent
vkpi_kol_llm_deep_analysis_results rows with analysis_kind='profile_llm'. It
never calls providers/LLMs, never enqueues worker jobs, and never updates
vkpi_kol_pool fit fields.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local dependency guard.
    load_dotenv = None  # type: ignore[assignment]

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.domains.kol.account_dossier_extract import (  # noqa: E402
    METHOD,
    ANALYSIS_KIND,
    prepare_account_dossier_extract,
    upsert_account_dossier_extract,
)


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(ROOT / ".env")
        return
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://postgres@127.0.0.1:54329/viltrox2"


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(_database_url(), row_factory=dict_row)


def _parse_ids(values: list[str]) -> list[int]:
    ids: list[int] = []
    for value in values:
        for item in str(value or "").split(","):
            item = item.strip()
            if not item:
                continue
            ids.append(int(item))
    return sorted(set(ids))


def _candidate_ids(
    conn: psycopg.Connection[Any],
    *,
    include_video_only: bool,
    limit: int | None,
) -> list[int]:
    if include_video_only:
        sql = """
            SELECT DISTINCT e.kol_pool_id
            FROM vkpi_kol_video_evidence e
            WHERE e.kol_pool_id IS NOT NULL
              AND e.is_active IS NOT FALSE
              AND COALESCE(e.evidence_type, 'video')='video'
            ORDER BY e.kol_pool_id
        """
    else:
        sql = """
            WITH final AS (
              SELECT DISTINCT e.kol_pool_id
              FROM vkpi_analysis_cache c
              JOIN vkpi_kol_video_evidence e
                ON e.id::text=c.target_id
               AND c.target_type='video'
              WHERE c.derive_method='video_analysis_final_v1'
                AND c.status='ready'
            ),
            deep AS (
              SELECT DISTINCT kol_pool_id
              FROM vkpi_kol_llm_deep_analysis_results
              WHERE analysis_kind='video_final_v1'
                AND status='ready'
            )
            SELECT DISTINCT kol_pool_id
            FROM (
              SELECT kol_pool_id FROM final
              UNION ALL
              SELECT kol_pool_id FROM deep
            ) src
            WHERE kol_pool_id IS NOT NULL
            ORDER BY kol_pool_id
        """
    if limit:
        sql += "\nLIMIT %s"
        params: tuple[Any, ...] = (int(limit),)
    else:
        params = ()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [int(row["kol_pool_id"]) for row in cur.fetchall()]


def _existing_profile_llm_ids(conn: psycopg.Connection[Any], kol_pool_id: int) -> list[int]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE kol_pool_id=%s
              AND analysis_kind=%s
              AND method=%s
              AND status='ready'
            ORDER BY id DESC
            """,
            (int(kol_pool_id), ANALYSIS_KIND, METHOD),
        )
        return [int(row["id"]) for row in cur.fetchall()]


def _score_bucket(value: Any) -> str:
    if value is None:
        return "null"
    number = float(value)
    if number <= 0:
        return "0"
    if number < 40:
        return "1-39"
    if number < 60:
        return "40-59"
    if number < 75:
        return "60-74"
    if number < 90:
        return "75-89"
    return "90-100"


def build_plan(conn: psycopg.Connection[Any], kol_ids: list[int]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for kol_id in kol_ids:
        prepared = prepare_account_dossier_extract(int(kol_id))
        existing_ids = _existing_profile_llm_ids(conn, int(kol_id))
        entry: dict[str, Any] = {
            "kol_pool_id": int(kol_id),
            "status": prepared.get("status"),
            "reason": prepared.get("reason"),
            "existing_ids": existing_ids,
            "action": "update" if existing_ids else "insert",
        }
        if prepared.get("status") == "ready":
            summary = prepared.get("summary") if isinstance(prepared.get("summary"), dict) else {}
            entry.update(
                {
                    "llm_v6_fit": summary.get("llm_v6_fit"),
                    "confidence": summary.get("confidence"),
                    "video_count": summary.get("video_count"),
                    "analyzed_final_v1_count": summary.get("analyzed_final_v1_count"),
                    "qa_count": summary.get("qa_count"),
                    "deep_result_count": summary.get("deep_result_count"),
                    "gaps": summary.get("gaps") or [],
                    "source_url": prepared.get("source_url"),
                }
            )
        else:
            entry["action"] = "skip"
        plan.append(entry)
    return plan


def print_report(plan: list[dict[str, Any]], *, commit: bool) -> None:
    ready = [item for item in plan if item.get("status") == "ready"]
    skipped = [item for item in plan if item.get("status") != "ready"]
    action_counts = Counter(item.get("action") for item in ready)
    score_buckets = Counter(_score_bucket(item.get("llm_v6_fit")) for item in ready)
    gap_counts: Counter[str] = Counter()
    for item in ready:
        for gap in item.get("gaps") or []:
            gap_counts[str(gap)] += 1
    print(f"mode: {'commit' if commit else 'dry-run (no writes)'}")
    print(f"analysis_kind: {ANALYSIS_KIND}")
    print(f"method: {METHOD}")
    print(f"candidates: {len(plan)}")
    print(f"ready: {len(ready)}")
    print(f"skipped: {len(skipped)}")
    print(f"would_insert: {action_counts.get('insert', 0)}")
    print(f"would_update: {action_counts.get('update', 0)}")
    print("score_distribution:")
    for key in ("null", "0", "1-39", "40-59", "60-74", "75-89", "90-100"):
        print(f"  {key}: {score_buckets.get(key, 0)}")
    print("top_gaps:")
    for key, value in gap_counts.most_common(12):
        print(f"  {key}: {value}")
    print("sample_ready:")
    for item in ready[:20]:
        print(
            "  "
            f"{item.get('action')} kol={item.get('kol_pool_id')} "
            f"score={item.get('llm_v6_fit')} confidence={item.get('confidence')} "
            f"videos={item.get('video_count')} analyzed={item.get('analyzed_final_v1_count')} "
            f"qa={item.get('qa_count')} deep={item.get('deep_result_count')}"
        )
    print("sample_skipped:")
    for item in skipped[:20]:
        print(f"  kol={item.get('kol_pool_id')} reason={item.get('reason') or item.get('status')}")


def write_plan(conn: psycopg.Connection[Any], plan: list[dict[str, Any]]) -> dict[str, Any]:
    inserted = 0
    updated = 0
    skipped = 0
    changed_ids: list[int] = []
    for item in plan:
        if item.get("status") != "ready":
            skipped += 1
            continue
        result = upsert_account_dossier_extract(conn, int(item["kol_pool_id"]))
        action = str(result.get("action") or "")
        if action == "inserted":
            inserted += 1
        elif action == "updated":
            updated += 1
        else:
            skipped += 1
        changed_ids.extend(int(value) for value in result.get("viltrox_fit_score_changed_ids") or [])
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "viltrox_fit_score_changed_ids": sorted(set(changed_ids)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill account-level profile_llm extracts from local KOL dossier data.")
    parser.add_argument("--kol-id", action="append", default=[], help="KOL Pool id, comma-separated or repeated.")
    parser.add_argument("--include-video-only", action="store_true", help="Include KOLs with video evidence but no final_v1/deep result.")
    parser.add_argument("--limit", type=int, default=None, help="Limit auto-selected candidates.")
    parser.add_argument("--json", action="store_true", help="Print full plan JSON after the summary.")
    parser.add_argument("--commit", action="store_true", help="Write profile_llm rows. Omit for dry-run.")
    args = parser.parse_args()

    _load_env()
    with _connect() as conn:
        kol_ids = _parse_ids(args.kol_id)
        if not kol_ids:
            kol_ids = _candidate_ids(conn, include_video_only=bool(args.include_video_only), limit=args.limit)
        plan = build_plan(conn, kol_ids)
        print_report(plan, commit=bool(args.commit))
        if args.json:
            print("plan_json:")
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        if not args.commit:
            return
        result = write_plan(conn, plan)
        conn.commit()
        print("write_result:")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
