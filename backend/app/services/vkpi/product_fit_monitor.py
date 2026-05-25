"""Read-only Product Fit monitoring for SKU alias/spec readiness."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.domains.kol import sku_fit as kol_sku_fit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value is not None else default))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def _count(sql: str, params: tuple[Any, ...] = ()) -> int:
    row = get_conn().execute(sql, params).fetchone()
    if not row:
        return 0
    if "n" in row.keys():
        return _int(row["n"])
    return _int(row[0])


def _coverage_counts() -> dict[str, Any]:
    product_count = _count("SELECT COUNT(*) AS n FROM vkpi_products") if _table_exists("vkpi_products") else 0
    alias_rows = _count("SELECT COUNT(*) AS n FROM vkpi_product_aliases") if _table_exists("vkpi_product_aliases") else 0
    alias_skus = _count("SELECT COUNT(DISTINCT sku) AS n FROM vkpi_product_aliases") if _table_exists("vkpi_product_aliases") else 0
    spec_rows = _count("SELECT COUNT(*) AS n FROM vkpi_product_spec_facts") if _table_exists("vkpi_product_spec_facts") else 0
    spec_skus = _count("SELECT COUNT(DISTINCT sku) AS n FROM vkpi_product_spec_facts") if _table_exists("vkpi_product_spec_facts") else 0
    return {
        "product_count": product_count,
        "alias_rows": alias_rows,
        "alias_skus": alias_skus,
        "spec_rows": spec_rows,
        "spec_skus": spec_skus,
        "alias_sku_coverage": round((alias_skus / product_count) * 100, 2) if product_count else 0,
        "spec_sku_coverage": round((spec_skus / product_count) * 100, 2) if product_count else 0,
    }


def _missing_samples(limit: int = 20) -> dict[str, Any]:
    result: dict[str, Any] = {"products_without_aliases": [], "products_without_spec_facts": []}
    if _table_exists("vkpi_products") and _table_exists("vkpi_product_aliases"):
        rows = get_conn().execute(
            """
            SELECT p.sku, p.model_name
            FROM vkpi_products p
            LEFT JOIN vkpi_product_aliases a ON a.sku=p.sku
            WHERE a.sku IS NULL
            ORDER BY p.sku ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result["products_without_aliases"] = [_row_dict(row) for row in rows]
    if _table_exists("vkpi_products") and _table_exists("vkpi_product_spec_facts"):
        rows = get_conn().execute(
            """
            SELECT p.sku, p.model_name
            FROM vkpi_products p
            LEFT JOIN vkpi_product_spec_facts sf ON sf.sku=p.sku
            WHERE sf.sku IS NULL
            ORDER BY p.sku ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result["products_without_spec_facts"] = [_row_dict(row) for row in rows]
    return result


def _ambiguous_aliases(limit: int = 20) -> dict[str, Any]:
    if not _table_exists("vkpi_product_aliases"):
        return {"count": 0, "sample": []}
    rows = get_conn().execute(
        """
        SELECT alias_norm, COUNT(DISTINCT sku) AS sku_count
        FROM vkpi_product_aliases
        GROUP BY alias_norm
        HAVING COUNT(DISTINCT sku) > 1
        ORDER BY sku_count DESC, alias_norm ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    sample = []
    for row in rows:
        item = _row_dict(row)
        aliases = get_conn().execute(
            """
            SELECT sku, alias, alias_type, confidence
            FROM vkpi_product_aliases
            WHERE alias_norm=?
            ORDER BY confidence DESC, sku ASC
            LIMIT 10
            """,
            (item.get("alias_norm"),),
        ).fetchall()
        sample.append({**item, "matches": [_row_dict(alias) for alias in aliases]})
    count = _count(
        """
        SELECT COUNT(*) AS n
        FROM (
          SELECT alias_norm
          FROM vkpi_product_aliases
          GROUP BY alias_norm
          HAVING COUNT(DISTINCT sku) > 1
        ) x
        """
    )
    return {"count": count, "sample": sample}


def _low_spec_completeness(limit: int = 20, threshold: float = 70.0) -> dict[str, Any]:
    if not _table_exists("vkpi_product_spec_facts"):
        return {"threshold": threshold, "count": 0, "sample": []}
    rows = get_conn().execute(
        """
        SELECT sku, category_detail, mount_norm, focal_length_label, max_aperture_label,
               completeness_score, missing_fields_json
        FROM vkpi_product_spec_facts
        WHERE completeness_score < ?
        ORDER BY completeness_score ASC, sku ASC
        LIMIT ?
        """,
        (float(threshold), int(limit)),
    ).fetchall()
    count = _count("SELECT COUNT(*) AS n FROM vkpi_product_spec_facts WHERE completeness_score < ?", (float(threshold),))
    sample = []
    for row in rows:
        item = _row_dict(row)
        try:
            item["missing_fields"] = json.loads(str(item.get("missing_fields_json") or "[]"))
        except Exception:
            item["missing_fields"] = []
        sample.append(item)
    return {"threshold": threshold, "count": count, "sample": sample}


def _launch_alias_probe(limit: int = 20) -> dict[str, Any]:
    if not (_table_exists("vkpi_product_launches") and _table_exists("vkpi_product_aliases")):
        return {"available": False, "reason": "required_tables_missing"}
    rows = get_conn().execute(
        """
        SELECT id, product_sku, product_name
        FROM vkpi_product_launches
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT 500
        """
    ).fetchall()
    hits = 0
    misses = []
    for row in rows:
        item = _row_dict(row)
        values = [item.get("product_sku"), item.get("product_name")]
        matched = False
        for value in values:
            norm = kol_sku_fit._norm(value)
            if not norm:
                continue
            alias = get_conn().execute(
                "SELECT sku FROM vkpi_product_aliases WHERE alias_norm=? LIMIT 1",
                (norm,),
            ).fetchone()
            if alias:
                matched = True
                break
        if matched:
            hits += 1
        elif len(misses) < limit:
            misses.append(item)
    return {
        "available": True,
        "launch_count": len(rows),
        "exact_alias_hits": hits,
        "misses": max(0, len(rows) - hits),
        "miss_sample": misses,
    }


def build_monitor_report(*, query: str = "viltrox", sample_kol_pool_id: int = 0, low_threshold: float = 70.0) -> dict[str, Any]:
    coverage = _coverage_counts()
    missing = _missing_samples()
    ambiguous = _ambiguous_aliases()
    low_specs = _low_spec_completeness(threshold=low_threshold)
    launch_probe = _launch_alias_probe()
    sku_fit = kol_sku_fit.build_kol_sku_fit_report(kol_pool_id=sample_kol_pool_id, query=query, sku_limit=500, top_n=8)
    coverage_ok = coverage["product_count"] > 0 and coverage["alias_sku_coverage"] >= 95 and coverage["spec_sku_coverage"] >= 95
    checks = {
        "product_catalog_present": coverage["product_count"] > 0,
        "alias_coverage_ok": coverage_ok and coverage["alias_sku_coverage"] >= 95,
        "spec_coverage_ok": coverage_ok and coverage["spec_sku_coverage"] >= 95,
        "join_misses_visible": isinstance(missing.get("products_without_aliases"), list)
        and isinstance(missing.get("products_without_spec_facts"), list),
        "ambiguous_aliases_visible": isinstance(ambiguous.get("sample"), list),
        "low_completeness_visible": isinstance(low_specs.get("sample"), list),
        "launch_probe_visible": bool(launch_probe.get("available")) or launch_probe.get("reason") == "required_tables_missing",
        "sample_kol_sku_fit_passed": bool(sku_fit.get("passed")),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "write_db_blocked": True,
        "sync_blocked": True,
    }
    warnings = []
    if ambiguous.get("count"):
        warnings.append({"type": "ambiguous_aliases", "count": ambiguous.get("count")})
    if low_specs.get("count"):
        warnings.append({"type": "low_spec_completeness", "count": low_specs.get("count"), "threshold": low_threshold})
    if launch_probe.get("available") and launch_probe.get("misses"):
        warnings.append({"type": "launch_alias_miss", "count": launch_probe.get("misses")})
    return {
        "mode": "p5_65_product_fit_monitor",
        "generated_at": _now(),
        "query": query,
        "provider_calls": False,
        "llm_calls": False,
        "write_db": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "passed": all(bool(value) for value in checks.values()),
        "status": "warning" if warnings else "ok",
        "checks": checks,
        "warnings": warnings,
        "coverage": coverage,
        "missing_samples": missing,
        "ambiguous_aliases": ambiguous,
        "low_spec_completeness": low_specs,
        "launch_probe": launch_probe,
        "sample_kol_sku_fit": {
            "passed": sku_fit.get("passed"),
            "kol_pool_id": sku_fit.get("kol_pool_id"),
            "kol": sku_fit.get("kol"),
            "summary": sku_fit.get("summary"),
            "top_skus": (sku_fit.get("top_skus") or [])[:3],
        },
    }
