#!/usr/bin/env python3
"""把 reviewed manifest 里的门店 upsert 成 vkpi_dealers 草稿行(发布前置步骤)。

apply_reviewed_physical_store_manifest 只验证+发布已存在的行(LOWER(name)+LOWER(address)
精确命中 1 行),不负责插入。本脚本补插入环:只写基础联系字段与坐标,发布/验证状态
一律不碰(保持 draft/pending,让 270 契约的三态回执由 apply 步骤在人审动作里落)。
幂等:ON CONFLICT (name, address) 只刷新联系字段。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from app.db.connection import get_conn  # noqa: E402
from stdout_utils import out as stdout_out  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="缺省 dry-run,只统计")
    args = parser.parse_args()

    data = json.loads(args.manifest.read_text())
    stores = data.get("stores") or []
    conn = get_conn()
    inserted = updated = 0
    for s in stores:
        coords = s.get("coordinates") or {}
        row = (
            s["name"], s["address"], s["city"], s["state"],
            s.get("postal_code") or None, s.get("phone") or None,
            s.get("website_url") or None, s.get("store_hours") or None,
            float(coords["lat"]), float(coords["lng"]),
            "US", "reviewed_manifest_upsert_v1",
            s.get("location_source_url") or None, s.get("brand_listing_url") or None,
        )
        if not args.write:
            existing = conn.execute(
                "SELECT 1 FROM vkpi_dealers WHERE LOWER(name)=LOWER(?) AND LOWER(address)=LOWER(?)",
                (s["name"], s["address"]),
            ).fetchone()
            inserted += 0 if existing else 1
            updated += 1 if existing else 0
            continue
        result = conn.execute(
            """
            INSERT INTO vkpi_dealers
              (name, address, city, state, postal_code, phone, website_url, store_hours,
               lat, lng, country, source, location_source_url, brand_listing_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (name, address) DO UPDATE SET
              city = EXCLUDED.city,
              state = EXCLUDED.state,
              postal_code = EXCLUDED.postal_code,
              phone = EXCLUDED.phone,
              website_url = EXCLUDED.website_url,
              store_hours = EXCLUDED.store_hours,
              lat = EXCLUDED.lat,
              lng = EXCLUDED.lng,
              location_source_url = EXCLUDED.location_source_url,
              brand_listing_url = EXCLUDED.brand_listing_url
            RETURNING (xmax = 0) AS was_insert
            """,
            row,
        ).fetchone()
        if result and dict(result).get("was_insert"):
            inserted += 1
        else:
            updated += 1
    if args.write:
        conn.commit()
    stdout_out(json.dumps({
        "manifest": str(args.manifest.name),
        "stores": len(stores),
        "inserted": inserted,
        "updated": updated,
        "mode": "write" if args.write else "dry_run",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
