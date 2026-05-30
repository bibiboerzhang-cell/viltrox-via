#!/usr/bin/env python3
"""Dashboard KOL Picker Phase 1A ETL.

Classifies the current V-KPI account pool without invoking any provider:
- vkpi_kol_pool rows become kol/media candidates.
- official company accounts are read from vkpi_employee_channels.
- --dry-run reports the numbers only.
- --commit writes dashboard_* fields back to vkpi_kol_pool.
"""

from __future__ import annotations

import argparse
import os
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None
    dict_row = None

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover
    psycopg2 = None
    RealDictCursor = None


MEDIA_HANDLES = {
    "35mmc",
    "admiringlight",
    "amateurphotographer",
    "cameralabs",
    "digitalcameraworld",
    "dpreview",
    "fstoppers",
    "imaging-resource",
    "kenrockwell",
    "lensrentals",
    "lensreviewer",
    "lensvid",
    "lenstip",
    "mirrorlessons",
    "opticallimits",
    "petapixel",
    "phillipreeve",
    "photographyblog",
    "photographylife",
    "photographyreview",
    "sonyalpha",
    "sonyalpha.blog",
    "thephoblographer",
}


COUNTRY_COORDS = {
    "美国": (37.0902, -95.7129),
    "德国": (51.1657, 10.4515),
    "英国": (55.3781, -3.4360),
    "日本": (36.2048, 138.2529),
    "法国": (46.6034, 1.8883),
    "意大利": (41.8719, 12.5674),
    "加拿大": (56.1304, -106.3468),
    "澳大利亚": (-25.2744, 133.7751),
    "西班牙": (40.4637, -3.7492),
    "瑞典": (60.1282, 18.6435),
    "比利时": (50.5039, 4.4699),
    "罗马尼亚": (45.9432, 24.9668),
    "荷兰": (52.1326, 5.2913),
    "瑞士": (46.8182, 8.2275),
    "波兰": (51.9194, 19.1451),
    "巴西": (-14.2350, -51.9253),
    "墨西哥": (23.6345, -102.5528),
    "俄罗斯": (61.5240, 105.3188),
    "韩国": (35.9078, 127.7669),
    "中国": (35.8617, 104.1954),
    "印度": (20.5937, 78.9629),
    "香港": (22.3193, 114.1694),
    "台湾": (23.6978, 120.9605),
    "新加坡": (1.3521, 103.8198),
    "马来西亚": (4.2105, 101.9758),
    "印尼": (-0.7893, 113.9213),
    "泰国": (15.8700, 100.9925),
    "越南": (14.0583, 108.2772),
    "菲律宾": (12.8797, 121.7740),
    "土耳其": (38.9637, 35.2433),
    "阿根廷": (-38.4161, -63.6167),
    "南非": (-30.5595, 22.9375),
    "挪威": (60.4720, 8.4689),
    "丹麦": (56.2639, 9.5018),
    "芬兰": (61.9241, 25.7482),
    "葡萄牙": (39.3999, -8.2245),
    "希腊": (39.0742, 21.8243),
    "奥地利": (47.5162, 14.5501),
    "捷克": (49.8175, 15.4730),
    "匈牙利": (47.1625, 19.5033),
    "爱尔兰": (53.1424, -7.6921),
    "以色列": (31.0461, 34.8516),
    "阿联酋": (23.4241, 53.8478),
    "新西兰": (-40.9006, 174.8860),
    "哥伦比亚": (4.5709, -74.2973),
    "斯洛伐克": (48.6690, 19.6990),
    "伊朗": (32.4279, 53.6880),
    "智利": (-35.6751, -71.5430),
    "哈萨克斯坦": (48.0196, 66.9237),
    "格鲁吉亚": (42.3154, 43.3569),
    "乌克兰": (48.3794, 31.1656),
    "缅甸": (21.9162, 95.9560),
    "卢森堡": (49.8153, 6.1296),
    "摩洛哥": (31.7917, -7.0926),
    "冰岛": (64.9631, -19.0208),
    "孟加拉国": (23.6850, 90.3563),
    "斯洛文尼亚": (46.1512, 14.9955),
    "秘鲁": (-9.1900, -75.0152),
}

COUNTRY_ALIASES = {
    "AU": "澳大利亚",
    "Australia": "澳大利亚",
    "BE": "比利时",
    "CA": "加拿大",
    "DE": "德国",
    "GB": "英国",
    "UK": "英国",
    "US": "美国",
    "JP": "日本",
    "Japan": "日本",
    "Italy": "意大利",
    "迪拜": "阿联酋",
    "中国台湾": "台湾",
    "印度尼西亚": "印尼",
    "常驻巴厘岛": "印尼",
    "美国（西语）": "美国",
}


@dataclass(frozen=True)
class Classification:
    account_type: str
    tier: str
    latitude: float | None
    longitude: float | None


def _text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except Exception:
        return 0


def classify_account_type(handle: str, display_name: str, platform: str) -> str:
    h = _text(handle).lower()
    name = _text(display_name).lower()
    p = _text(platform).lower()
    if "viltrox" in h or "viltrox" in name or "唯卓仕" in name:
        return "company"
    if p == "media" or "media" in name or any(marker in h for marker in MEDIA_HANDLES):
        return "media"
    return "kol"


def classify_tier(followers: Any) -> str:
    count = _int(followers)
    if count >= 100_000:
        return "头部"
    if count >= 10_000:
        return "腰部"
    return "尾部"


def assign_coords(country: str, seed: int) -> tuple[float | None, float | None]:
    country = normalize_country(country)
    if not country or country not in COUNTRY_COORDS:
        return None, None
    base_lat, base_lng = COUNTRY_COORDS[country]
    rnd = random.Random(seed)
    return (
        round(base_lat + (rnd.random() - 0.5) * 4, 6),
        round(base_lng + (rnd.random() - 0.5) * 4, 6),
    )


def normalize_country(country: str) -> str:
    text = _text(country).replace("\n", "").strip()
    if not text or text in {"未知", "全球"}:
        return ""
    if text in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[text]
    for delimiter in ("/", ",", "，"):
        if delimiter in text:
            first = _text(text.split(delimiter, 1)[0])
            return COUNTRY_ALIASES.get(first, first)
    return COUNTRY_ALIASES.get(text, text)


def classify_pool_row(row: dict[str, Any]) -> Classification:
    account_type = classify_account_type(row.get("handle"), row.get("display_name"), row.get("platform"))
    tier = classify_tier(row.get("followers"))
    lat, lng = assign_coords(_text(row.get("country")), _int(row.get("id")))
    return Classification(account_type, tier, lat, lng)


def fetch_pool_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, platform, handle, display_name, country, followers
        FROM vkpi_kol_pool
        ORDER BY id
        """
    )
    return list(cur.fetchall())


def fetch_official_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
          c.id,
          c.platform,
          c.account_handle AS handle,
          c.account_display_name AS display_name,
          COALESCE(m.followers, c.self_reported_followers, 0) AS followers
        FROM vkpi_employee_channels c
        LEFT JOIN LATERAL (
          SELECT followers
          FROM vkpi_channel_metrics mm
          WHERE mm.channel_id = c.id
          ORDER BY mm.snapshot_date DESC, mm.captured_at DESC, mm.id DESC
          LIMIT 1
        ) m ON TRUE
        WHERE c.deleted_at IS NULL
          AND c.status = 'active'
          AND (
            POSITION('official_account' IN COALESCE(c.metadata_json::text, '')) > 0
            OR POSITION('official_list_20260516' IN COALESCE(c.metadata_json::text, '')) > 0
            OR POSITION('official_account_list_2026_05_16' IN COALESCE(c.metadata_json::text, '')) > 0
          )
        ORDER BY c.platform, c.account_handle
        """
    )
    return list(cur.fetchall())


def print_counter(title: str, counter: Counter) -> None:
    print(f"\n=== {title} ===")
    for key in sorted(counter):
        print(f"{key}: {counter[key]}")


def ensure_commit_columns(cur) -> None:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name='vkpi_kol_pool'
          AND column_name IN (
            'dashboard_account_type',
            'dashboard_tier',
            'dashboard_latitude',
            'dashboard_longitude'
          )
        """
    )
    columns = {row["column_name"] for row in cur.fetchall()}
    missing = {
        "dashboard_account_type",
        "dashboard_tier",
        "dashboard_latitude",
        "dashboard_longitude",
    } - columns
    if missing:
        raise RuntimeError(
            "Missing migration columns: "
            + ", ".join(sorted(missing))
            + ". Run migrations/083_dashboard_kol_account_picker.sql first."
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show numbers without writing")
    parser.add_argument("--commit", action="store_true", help="write dashboard_* fields to vkpi_kol_pool")
    args = parser.parse_args()
    if args.dry_run and args.commit:
        raise SystemExit("Use either --dry-run or --commit, not both.")
    if not args.dry_run and not args.commit:
        args.dry_run = True

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required.")

    if psycopg is not None:
        conn = psycopg.connect(dsn, row_factory=dict_row)
        cur = conn.cursor()
    elif psycopg2 is not None:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
    else:
        raise SystemExit("Missing PostgreSQL client library. Install psycopg or psycopg2-binary.")
    pool_rows = fetch_pool_rows(cur)
    official_rows = fetch_official_rows(cur)

    updates: list[tuple[str, str, float | None, float | None, int]] = []
    pool_type = Counter()
    pool_tier = Counter()
    pool_group = Counter()
    coords_missing = 0

    for row in pool_rows:
        item = classify_pool_row(row)
        updates.append((item.account_type, item.tier, item.latitude, item.longitude, int(row["id"])))
        pool_type[item.account_type] += 1
        pool_tier[item.tier] += 1
        pool_group[(item.account_type, item.tier)] += 1
        if item.latitude is None:
            coords_missing += 1

    official_tier = Counter(classify_tier(row.get("followers")) for row in official_rows)
    dashboard_group = Counter(pool_group)
    dashboard_group.subtract({key: value for key, value in pool_group.items() if key[0] == "company"})
    for tier, count in official_tier.items():
        dashboard_group[("company", tier)] += count

    print("=" * 72)
    print("Dashboard KOL Picker Phase 1A ETL")
    print(f"mode: {'COMMIT' if args.commit else 'DRY-RUN'}")
    print("=" * 72)
    print(f"vkpi_kol_pool rows: {len(pool_rows)}")
    print(f"official company accounts: {len(official_rows)}")
    print(f"dashboard view rows after company-source merge: {sum(dashboard_group.values())}")
    print_counter("pool account_type", pool_type)
    print_counter("pool tier", pool_tier)
    print(f"\ncoords available: {len(pool_rows) - coords_missing}")
    print(f"coords missing: {coords_missing}")

    print("\n=== dashboard account_type,tier preview ===")
    for account_type, tier in sorted(dashboard_group):
        count = dashboard_group[(account_type, tier)]
        if count > 0:
            print(f"{account_type}\t{tier}\t{count}")

    if args.commit:
        ensure_commit_columns(cur)
        cur.executemany(
            """
            UPDATE vkpi_kol_pool
               SET dashboard_account_type=%s,
                   dashboard_tier=%s,
                   dashboard_latitude=%s,
                   dashboard_longitude=%s
             WHERE id=%s
            """,
            updates,
        )
        conn.commit()
        print(f"\ncommitted rows: {len(updates)}")
    else:
        print("\n[DRY-RUN] no rows were written.")

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
