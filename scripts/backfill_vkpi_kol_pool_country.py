#!/usr/bin/env python3
"""Backfill missing vkpi_kol_pool.country from existing local fields only.

This script does not call providers or crawl profiles. It uses already stored
raw/profile URL/display fields, writes only missing country values, and creates
a JSON backup before --commit writes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.db.connection import close_db_runtime, get_conn
from app.domains.kol.pool_common import COUNTRY_NAMES, _clear_kol_pool_read_cache, _country_code


DOMAIN_COUNTRIES: dict[str, tuple[str, float, str]] = {
    "35mmc.com": ("GB", 0.92, "known_media_domain"),
    "digitalcameraworld.com": ("GB", 0.92, "known_media_domain"),
    "ephotozine.com": ("GB", 0.92, "known_media_domain"),
    "macfilos.com": ("GB", 0.88, "known_media_domain"),
    "photographyblog.com": ("GB", 0.92, "known_media_domain"),
    "nikon-fotografie.de": ("DE", 0.96, "country_tld_domain"),
    "opticallimits.com": ("AU", 0.84, "known_media_domain"),
    "pcmag.com": ("US", 0.92, "known_media_domain"),
    "thephoblographer.com": ("US", 0.92, "known_media_domain"),
    "fstoppers.com": ("US", 0.88, "known_media_domain"),
    "photographylife.com": ("CA", 0.84, "known_media_domain"),
    "sonyalpha.blog": ("BE", 0.84, "known_media_domain"),
    "kojinakagawa.com": ("JP", 0.92, "known_creator_domain"),
    "yphoto-journal.com": ("JP", 0.88, "known_media_domain"),
}

TLD_COUNTRIES: dict[str, tuple[str, float]] = {
    ".de": ("DE", 0.95),
    ".fr": ("FR", 0.95),
    ".jp": ("JP", 0.95),
    ".co.jp": ("JP", 0.95),
    ".uk": ("GB", 0.95),
    ".co.uk": ("GB", 0.95),
    ".it": ("IT", 0.95),
    ".es": ("ES", 0.95),
    ".nl": ("NL", 0.95),
    ".ca": ("CA", 0.95),
    ".com.au": ("AU", 0.95),
    ".com.br": ("BR", 0.95),
}

RAW_COUNTRY_KEYS = {
    "country",
    "country_code",
    "countryCode",
    "国家",
    "国家/地区",
}


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return {}


def _domain(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    return parsed.netloc.lower().removeprefix("www.")


def _valid_code(value: Any) -> str:
    code = _country_code(value)
    return code if code in COUNTRY_NAMES else ""


def _walk_country_values(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in RAW_COUNTRY_KEYS:
                code = _valid_code(item)
                if code:
                    found.append(code)
            if isinstance(item, (dict, list)):
                found.extend(_walk_country_values(item))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                found.extend(_walk_country_values(item))
    return found


def infer_country(row: dict[str, Any]) -> dict[str, Any] | None:
    raw_codes = _walk_country_values(_json_loads(row.get("raw_platform_data")))
    if raw_codes:
        code = Counter(raw_codes).most_common(1)[0][0]
        return {"country": code, "confidence": 0.98, "reason": "raw_country_field"}

    domain = _domain(row.get("profile_url"))
    if domain:
        for known_domain, (code, confidence, reason) in DOMAIN_COUNTRIES.items():
            if domain == known_domain or domain.endswith(f".{known_domain}"):
                return {"country": code, "confidence": confidence, "reason": reason, "domain": domain}
        for suffix, (code, confidence) in TLD_COUNTRIES.items():
            if domain.endswith(suffix):
                return {"country": code, "confidence": confidence, "reason": "country_tld", "domain": domain}

    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("display_name", "handle", "bio", "profile_url")
    ).lower()
    explicit_markers = {
        "united states": "US",
        " u.s.": "US",
        " usa": "US",
        "united kingdom": "GB",
        "great britain": "GB",
        " germany": "DE",
        " deutschland": "DE",
        " japan": "JP",
        " australia": "AU",
        " canada": "CA",
        " france": "FR",
    }
    for marker, code in explicit_markers.items():
        if marker in haystack:
            return {"country": code, "confidence": 0.82, "reason": "explicit_text_marker"}
    return None


def load_missing(limit: int) -> list[dict[str, Any]]:
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, platform, handle, display_name, bio, profile_url, raw_platform_data, source_type, country
        FROM vkpi_kol_pool
        WHERE country IS NULL OR TRIM(country) = ''
        ORDER BY id ASC
        LIMIT ?
        """,
        (max(1, int(limit or 500)),),
    ).fetchall()
    return [dict(row) for row in rows]


def write_backup(rows: list[dict[str, Any]], backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"vkpi-kol-pool-country-backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--min-confidence", type=float, default=0.80)
    parser.add_argument("--backup-dir", default="runtime/vkpi-country-backfill")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    rows = load_missing(args.limit)
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        candidate = infer_country(row)
        if not candidate or float(candidate["confidence"]) < float(args.min_confidence):
            skipped.append({
                "id": row.get("id"),
                "platform": row.get("platform"),
                "handle": row.get("handle"),
                "profile_url": row.get("profile_url"),
                "reason": "no_confident_local_signal" if not candidate else "below_min_confidence",
                "candidate": candidate,
            })
            continue
        planned.append({
            "id": row["id"],
            "platform": row.get("platform"),
            "handle": row.get("handle"),
            "display_name": row.get("display_name"),
            "profile_url": row.get("profile_url"),
            "old_country": row.get("country") or "",
            **candidate,
        })

    backup_path = None
    updated = 0
    if args.commit and planned:
        backup_path = str(write_backup(planned, Path(args.backup_dir)))
        conn = get_conn()
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for item in planned:
            result = conn.execute(
                """
                UPDATE vkpi_kol_pool
                SET country=?, updated_at=?
                WHERE id=? AND (country IS NULL OR TRIM(country) = '')
                """,
                (item["country"], now, int(item["id"])),
            )
            updated += int(getattr(result, "rowcount", 0) or 0)
        conn.commit()
        _clear_kol_pool_read_cache()

    output = {
        "commit": bool(args.commit),
        "total_missing_scanned": len(rows),
        "planned": len(planned),
        "updated": updated,
        "skipped": len(skipped),
        "by_country": dict(Counter(str(item["country"]) for item in planned)),
        "backup_path": backup_path,
        "samples": planned[:20],
        "skipped_samples": skipped[:10],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            asyncio.run(close_db_runtime())
        except Exception:
            pass
