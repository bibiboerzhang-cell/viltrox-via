#!/usr/bin/env python3
"""Extract final_v1 video cache into independent KOL LLM deep results.

Dry-run is the default. Use --commit to write.

This script only reads vkpi_analysis_cache/vkpi_kol_video_evidence and writes
vkpi_kol_llm_deep_analysis_results when explicitly committed. It never calls
LLMs, never enqueues worker jobs, and never updates vkpi_kol_pool fit fields.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from stdout_utils import out

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
from psycopg.types.json import Jsonb  # noqa: E402
from app.domains.kol.final_v1_extract import (  # noqa: E402
    ANALYSIS_KIND,
    FINAL_DERIVE_METHOD,
    METHOD,
    PROVIDER,
    QA_DERIVE_METHOD,
    prepare_deep_analysis_projection,
)


@dataclass(frozen=True)
class PreparedResult:
    final_cache_id: int
    kol_pool_id: int
    source_url: str
    source_evidence_id: int
    llm_v6_fit: Decimal | None
    confidence: Decimal | None
    llm_dimensions_11: dict[str, Any]
    action: str
    handle: str
    platform: str
    qa_cache_id: int | None


@dataclass(frozen=True)
class SkippedResult:
    final_cache_id: int
    kol_pool_id: int | None
    source_evidence_id: int | None
    handle: str
    reason: str


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
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://viltrox2@127.0.0.1:54329/viltrox2"


def _connect() -> psycopg.Connection[Any]:
    return psycopg.connect(_database_url(), row_factory=dict_row)


def fetch_rows(conn: psycopg.Connection[Any], *, cache_ids: list[int] | None = None) -> list[dict[str, Any]]:
    cache_ids = sorted(set(int(item) for item in (cache_ids or []) if int(item) > 0))
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            WITH qa AS (
              SELECT q.*,
                     ROW_NUMBER() OVER (PARTITION BY q.target_id ORDER BY q.updated_at DESC, q.id DESC) AS rn
              FROM vkpi_analysis_cache q
              WHERE q.target_type = 'video'
                AND q.derive_method = %(qa_derive_method)s
                AND q.status = 'ready'
            )
            SELECT c.id AS final_cache_id,
                   c.result AS final_result,
                   c.model AS final_model,
                   c.cost AS final_cost,
                   c.updated_at AS final_updated_at,
                   e.id AS evidence_id,
                   e.kol_pool_id,
                   e.content_url,
                   e.title,
                   e.video_title,
                   p.handle,
                   p.display_name,
                   p.platform,
                   qa.id AS qa_cache_id,
                   qa.result AS qa_result,
                   qa.model AS qa_model,
                   qa.cost AS qa_cost,
                   qa.updated_at AS qa_updated_at
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e
              ON e.id::text = c.target_id
             AND c.target_type = 'video'
            JOIN vkpi_kol_pool p
              ON p.id = e.kol_pool_id
            LEFT JOIN qa
              ON qa.target_id = c.target_id
             AND qa.rn = 1
            WHERE c.target_type = 'video'
              AND c.derive_method = %(final_derive_method)s
              AND c.status = 'ready'
              AND (
                %(cache_ids)s::bigint[] IS NULL
                OR c.id = ANY(%(cache_ids)s::bigint[])
              )
            ORDER BY c.id
            """,
            {
                "final_derive_method": FINAL_DERIVE_METHOD,
                "qa_derive_method": QA_DERIVE_METHOD,
                "cache_ids": cache_ids or None,
            },
        )
        return [dict(row) for row in cur.fetchall()]


def _parse_cache_ids(values: list[str]) -> list[int]:
    ids: list[int] = []
    for value in values:
        for item in str(value or "").split(","):
            item = item.strip()
            if not item:
                continue
            ids.append(int(item))
    return sorted(set(ids))


def fetch_existing_by_source_cache(conn: psycopg.Connection[Any]) -> dict[int, list[int]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT source_cache_id, ARRAY_AGG(id ORDER BY id) AS ids
            FROM vkpi_kol_llm_deep_analysis_results
            WHERE analysis_kind = %(analysis_kind)s
              AND source_cache_id IS NOT NULL
            GROUP BY source_cache_id
            """,
            {"analysis_kind": ANALYSIS_KIND},
        )
        return {int(row["source_cache_id"]): [int(item) for item in row["ids"]] for row in cur.fetchall()}


def build_plan(
    rows: list[dict[str, Any]],
    existing: dict[int, list[int]],
) -> tuple[list[PreparedResult], list[SkippedResult]]:
    prepared: list[PreparedResult] = []
    skipped: list[SkippedResult] = []
    duplicate_existing = {cache_id: ids for cache_id, ids in existing.items() if len(ids) > 1}
    if duplicate_existing:
        details = ", ".join(f"{cache_id}:{ids}" for cache_id, ids in sorted(duplicate_existing.items())[:10])
        raise RuntimeError(f"duplicate target rows by source_cache_id; aborting for idempotency: {details}")

    for row in rows:
        cache_id = int(row["final_cache_id"])
        kol_pool_id = int(row["kol_pool_id"]) if row.get("kol_pool_id") is not None else None
        evidence_id = int(row["evidence_id"]) if row.get("evidence_id") is not None else None
        handle = str(row.get("handle") or row.get("display_name") or "")
        if kol_pool_id is None or evidence_id is None:
            skipped.append(SkippedResult(cache_id, kol_pool_id, evidence_id, handle, "missing_kol_or_evidence"))
            continue
        projection = prepare_deep_analysis_projection(row)
        if projection.get("status") != "ready":
            skipped.append(
                SkippedResult(
                    cache_id,
                    kol_pool_id,
                    evidence_id,
                    handle,
                    str(projection.get("reason") or "projection_not_ready"),
                )
            )
            continue
        action = "update" if cache_id in existing else "insert"
        prepared.append(
            PreparedResult(
                final_cache_id=cache_id,
                kol_pool_id=kol_pool_id,
                source_url=str(projection["source_url"]),
                source_evidence_id=evidence_id,
                llm_v6_fit=projection["llm_v6_fit"],
                confidence=projection["confidence"],
                llm_dimensions_11=projection["llm_dimensions_11"],
                action=action,
                handle=handle,
                platform=str(row.get("platform") or ""),
                qa_cache_id=projection["qa_cache_id"],
            )
        )
    return prepared, skipped


def _bucket(score: Decimal | None) -> str:
    if score is None:
        return "unknown"
    if score == 0:
        return "0"
    if score < 40:
        return "1-39"
    if score < 60:
        return "40-59"
    if score < 75:
        return "60-74"
    if score < 90:
        return "75-89"
    return "90-100"


def print_report(
    rows: list[dict[str, Any]],
    prepared: list[PreparedResult],
    skipped: list[SkippedResult],
    *,
    commit: bool = False,
) -> None:
    scores = [item.llm_v6_fit for item in prepared if item.llm_v6_fit is not None]
    action_counts = Counter(item.action for item in prepared)
    platform_counts = Counter(item.platform or "unknown" for item in prepared)
    bucket_counts = Counter(_bucket(item.llm_v6_fit) for item in prepared)
    qa_items = [item for item in prepared if item.qa_cache_id is not None]
    kol_ids = {item.kol_pool_id for item in prepared}
    out("mode: commit (writes enabled)" if commit else "mode: dry-run (no writes)")
    out(f"source final_v1 ready rows: {len(rows)}")
    out(f"would_write: {len(prepared)}")
    out(f"would_insert: {action_counts.get('insert', 0)}")
    out(f"would_update: {action_counts.get('update', 0)}")
    out(f"skipped: {len(skipped)}")
    out(f"with_qa: {len(qa_items)}")
    out(f"score_unknown: {bucket_counts.get('unknown', 0)}")
    out(f"kol_coverage_writable: {len(kol_ids)}")
    if scores:
        avg = sum(scores, Decimal("0")) / Decimal(len(scores))
        out(f"llm_v6_fit_min: {min(scores)}")
        out(f"llm_v6_fit_max: {max(scores)}")
        out(f"llm_v6_fit_avg: {avg.quantize(Decimal('0.01'))}")
    out("score_distribution:")
    for key in ["unknown", "0", "1-39", "40-59", "60-74", "75-89", "90-100"]:
        out(f"  {key}: {bucket_counts.get(key, 0)}")
    out("platform_distribution:")
    for key, value in sorted(platform_counts.items()):
        out(f"  {key}: {value}")
    out("qa_rows:")
    if qa_items:
        for item in qa_items:
            out(
                f"  evidence={item.source_evidence_id} kol={item.kol_pool_id} "
                f"handle={item.handle} final_cache={item.final_cache_id} qa_cache={item.qa_cache_id} "
                f"score={item.llm_v6_fit}"
            )
    else:
        out("  (none)")
    out("skipped_rows:")
    if skipped:
        for item in skipped:
            out(
                f"  final_cache={item.final_cache_id} evidence={item.source_evidence_id} "
                f"kol={item.kol_pool_id} handle={item.handle} reason={item.reason}"
            )
    else:
        out("  (none)")
    out("sample_writes:")
    for item in sorted(prepared, key=lambda row: (row.source_evidence_id, row.final_cache_id))[:12]:
        out(
            f"  {item.action} final_cache={item.final_cache_id} evidence={item.source_evidence_id} "
            f"kol={item.kol_pool_id} handle={item.handle} score={item.llm_v6_fit} "
            f"confidence={item.confidence if item.confidence is not None else ''}"
        )


def _fit_snapshot(conn: psycopg.Connection[Any], kol_ids: list[int]) -> dict[int, Any]:
    if not kol_ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, viltrox_fit_score
            FROM vkpi_kol_pool
            WHERE id = ANY(%s::bigint[])
            """,
            (kol_ids,),
        )
        return {int(row["id"]): row["viltrox_fit_score"] for row in cur.fetchall()}


def write_results(conn: psycopg.Connection[Any], prepared: list[PreparedResult], existing: dict[int, list[int]]) -> dict[str, Any]:
    kol_ids = sorted({item.kol_pool_id for item in prepared})
    before = _fit_snapshot(conn, kol_ids)
    inserted = 0
    updated = 0
    with conn.cursor(row_factory=dict_row) as cur:
        for item in prepared:
            params = {
                "kol_pool_id": item.kol_pool_id,
                "source_url": item.source_url,
                "source_evidence_id": item.source_evidence_id,
                "analysis_kind": ANALYSIS_KIND,
                "llm_v6_fit": item.llm_v6_fit,
                "llm_dimensions_11": Jsonb(item.llm_dimensions_11),
                "method": METHOD,
                "provider": PROVIDER,
                "confidence": item.confidence,
                "source_cache_id": item.final_cache_id,
                "status": "ready",
            }
            existing_ids = existing.get(item.final_cache_id, [])
            if existing_ids:
                cur.execute(
                    """
                    UPDATE vkpi_kol_llm_deep_analysis_results
                    SET kol_pool_id = %(kol_pool_id)s,
                        source_url = %(source_url)s,
                        source_evidence_id = %(source_evidence_id)s,
                        analysis_kind = %(analysis_kind)s,
                        llm_v6_fit = %(llm_v6_fit)s,
                        llm_dimensions_11 = %(llm_dimensions_11)s,
                        method = %(method)s,
                        provider = %(provider)s,
                        confidence = %(confidence)s,
                        source_cache_id = %(source_cache_id)s,
                        status = %(status)s
                    WHERE id = %(id)s
                    """,
                    {**params, "id": existing_ids[0]},
                )
                updated += cur.rowcount
            else:
                cur.execute(
                    """
                    INSERT INTO vkpi_kol_llm_deep_analysis_results (
                        kol_pool_id,
                        source_url,
                        source_evidence_id,
                        analysis_kind,
                        llm_v6_fit,
                        llm_dimensions_11,
                        method,
                        provider,
                        confidence,
                        source_cache_id,
                        status
                    ) VALUES (
                        %(kol_pool_id)s,
                        %(source_url)s,
                        %(source_evidence_id)s,
                        %(analysis_kind)s,
                        %(llm_v6_fit)s,
                        %(llm_dimensions_11)s,
                        %(method)s,
                        %(provider)s,
                        %(confidence)s,
                        %(source_cache_id)s,
                        %(status)s
                    )
                    """,
                    params,
                )
                inserted += cur.rowcount
    after = _fit_snapshot(conn, kol_ids)
    changed_ids = [kol_id for kol_id in kol_ids if before.get(kol_id) != after.get(kol_id)]
    if changed_ids:
        conn.rollback()
        raise RuntimeError(f"viltrox_fit_score_changed_ids={changed_ids}; rolled back")
    conn.commit()
    return {"inserted": inserted, "updated": updated, "viltrox_fit_score_changed_ids": changed_ids}


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill independent KOL deep analysis results from final_v1 cache.")
    parser.add_argument("--cache-id", action="append", default=[], help="Limit to one or more final_v1 cache ids; comma-separated or repeated.")
    parser.add_argument("--commit", action="store_true", help="Write results. Omit for dry-run.")
    args = parser.parse_args()
    _load_env()
    with _connect() as conn:
        cache_ids = _parse_cache_ids(args.cache_id)
        rows = fetch_rows(conn, cache_ids=cache_ids)
        existing = fetch_existing_by_source_cache(conn)
        prepared, skipped = build_plan(rows, existing)
        print_report(rows, prepared, skipped, commit=bool(args.commit))
        if not args.commit:
            return
        result = write_results(conn, prepared, existing)
        out("write_result:")
        out(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
