#!/usr/bin/env python3
"""Lint vkpi_kol_pool profile URLs for needs_scrape KOLs.

Read-only diagnostic. Prints bucketed URL hygiene for current
needs_scrape=TRUE rows. It does not write files or update the database.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras


BUCKETS = [
    ("clean_youtube_channel", "桶 1 - clean_youtube_channel"),
    ("clean_instagram", "桶 2 - clean_instagram"),
    ("clean_tiktok", "桶 3 - clean_tiktok"),
    ("clean_facebook", "桶 4 - clean_facebook"),
    ("media_site", "桶 5 - media_site"),
    ("youtube_video_url", "桶 6 - youtube_video_url"),
    ("empty_or_null", "桶 7 - empty_or_null"),
    ("other_unparseable", "桶 8 - other_unparseable"),
]


@dataclass(frozen=True)
class PoolRow:
    id: int
    display_name: str
    dashboard_account_type: str
    handle: str
    profile_url: str


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def text(value: Any) -> str:
    return str(value or "").strip()


def connect_db():
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def load_rows() -> list[PoolRow]:
    query = """
        SELECT id, display_name, dashboard_account_type, handle, profile_url
        FROM vkpi_kol_pool
        WHERE needs_scrape = TRUE
        ORDER BY id
    """
    with connect_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            return [
                PoolRow(
                    id=int(row["id"]),
                    display_name=text(row["display_name"]),
                    dashboard_account_type=text(row["dashboard_account_type"]),
                    handle=text(row["handle"]),
                    profile_url=text(row["profile_url"]),
                )
                for row in cur.fetchall()
            ]


def classify_url(url: str) -> str:
    raw = text(url)
    normalized = raw.lower().strip()
    if not normalized or normalized in {"https://", "http://", "none", "null", "-"}:
        return "empty_or_null"
    if re.match(r"^https?://(www\.)?youtube\.com/watch\?v=[^&#?/]+", normalized):
        return "youtube_video_url"
    if re.match(r"^https?://(www\.)?youtube\.com/@[\w\-.]+/?(videos)?/?$", normalized):
        return "clean_youtube_channel"
    if re.match(r"^https?://(www\.)?youtube\.com/channel/uc[\w-]+/?(videos)?/?$", normalized):
        return "clean_youtube_channel"
    if re.match(r"^https?://(www\.)?youtube\.com/c/[\w\-.]+/?(videos)?/?$", normalized):
        return "clean_youtube_channel"
    if re.match(r"^https?://(www\.)?youtube\.com/user/[\w\-.]+/?(videos)?/?$", normalized):
        return "clean_youtube_channel"
    if re.match(r"^https?://(www\.)?instagram\.com/[a-z0-9_.]+/?$", normalized):
        return "clean_instagram"
    if re.match(r"^https?://(www\.)?tiktok\.com/@[\w\-.]+/?$", normalized):
        return "clean_tiktok"
    if re.match(r"^https?://(www\.)?facebook\.com/[^/?#]+/?$", normalized):
        return "clean_facebook"
    if re.match(r"^https?://", normalized):
        return "media_site"
    return "other_unparseable"


def print_bucket(label: str, rows: list[PoolRow]) -> None:
    print(f"[{label}] {len(rows)} 条")
    if not rows:
        print("  无")
        print("")
        return
    for row in rows:
        print(f"  {row.id} | {row.display_name} | {row.handle} | {row.profile_url}")
    print("")


def expected_type_for_bucket(bucket: str) -> str | None:
    mapping = {
        "clean_youtube_channel": "youtube",
        "clean_instagram": "instagram",
        "clean_tiktok": "tiktok",
        "clean_facebook": "facebook",
    }
    return mapping.get(bucket)


def main() -> int:
    rows = load_rows()
    grouped: dict[str, list[PoolRow]] = {key: [] for key, _ in BUCKETS}
    for row in rows:
        grouped[classify_url(row.profile_url)].append(row)

    print("============================================================")
    print("vkpi_kol_pool channel URL lint report")
    print("============================================================")
    print(f"needs_scrape=TRUE rows: {len(rows)}")
    print("URL fields detected: profile_url")
    print("")

    for key, label in BUCKETS:
        print_bucket(label, grouped[key])

    print("汇总表")
    print("| 桶 | 数量 | Step 4a 适用？ | Step 4b/4c 处理？|")
    print("| --- | ---: | --- | --- |")
    summary = {
        "clean_youtube_channel": ("✅", "-"),
        "clean_instagram": ("❌ Step 4b", "✅"),
        "clean_tiktok": ("❌ Step 4b", "✅"),
        "clean_facebook": ("❌ Step 4b", "✅"),
        "media_site": ("❌ Step 4c", "✅"),
        "youtube_video_url": ("⚠️ 单视频要特殊处理", "-"),
        "empty_or_null": ("❌ 数据缺失", "需补"),
        "other_unparseable": ("❌ 人工", "需补"),
    }
    for key, _ in BUCKETS:
        step4a, later = summary[key]
        print(f"| {key} | {len(grouped[key])} | {step4a} | {later} |")

    conflicts: list[tuple[PoolRow, str, str]] = []
    for key, bucket_rows in grouped.items():
        expected = expected_type_for_bucket(key)
        if not expected:
            continue
        for row in bucket_rows:
            actual = row.dashboard_account_type.lower()
            if actual != expected:
                conflicts.append((row, expected, row.dashboard_account_type or "-"))

    print("")
    print("type 冲突清单")
    if not conflicts:
        print("  无")
    else:
        for row, expected, actual in conflicts:
            print(
                f"  {row.id} | {row.display_name} | expected={expected} | "
                f"actual={actual} | {row.profile_url}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
