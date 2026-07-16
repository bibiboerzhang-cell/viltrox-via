#!/usr/bin/env python3
"""Mark index-side KOL recall readiness statuses.

This writes only vkpi_kol_profile_recall_status. It never touches
vkpi_kol_pool, viltrox_fit_score, V6 Fit, Qdrant, or KOL Pool ordering.
"""

from __future__ import annotations

from stdout_utils import out

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
MIGRATION_NAME = "101_vkpi_kol_profile_recall_status.sql"
COLLECTION_NAME = "vkpi_kol_profile_index_v1"
STATUS_METHOD = "kol_recall_status_v1"

MUSIC_RE = re.compile(
    r"(rick astley|never gonna give you up|official video|music video|\bmv\b|lyrics|official audio|\bsong\b|remaster)",
    re.I,
)
PHOTO_RE = re.compile(
    r"(viltrox|lens|camera|photo|photography|portrait|street|wedding|review|unboxing|bokeh|f/|mm|sony|nikon|"
    r"fuji|lumix|canon|gear|filmmak|cinema)",
    re.I,
)
LOW_TERMS = {
    "none",
    "none.",
    "unknown",
    "unknown due to missing input",
    "无",
    "无。",
}
LOW_PATTERNS = (
    "zero viltrox",
    "0% viltrox",
    "viltrox receives 0%",
    "viltrox is completely absent",
    "completely absent",
    "no viltrox logos",
    "no viltrox products",
    "no physical viltrox",
    "no video content",
    "品牌零曝光",
    "存在感为零",
)
POS_PATTERNS = (
    "high",
    "very high",
    "extremely high",
    "clear",
    "prominent",
    "central",
    "dominant",
    "viltrox logo",
    "brand name",
    "mentioned",
    "visible",
    "极高",
    "清晰",
    "明显",
    "高频",
    "强",
    "持续",
    "明确",
)


@dataclass(frozen=True)
class RecallStatusRow:
    kol_pool_id: int
    status: str
    status_reason: str
    source_counts: dict[str, Any]


def load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip() or "postgresql://viltrox2@127.0.0.1:54329/viltrox2"


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row)


def apply_migration() -> None:
    migration_path = PROJECT_ROOT / "migrations" / MIGRATION_NAME
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version_key TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('viltrox_schema_migrations'))")
            cur.execute("SELECT 1 FROM schema_migrations WHERE version_key = %s", (MIGRATION_NAME,))
            if cur.fetchone():
                out(f"migration already applied: {MIGRATION_NAME}")
                return
            cur.execute(migration_path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations(version_key) VALUES (%s)", (MIGRATION_NAME,))
        conn.commit()
    out(f"migration applied: {MIGRATION_NAME}")


def low_signal(value: str | None) -> bool:
    if not value:
        return False
    lowered = re.sub(r"\s+", " ", value.strip().lower())
    return lowered in LOW_TERMS or any(pattern in lowered for pattern in LOW_PATTERNS)


def positive_signal(value: str | None) -> bool:
    if not value:
        return False
    lowered = re.sub(r"\s+", " ", value.strip().lower())
    return not low_signal(lowered) and any(pattern in lowered for pattern in POS_PATTERNS)


def useful_profile(dimensions: Any) -> bool:
    if not isinstance(dimensions, dict):
        return False
    specialty = dimensions.get("block4_specialty")
    if not isinstance(specialty, dict):
        return False
    product_fit = specialty.get("product_fit") if isinstance(specialty.get("product_fit"), dict) else {}
    industry_cluster = specialty.get("industry_cluster") if isinstance(specialty.get("industry_cluster"), list) else []
    return bool(product_fit or industry_cluster)


def build_status_rows() -> tuple[list[RecallStatusRow], dict[int, dict[str, Any]]]:
    with connect() as conn:
        kols = {
            row["id"]: row
            for row in conn.execute(
                """
                SELECT id, platform, handle, display_name
                FROM vkpi_kol_pool
                ORDER BY id
                """
            )
        }
        index_ids = {
            row["kol_pool_id"]
            for row in conn.execute(
                """
                SELECT DISTINCT kol_pool_id
                FROM vkpi_kol_profile_index_entries
                WHERE collection_name = %s AND status = 'ready'
                """,
                (COLLECTION_NAME,),
            )
        }
        evidence: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in conn.execute(
            """
            SELECT id, kol_pool_id, COALESCE(video_title, title, '') AS title
            FROM vkpi_kol_video_evidence
            WHERE kol_pool_id IS NOT NULL
            """
        ):
            evidence[row["kol_pool_id"]].append(dict(row))

        final_v1: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in conn.execute(
            """
            SELECT
              e.kol_pool_id,
              e.id AS evidence_id,
              COALESCE(e.video_title, e.title, '') AS title,
              c.result #>> '{layer1_visual_content,product_presence}' AS product_presence,
              c.result #>> '{layer1_visual_content,brand_exposure}' AS brand_exposure
            FROM vkpi_analysis_cache c
            JOIN vkpi_kol_video_evidence e ON e.id::text = c.target_id
            WHERE c.status = 'ready' AND c.derive_method = 'video_analysis_final_v1'
            """
        ):
            final_v1[row["kol_pool_id"]].append(dict(row))

        effective_profiles = {
            row["kol_pool_id"]: useful_profile(row["dimensions_11_json"])
            for row in conn.execute("SELECT kol_pool_id, dimensions_11_json FROM vkpi_kol_profile_deep")
        }

    rows: list[RecallStatusRow] = []
    for kol_pool_id in kols:
        evidence_rows = evidence.get(kol_pool_id, [])
        final_rows = final_v1.get(kol_pool_id, [])
        has_effective_profile = bool(effective_profiles.get(kol_pool_id, False))
        source_counts = {
            "collection_name": COLLECTION_NAME,
            "indexed_ready": kol_pool_id in index_ids,
            "evidence_count": len(evidence_rows),
            "final_v1_count": len(final_rows),
            "effective_profile": has_effective_profile,
        }

        if kol_pool_id in index_ids:
            rows.append(
                RecallStatusRow(
                    kol_pool_id=kol_pool_id,
                    status="recallable",
                    status_reason="A: in vkpi_kol_profile_index_v1 ready entries",
                    source_counts=source_counts,
                )
            )
            continue

        suspect_reasons: list[str] = []
        if final_rows:
            negative_rows = []
            positive_rows = []
            for final in final_rows:
                product_presence = final.get("product_presence")
                brand_exposure = final.get("brand_exposure")
                title = final.get("title") or ""
                if low_signal(product_presence) and low_signal(brand_exposure):
                    negative_rows.append(final)
                if positive_signal(product_presence) or positive_signal(brand_exposure) or PHOTO_RE.search(title):
                    positive_rows.append(final)
            if negative_rows and len(negative_rows) == len(final_rows) and not positive_rows:
                suspect_reasons.append("all final_v1 rows say product_presence/brand_exposure none")

        if evidence_rows:
            titles = [row.get("title") or "" for row in evidence_rows if (row.get("title") or "").strip()]
            if titles and all(MUSIC_RE.search(title) for title in titles) and not any(PHOTO_RE.search(title) for title in titles):
                suspect_reasons.append("all evidence titles look music/MV/non-photography")

        if suspect_reasons:
            rows.append(
                RecallStatusRow(
                    kol_pool_id=kol_pool_id,
                    status="suspect",
                    status_reason="B: " + "; ".join(suspect_reasons),
                    source_counts=source_counts,
                )
            )
        elif evidence_rows or final_rows or has_effective_profile:
            bits: list[str] = []
            if evidence_rows:
                bits.append(f"evidence={len(evidence_rows)}")
            if final_rows:
                bits.append(f"final_v1={len(final_rows)}")
            if has_effective_profile:
                bits.append("effective_profile=1")
            rows.append(
                RecallStatusRow(
                    kol_pool_id=kol_pool_id,
                    status="pending_data",
                    status_reason="C: not indexed but has supplementable data (" + ", ".join(bits) + ")",
                    source_counts=source_counts,
                )
            )
        else:
            rows.append(
                RecallStatusRow(
                    kol_pool_id=kol_pool_id,
                    status="empty",
                    status_reason="D: zero evidence, zero final_v1, zero effective profile",
                    source_counts=source_counts,
                )
            )

    return rows, kols


def write_statuses(rows: list[RecallStatusRow]) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO vkpi_kol_profile_recall_status (
                  kol_pool_id,
                  status,
                  status_reason,
                  status_method,
                  scanned_at,
                  source_counts_json
                )
                VALUES (%s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (kol_pool_id) DO UPDATE SET
                  status = EXCLUDED.status,
                  status_reason = EXCLUDED.status_reason,
                  status_method = EXCLUDED.status_method,
                  scanned_at = EXCLUDED.scanned_at,
                  source_counts_json = EXCLUDED.source_counts_json
                """,
                [
                    (
                        row.kol_pool_id,
                        row.status,
                        row.status_reason,
                        STATUS_METHOD,
                        Jsonb(row.source_counts),
                    )
                    for row in rows
                ],
            )
        conn.commit()


def print_report(rows: list[RecallStatusRow], kols: dict[int, dict[str, Any]]) -> None:
    distribution = Counter(row.status for row in rows)
    out("STATUS_WRITE_DISTRIBUTION=" + json.dumps(dict(distribution), ensure_ascii=False, sort_keys=True))
    out("STATUS_WRITE_TOTAL=" + str(sum(distribution.values())))
    sample_by_status: dict[str, list[dict[str, Any]]] = {key: [] for key in ("recallable", "suspect", "pending_data", "empty")}
    for row in rows:
        samples = sample_by_status[row.status]
        if len(samples) >= 2:
            continue
        kol = kols[row.kol_pool_id]
        samples.append(
            {
                "kol_pool_id": row.kol_pool_id,
                "handle": kol.get("handle"),
                "display_name": kol.get("display_name"),
                "platform": kol.get("platform"),
                "status": row.status,
                "status_reason": row.status_reason,
            }
        )
    out("STATUS_WRITE_SAMPLES=" + json.dumps(sample_by_status, ensure_ascii=False, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-migration", action="store_true", help="Apply migration 101 before writing statuses.")
    parser.add_argument("--dry-run", action="store_true", help="Compute statuses without writing.")
    args = parser.parse_args()

    load_dotenv()
    if args.apply_migration:
        apply_migration()
    rows, kols = build_status_rows()
    if not args.dry_run:
        write_statuses(rows)
    print_report(rows, kols)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
