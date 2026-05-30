"""Dashboard KOL country distribution helpers."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.dashboard.recent_content import _dashboard_int
from app.domains.kol import pool as kol_pool
from app.domains.dashboard.country_coords import country_geo, resolve_country_code
from app.domains.dashboard.city_coords import country_city_centroids, resolve_city
from app.domains.kol.pool_common import _table_columns


logger = get_logger(__name__)

MAP_PACK_SCHEMA_VERSION = 1
MAP_PACK_TTL_SECONDS = 3600
_MAP_PACK_CACHE: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}

CITY_FIELD_KEYS = {
    "city",
    "town",
    "basecity",
    "homecity",
    "profilecity",
    "locationcity",
    "creatorcity",
    "audiencecity",
}
LOCATION_FIELD_MARKERS = (
    "location",
    "located",
    "address",
    "place",
    "hometown",
    "geo",
    "region",
)


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        logger.debug("Failed to decode KOL location JSON payload", exc_info=True)
        return {}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _walk_location_values(value: Any, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        for raw_key, raw_item in value.items():
            key = _key(raw_key)
            if isinstance(raw_item, (dict, list)):
                found.extend(_walk_location_values(raw_item, depth + 1))
                continue
            text = _text(raw_item)
            if not text:
                continue
            if key in CITY_FIELD_KEYS or key.endswith("city"):
                found.insert(0, text)
            elif any(marker in key for marker in LOCATION_FIELD_MARKERS):
                found.append(text)
    elif isinstance(value, list):
        for item in value[:100]:
            if isinstance(item, (dict, list)):
                found.extend(_walk_location_values(item, depth + 1))
    return found


def _row_city(row: dict[str, Any], country_code: str) -> dict[str, Any] | None:
    candidates: list[str] = []
    for column in ("city", "location", "profile_location", "bio_location"):
        value = _text(row.get(column))
        if value:
            candidates.append(value)
    for column in ("audience_estimated_json", "raw_platform_data"):
        candidates.extend(_walk_location_values(_loads(row.get(column))))
    bio = _text(row.get("bio"))
    if bio:
        # Last resort: only matches known city names as whole words.
        candidates.append(bio[:800])
    city = resolve_city(country_code, *candidates)
    if not city:
        return None
    return {
        "country": str(city["country"]),
        "name": str(city["name"]),
        "lat": float(city["lat"]),
        "lng": float(city["lng"]),
        "source": "stored_location",
    }


def _stable_index(row: dict[str, Any], modulo: int) -> int:
    if modulo <= 1:
        return 0
    seed = "|".join(
        str(row.get(key) or "")
        for key in ("id", "pool_uid", "platform", "handle", "display_name")
    )
    value = 0
    for char in seed:
        value = (value * 31 + ord(char)) % 1000003
    return value % modulo


def _attach_city_distribution(conn, countries_by_code: dict[str, dict]) -> dict[str, int]:
    columns = _table_columns(conn, "vkpi_kol_pool")
    wanted = [
        "id",
        "country",
        "platform",
        "handle",
        "display_name",
        "followers",
        "avg_views",
        "engagement_rate",
        "viltrox_fit_score",
        "primary_topic",
        "content_style",
        "bio",
        "profile_url",
        "raw_platform_data",
        "audience_estimated_json",
    ]
    select_columns = [column for column in wanted if column in columns]
    if not select_columns:
        return {"city_count": 0, "city_mapped_kol_count": 0}
    try:
        rows = conn.execute(
            f"SELECT {', '.join(select_columns)} FROM vkpi_kol_pool LIMIT 10000"
        ).fetchall()
    except Exception:
        return {"city_count": 0, "city_mapped_kol_count": 0}

    city_count = 0
    mapped_rows = 0
    for raw_row in rows:
        row = dict(raw_row)
        country_code = resolve_country_code(row.get("country"))
        city = _row_city(row, country_code)
        location_source = "stored_location"
        if not city:
            city_options = country_city_centroids(country_code)
            if not city_options:
                continue
            city = dict(city_options[_stable_index(row, len(city_options))])
            location_source = "country_scatter"
        city_country = str(city["country"])
        geo = country_geo(city_country)
        if not geo:
            continue
        country_item = countries_by_code.setdefault(
            city_country,
            {
                **geo,
                "count": 0,
                "share": 0.0,
                "exposure": 0,
                "platforms": [],
                "raw_values": [],
            },
        )
        cities = country_item.setdefault("_city_buckets", {})
        city_item = cities.setdefault(
            city["name"],
            {
                "name": city["name"],
                "lat": city["lat"],
                "lng": city["lng"],
                "count": 0,
                "exposure": 0,
                "platforms": {},
                "sample_kols": [],
                "location_source_counts": {},
            },
        )
        city_item["count"] += 1
        city_item["exposure"] += _dashboard_int(row.get("avg_views"))
        platform = str(row.get("platform") or "unknown").strip() or "unknown"
        city_item["platforms"][platform] = int(city_item["platforms"].get(platform) or 0) + 1
        city_item["location_source_counts"][location_source] = int(city_item["location_source_counts"].get(location_source) or 0) + 1
        city_item["sample_kols"].append(
            {
                "id": row.get("id"),
                "handle": row.get("handle") or row.get("display_name") or "KOL",
                "display_name": row.get("display_name") or row.get("handle") or "KOL",
                "platform": platform,
                "followers": row.get("followers"),
                "avg_views": row.get("avg_views"),
                "engagement_rate": row.get("engagement_rate"),
                "viltrox_fit_score": row.get("viltrox_fit_score"),
                "primary_topic": row.get("primary_topic"),
                "content_style": row.get("content_style"),
                "location_source": location_source,
            }
        )
        mapped_rows += 1

    for country_item in countries_by_code.values():
        buckets = country_item.pop("_city_buckets", {})
        cities = []
        for city_item in buckets.values():
            city_count += 1
            sample_kols = sorted(
                city_item["sample_kols"],
                key=lambda item: (
                    -_dashboard_int(item.get("viltrox_fit_score")),
                    -_dashboard_int(item.get("avg_views")),
                    -_dashboard_int(item.get("followers")),
                    str(item.get("handle") or ""),
                ),
            )[:80]
            platforms = [
                {"platform": platform, "count": count}
                for platform, count in sorted(city_item["platforms"].items(), key=lambda item: (-int(item[1]), str(item[0])))
            ]
            cities.append(
                {
                    "name": city_item["name"],
                    "lat": city_item["lat"],
                    "lng": city_item["lng"],
                    "count": city_item["count"],
                    "exposure": city_item["exposure"],
                    "platforms": platforms,
                    "sample_kols": sample_kols,
                    "location_source_counts": city_item["location_source_counts"],
                }
            )
        cities.sort(key=lambda item: (-int(item["count"] or 0), str(item["name"])))
        country_item["cities"] = cities
        country_item["city_count"] = len(cities)
        country_item["city_mapped_kol_count"] = sum(int(item["count"] or 0) for item in cities)
    return {"city_count": city_count, "city_mapped_kol_count": mapped_rows}


def build_dashboard_kol_distribution(limit: int = 200) -> dict:
    conn = get_conn()
    distribution = kol_pool._country_distribution(conn, limit=limit)
    countries_by_code: dict[str, dict] = {}
    unmapped: list[dict] = []
    for row in distribution:
        raw_values = row.get("raw_values") if isinstance(row.get("raw_values"), list) else []
        code = resolve_country_code(row.get("country_code"), row.get("country_name"), *raw_values)
        geo = country_geo(code)
        if not geo:
            unmapped.append(row)
            continue
        item = countries_by_code.setdefault(
            geo["code"],
            {
                **geo,
                "count": 0,
                "share": 0.0,
                "exposure": 0,
                "platforms": [],
                "raw_values": [],
            },
        )
        item["count"] += int(row.get("kol_count") or 0)
        for raw in raw_values:
            if raw not in item["raw_values"]:
                item["raw_values"].append(raw)

    try:
        platform_rows = conn.execute(
            """
            SELECT country, platform, COUNT(*) AS n, COALESCE(SUM(avg_views), 0) AS exposure
            FROM vkpi_kol_pool
            WHERE country IS NOT NULL AND TRIM(country) != ''
            GROUP BY country, platform
            """
        ).fetchall()
    except Exception:
        platform_rows = []
    platform_buckets: dict[str, dict[str, dict[str, int]]] = {}
    for row in platform_rows:
        row_data = dict(row)
        raw_country = str(row_data.get("country") or "").strip()
        code = resolve_country_code(raw_country)
        geo = country_geo(code)
        if not geo or geo["code"] not in countries_by_code:
            continue
        platform = str(row_data.get("platform") or "unknown").strip() or "unknown"
        item = platform_buckets.setdefault(geo["code"], {}).setdefault(platform, {"count": 0, "exposure": 0})
        item["count"] += _dashboard_int(row_data.get("n"))
        item["exposure"] += _dashboard_int(row_data.get("exposure"))
    for code, platform_map in platform_buckets.items():
        country_item = countries_by_code.get(code)
        if not country_item:
            continue
        platforms = [
            {"platform": platform, "count": values["count"], "exposure": values["exposure"]}
            for platform, values in platform_map.items()
        ]
        platforms.sort(key=lambda item: (-int(item["count"] or 0), str(item["platform"])))
        country_item["platforms"] = platforms
        country_item["exposure"] = sum(int(item["exposure"] or 0) for item in platforms)

    city_stats = _attach_city_distribution(conn, countries_by_code)
    mapped_kol_count = sum(int(item["count"] or 0) for item in countries_by_code.values())
    source_country_kol_count = mapped_kol_count + sum(int(item.get("kol_count") or 0) for item in unmapped)
    try:
        total_pool_rows = int((conn.execute("SELECT COUNT(*) AS n FROM vkpi_kol_pool").fetchone() or {})["n"] or 0)
    except Exception:
        total_pool_rows = 0
    countries = sorted(countries_by_code.values(), key=lambda item: (-int(item["count"] or 0), str(item["code"])))
    for item in countries:
        item["share"] = round((int(item["count"] or 0) / mapped_kol_count) * 100, 2) if mapped_kol_count else 0.0

    return {
        "total_kol": mapped_kol_count,
        "mapped_kol_count": mapped_kol_count,
        "source_country_kol_count": source_country_kol_count,
        "total_pool_rows": total_pool_rows,
        "missing_country_count": max(0, total_pool_rows - source_country_kol_count),
        "countries": countries,
        "country_count": len(countries),
        "city_count": city_stats["city_count"],
        "city_mapped_kol_count": city_stats["city_mapped_kol_count"],
        "unmapped_count": len(unmapped),
        "unmapped_kol_count": source_country_kol_count - mapped_kol_count,
        "unmapped_sample": unmapped[:10],
        "data_source": "vkpi_kol_pool.country + stored_location/country_scatter map buckets",
        "is_real": True,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _with_cache_meta(payload: dict[str, Any], *, hit: bool, age_seconds: int = 0) -> dict[str, Any]:
    result = dict(payload)
    result["cache"] = {
        "hit": hit,
        "age_seconds": age_seconds,
        "ttl_seconds": int(result.get("ttl_seconds") or MAP_PACK_TTL_SECONDS),
        "scope": "server-memory",
    }
    return result


def build_dashboard_kol_distribution_pack(limit: int = 250) -> dict:
    """Return a versioned map data pack intended for frontend cache-first use."""
    normalized_limit = max(1, min(int(limit or 250), 1000))
    cache_key = (MAP_PACK_SCHEMA_VERSION, normalized_limit)
    now = time.time()
    cached = _MAP_PACK_CACHE.get(cache_key)
    if cached:
        cached_at, payload = cached
        age = int(now - cached_at)
        if age < MAP_PACK_TTL_SECONDS:
            return _with_cache_meta(payload, hit=True, age_seconds=age)

    distribution = build_dashboard_kol_distribution(limit=normalized_limit)
    countries = distribution.get("countries") if isinstance(distribution.get("countries"), list) else []
    generated_at = _utc_now_iso()
    total_kol = _dashboard_int(distribution.get("mapped_kol_count") or distribution.get("total_kol"))
    payload: dict[str, Any] = {
        "schema_version": MAP_PACK_SCHEMA_VERSION,
        "resource": "dashboard.kol_distribution_pack",
        "generated_at": generated_at,
        "snapshot_id": f"kol-map-v{MAP_PACK_SCHEMA_VERSION}-{generated_at[:10]}-{len(countries)}-{total_kol}",
        "ttl_seconds": MAP_PACK_TTL_SECONDS,
        "countries": countries,
        "stats": {
            "total_kol": total_kol,
            "mapped_kol_count": _dashboard_int(distribution.get("mapped_kol_count")),
            "source_country_kol_count": _dashboard_int(distribution.get("source_country_kol_count")),
            "total_pool_rows": _dashboard_int(distribution.get("total_pool_rows")),
            "missing_country_count": _dashboard_int(distribution.get("missing_country_count")),
            "country_count": _dashboard_int(distribution.get("country_count")),
            "city_count": _dashboard_int(distribution.get("city_count")),
            "city_mapped_kol_count": _dashboard_int(distribution.get("city_mapped_kol_count")),
            "unmapped_kol_count": _dashboard_int(distribution.get("unmapped_kol_count")),
        },
        "data_source": distribution.get("data_source"),
        "is_real": bool(distribution.get("is_real")),
    }
    _MAP_PACK_CACHE[cache_key] = (now, payload)
    return _with_cache_meta(payload, hit=False, age_seconds=0)
