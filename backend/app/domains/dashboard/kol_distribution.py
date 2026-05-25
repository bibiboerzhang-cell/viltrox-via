"""Dashboard KOL country distribution helpers."""
from __future__ import annotations

from app.db.connection import get_conn
from app.domains.dashboard.recent_content import _dashboard_int
from app.domains.kol import pool as kol_pool
from app.services.vkpi.country_coords import country_geo, resolve_country_code


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
        "unmapped_count": len(unmapped),
        "unmapped_kol_count": source_country_kol_count - mapped_kol_count,
        "unmapped_sample": unmapped[:10],
        "data_source": "vkpi_kol_pool.country",
        "is_real": True,
    }
