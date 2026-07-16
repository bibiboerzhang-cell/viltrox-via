#!/usr/bin/env python3
"""Diagnose scrape scope differences between CSV backlog and vkpi_kol_pool.

Read-only diagnostic:
  - loads a to_scrape_remaining.csv file
  - fuzzy matches CSV KOL names to vkpi_kol_pool.display_name
  - prints bucketed differences for needs_scrape scope
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras
from rapidfuzz import fuzz, process
from stdout_utils import out


DEFAULT_CSV_PATH = Path("data/external/to_scrape_remaining.csv")
FALLBACK_CSV_PATH = Path("/Users/bibiboer/Downloads/vkpi-final/data/to_scrape_remaining.csv")


@dataclass(frozen=True)
class CsvKol:
    row_number: int
    name: str
    products: str
    staff: str
    home_url: str
    platform: str
    product_count: str


@dataclass(frozen=True)
class PoolKol:
    id: int
    handle: str
    display_name: str
    platform: str
    needs_scrape: bool
    has_video_evidence: bool
    video_evidence_count: int
    dashboard_account_type: str
    tier: str


@dataclass(frozen=True)
class MatchResult:
    csv: CsvKol
    pool: PoolKol | None
    score: float
    best_display_name: str


def load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def norm_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s*-?\s*【[^】]+】\s*$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def short(value: str, limit: int = 30) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def bool_from_db(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"t", "true", "1", "yes"}


def resolve_csv_path(raw: str | None) -> Path:
    if raw:
        path = Path(raw).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"CSV not found: {path}")
        return path
    if DEFAULT_CSV_PATH.exists():
        return DEFAULT_CSV_PATH
    if FALLBACK_CSV_PATH.exists():
        return FALLBACK_CSV_PATH
    raise FileNotFoundError(
        f"CSV not found at {DEFAULT_CSV_PATH} or {FALLBACK_CSV_PATH}"
    )


def load_csv(path: Path) -> list[CsvKol]:
    rows: list[CsvKol] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for idx, row in enumerate(reader, start=2):
            name = norm_text(row.get("KOL名"))
            if not name:
                continue
            rows.append(
                CsvKol(
                    row_number=idx,
                    name=name,
                    products=str(row.get("产品列表") or "").strip(),
                    staff=str(row.get("对接人") or "").strip(),
                    home_url=str(row.get("主页URL") or "").strip(),
                    platform=str(row.get("平台") or "").strip(),
                    product_count=str(row.get("产品数") or "").strip(),
                )
            )
    return rows


def connect_db():
    load_env(Path(".env"))
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(database_url)


def load_pool() -> list[PoolKol]:
    query = """
        SELECT
          id,
          COALESCE(handle, '') AS handle,
          COALESCE(display_name, '') AS display_name,
          COALESCE(platform, '') AS platform,
          COALESCE(needs_scrape, FALSE) AS needs_scrape,
          COALESCE(has_video_evidence, FALSE) AS has_video_evidence,
          COALESCE(video_evidence_count, 0) AS video_evidence_count,
          COALESCE(dashboard_account_type, '') AS dashboard_account_type,
          COALESCE(dashboard_tier, '') AS tier
        FROM vkpi_kol_pool
        ORDER BY id
    """
    with connect_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return [
                PoolKol(
                    id=int(row["id"]),
                    handle=str(row["handle"] or ""),
                    display_name=str(row["display_name"] or ""),
                    platform=str(row["platform"] or ""),
                    needs_scrape=bool_from_db(row["needs_scrape"]),
                    has_video_evidence=bool_from_db(row["has_video_evidence"]),
                    video_evidence_count=int(row["video_evidence_count"] or 0),
                    dashboard_account_type=str(row["dashboard_account_type"] or ""),
                    tier=str(row["tier"] or ""),
                )
                for row in cur.fetchall()
            ]


def match_csv_to_pool(csv_rows: list[CsvKol], pool_rows: list[PoolKol]) -> list[MatchResult]:
    candidates: list[tuple[str, PoolKol]] = []
    for pool in pool_rows:
        label = norm_text(pool.display_name)
        if label:
            candidates.append((label, pool))
    names = [item[0] for item in candidates]

    results: list[MatchResult] = []
    for item in csv_rows:
        best = process.extractOne(norm_text(item.name), names, scorer=fuzz.token_sort_ratio)
        if best and float(best[1]) >= 90:
            pool = candidates[int(best[2])][1]
            results.append(
                MatchResult(
                    csv=item,
                    pool=pool,
                    score=float(best[1]),
                    best_display_name=pool.display_name,
                )
            )
        else:
            results.append(
                MatchResult(
                    csv=item,
                    pool=None,
                    score=float(best[1]) if best else 0.0,
                    best_display_name=str(best[0]) if best else "",
                )
            )
    return results


def sample_line(match: MatchResult) -> str:
    pool = match.pool
    display = pool.display_name if pool else match.csv.name
    platform = match.csv.platform or (pool.platform if pool else "")
    return (
        f"{display} | {platform} | {match.csv.staff or '-'} | "
        f"{short(match.csv.products, 30)}"
    )


def print_samples(title: str, rows: list[MatchResult], limit: int = 5) -> None:
    out(title)
    if not rows:
        out("  无")
        return
    for idx, row in enumerate(rows[:limit], start=1):
        out(f"  {idx}. {sample_line(row)}")


def print_unmatched(rows: list[MatchResult]) -> None:
    out("\n[桶 D 完整名单] CSV 有 + 库未匹配到 (fuzzy <90)")
    if not rows:
        out("  无")
        return
    for idx, row in enumerate(rows, start=1):
        out(
            f"  {idx}. CSV={row.csv.name} | 平台={row.csv.platform or '-'} | "
            f"对接人={row.csv.staff or '-'} | 产品={short(row.csv.products, 30)} | "
            f"best={row.best_display_name or '-'} | score={row.score:.1f}"
        )


def print_extra_db(rows: list[PoolKol]) -> None:
    out("\n[桶 E 完整名单] 库 needs_scrape=true + CSV 没有")
    if not rows:
        out("  无")
        return
    for idx, row in enumerate(rows, start=1):
        out(
            f"  {idx}. id={row.id} | {row.display_name or row.handle} | "
            f"handle={row.handle or '-'} | platform={row.platform or '-'} | "
            f"type={row.dashboard_account_type or '-'} | tier={row.tier or '-'} | "
            f"evidence={row.video_evidence_count}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose CSV vs DB scrape scope.")
    parser.add_argument("--csv", default="", help="Path to to_scrape_remaining.csv")
    args = parser.parse_args()

    csv_path = resolve_csv_path(args.csv)
    csv_rows = load_csv(csv_path)
    pool_rows = load_pool()
    matches = match_csv_to_pool(csv_rows, pool_rows)

    matched = [row for row in matches if row.pool is not None]
    matched_pool_ids = {int(row.pool.id) for row in matched if row.pool is not None}

    bucket_a = [row for row in matched if row.pool and row.pool.needs_scrape]
    bucket_b = [
        row
        for row in matched
        if row.pool
        and not row.pool.needs_scrape
        and row.pool.has_video_evidence
    ]
    bucket_c = [
        row
        for row in matched
        if row.pool
        and not row.pool.needs_scrape
        and not row.pool.has_video_evidence
    ]
    bucket_d = [row for row in matches if row.pool is None]
    bucket_e = [
        row
        for row in pool_rows
        if row.needs_scrape and int(row.id) not in matched_pool_ids
    ]

    out("============================================================")
    out("Scrape Scope 诊断报告")
    out("============================================================")
    out(f"CSV: {csv_path}")
    out(f"CSV 有效 KOL 行: {len(csv_rows)}")
    out(f"vkpi_kol_pool 行: {len(pool_rows)}")
    out(f"CSV match>=90: {len(matched)}")
    out(f"CSV fuzzy<90: {len(bucket_d)}")
    out("")
    out(f"[桶 A] CSV 有 + 库匹配到 + needs_scrape=true: {len(bucket_a)} 条")
    out("       -> 一致，本来就在 Step 4 抓取范围里")
    out(f"[桶 B] CSV 有 + 库匹配到 + needs_scrape=false + has_video_evidence=true: {len(bucket_b)} 条")
    out("       -> 库认为已有 evidence 不需要抓；CSV 认为还需要抓")
    out(f"[桶 C] CSV 有 + 库匹配到 + needs_scrape=false + has_video_evidence=false: {len(bucket_c)} 条")
    out("       -> ETL 漏标 needs_scrape；库里没 evidence 也没标待抓")
    out(f"[桶 D] CSV 有 + 库未匹配到 (fuzzy <90): {len(bucket_d)} 条")
    out("       -> 主库没有这个 KOL，名字差异太大或确实新人")
    out(f"[桶 E] 库 needs_scrape=true + CSV 没有: {len(bucket_e)} 条")
    out("       -> 库标了待抓但 CSV 里没有，可能是 ETL 多标")
    out("")
    print_samples("[桶 A top 5]", bucket_a)
    print_samples("\n[桶 B top 5]", bucket_b)
    print_samples("\n[桶 C top 5]", bucket_c)
    print_samples("\n[桶 D top 5]", bucket_d)
    out("")
    print_unmatched(bucket_d)
    print_extra_db(bucket_e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
