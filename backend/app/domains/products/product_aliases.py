"""SKU alias readiness helpers for the V-KPI product catalog."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.shared.product_alias_normalization import APERTURE_REPLACEMENTS, normalize_product_alias


logger = get_logger(__name__)

GENERIC_ALIAS_TOKENS = {
    "af",
    "lens",
    "camera lens",
    "viltrox",
    "full frame",
    "aps c",
    "apsc",
    "evo",
    "lab",
    "pro",
    "air",
    "cine",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


from app.core.coerce import _text


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def normalize_alias(value: Any) -> str:
    """Normalize product aliases for equality checks without losing SKU tokens."""
    return normalize_product_alias(value)


def _sku_spaced(sku: str) -> str:
    return re.sub(r"[-_]+", " ", sku).strip()


def _sku_slug(sku: str) -> str:
    raw = _text(sku).upper()
    for before, after in APERTURE_REPLACEMENTS:
        raw = raw.replace(before.upper(), after.upper())
    return re.sub(r"[^A-Z0-9]+", "-", raw).strip("-")


def _product_handle(row: dict[str, Any], specs: dict[str, Any]) -> str:
    handle = _text(specs.get("official_handle"))
    if handle:
        return handle
    product_url = _text(row.get("product_url"))
    if product_url:
        return product_url.rstrip("/").split("/")[-1]
    return ""


def _lens_tokens(*values: Any) -> tuple[list[str], list[str]]:
    text = " ".join(_text(value) for value in values if _text(value)).lower()
    focal = sorted({re.sub(r"\s+", "", match) for match in re.findall(r"\b\d{1,3}\s*mm\b", text)})
    aperture_raw = set(re.findall(r"\bf\s*/?\s*\d(?:\.\d)?\b", text))
    aperture = sorted({normalize_alias(match) for match in aperture_raw if normalize_alias(match)})
    return focal, aperture


def _mount_codes(mount: Any) -> list[str]:
    text = _text(mount)
    if not text:
        return []
    norm = normalize_alias(text)
    codes: list[str] = []
    if norm.endswith(" mount"):
        codes.append(norm.replace(" mount", "").upper())
    if norm == "fe mount":
        codes.extend(["FE", "Sony E"])
    elif norm == "z mount":
        codes.append("Z")
    elif norm == "x mount":
        codes.append("X")
    elif norm == "e mount":
        codes.extend(["E", "Sony E"])
    elif norm == "m43":
        codes.extend(["M43", "M4/3"])
    codes.append(text)
    seen: set[str] = set()
    unique: list[str] = []
    for code in codes:
        key = normalize_alias(code)
        if key and key not in seen:
            seen.add(key)
            unique.append(code)
    return unique


def _add_alias(
    rows: dict[str, dict[str, Any]],
    *,
    sku: str,
    alias: Any,
    alias_type: str,
    confidence: float,
    source_id: str,
) -> None:
    display = _text(alias)
    norm = normalize_alias(display)
    if not norm or norm in GENERIC_ALIAS_TOKENS:
        return
    current = rows.get(norm)
    payload = {
        "sku": sku,
        "alias": display,
        "alias_norm": norm,
        "alias_type": alias_type,
        "source_table": "vkpi_products",
        "source_id": source_id,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 2),
    }
    if current is None or payload["confidence"] > float(current.get("confidence") or 0):
        rows[norm] = payload


def generated_aliases_for_product(product: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate conservative aliases from one vkpi_products row."""
    row = dict(product)
    sku = _text(row.get("sku")).upper()
    if not sku:
        return []
    specs = _loads(row.get("specs_json") or row.get("specs"), {})
    fit_tags = _loads(row.get("fit_tags_json") or row.get("fit_tags"), [])
    if not isinstance(specs, dict):
        specs = {}
    if not isinstance(fit_tags, list):
        fit_tags = []
    source_id = sku
    aliases: dict[str, dict[str, Any]] = {}

    _add_alias(aliases, sku=sku, alias=sku, alias_type="sku", confidence=1.0, source_id=source_id)
    _add_alias(aliases, sku=sku, alias=_sku_spaced(sku), alias_type="sku_spaced", confidence=0.98, source_id=source_id)
    _add_alias(aliases, sku=sku, alias=_sku_slug(sku), alias_type="sku_slug", confidence=0.98, source_id=source_id)

    for field, alias_type, confidence in (
        ("model_name", "model", 0.95),
        ("marketing_name", "marketing", 0.93),
    ):
        _add_alias(aliases, sku=sku, alias=row.get(field), alias_type=alias_type, confidence=confidence, source_id=source_id)

    handle = _product_handle(row, specs)
    _add_alias(aliases, sku=sku, alias=handle, alias_type="official_handle", confidence=0.86, source_id=source_id)

    focal_tokens, aperture_tokens = _lens_tokens(
        row.get("sku"),
        row.get("model_name"),
        row.get("marketing_name"),
        " ".join(str(tag) for tag in fit_tags),
        specs.get("focal_length"),
        specs.get("aperture"),
        specs.get("maximum_aperture"),
    )
    series = _text(row.get("series")).upper()
    mount = _text(row.get("mount"))
    mount_codes = _mount_codes(mount)
    for focal in focal_tokens:
        for aperture in aperture_tokens:
            _add_alias(aliases, sku=sku, alias=f"{focal} {aperture}", alias_type="spec_combo", confidence=0.75, source_id=source_id)
            if mount:
                _add_alias(aliases, sku=sku, alias=f"{focal} {aperture} {mount}", alias_type="spec_combo_mount", confidence=0.82, source_id=source_id)
            if series:
                _add_alias(aliases, sku=sku, alias=f"{focal} {aperture} {series}", alias_type="spec_combo_series", confidence=0.82, source_id=source_id)
            for mount_code in mount_codes:
                if series:
                    _add_alias(
                        aliases,
                        sku=sku,
                        alias=f"Viltrox AF {focal} {aperture} {series} {mount_code}",
                        alias_type="compact_brand",
                        confidence=0.88,
                        source_id=source_id,
                    )
                    _add_alias(
                        aliases,
                        sku=sku,
                        alias=f"AF {focal} {aperture} {series} {mount_code}",
                        alias_type="compact_model",
                        confidence=0.86,
                        source_id=source_id,
                    )
                _add_alias(
                    aliases,
                    sku=sku,
                    alias=f"Viltrox AF {focal} {aperture} {mount_code}",
                    alias_type="compact_brand",
                    confidence=0.84,
                    source_id=source_id,
                )

    return sorted(aliases.values(), key=lambda item: (-float(item["confidence"]), item["alias_norm"]))


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
        logger.debug("Postgres table lookup failed for %s; trying sqlite fallback", table_name, exc_info=True)
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        return bool(row)
    except Exception:
        return False


def ensure_product_alias_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_product_aliases (
                id BIGSERIAL PRIMARY KEY,
                sku TEXT NOT NULL REFERENCES vkpi_products(sku) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                alias_norm TEXT NOT NULL,
                alias_type TEXT NOT NULL DEFAULT 'generated',
                source_table TEXT NOT NULL DEFAULT 'vkpi_products',
                source_id TEXT NOT NULL DEFAULT '',
                confidence NUMERIC(4,2) NOT NULL DEFAULT 0.50,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (sku, alias_norm)
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_product_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT NOT NULL REFERENCES vkpi_products(sku) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                alias_norm TEXT NOT NULL,
                alias_type TEXT NOT NULL DEFAULT 'generated',
                source_table TEXT NOT NULL DEFAULT 'vkpi_products',
                source_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.50,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (sku, alias_norm)
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_alias_norm ON vkpi_product_aliases(alias_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_sku ON vkpi_product_aliases(sku)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_alias_type ON vkpi_product_aliases(alias_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_aliases_confidence ON vkpi_product_aliases(confidence DESC)")
    conn.commit()


def _fetch_products(limit: int) -> list[dict[str, Any]]:
    if not _table_exists("vkpi_products"):
        return []
    rows = get_conn().execute(
        """
        SELECT sku, category_main, category_detail, model_name, marketing_name,
               price_usd, status, description, source_file, series, mount,
               product_url, specs_json, fit_tags_json, source_url,
               source_checked_at, source_confidence, updated_at
        FROM vkpi_products
        ORDER BY source_confidence DESC, sku ASC
        LIMIT ?
        """,
        (max(1, min(2000, int(limit or 500))),),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _upsert_aliases(rows: list[dict[str, Any]]) -> int:
    ensure_product_alias_schema()
    conn = get_conn()
    now = _now()
    written = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO vkpi_product_aliases (
                sku, alias, alias_norm, alias_type, source_table, source_id,
                confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku, alias_norm) DO UPDATE SET
                alias=excluded.alias,
                alias_type=excluded.alias_type,
                source_table=excluded.source_table,
                source_id=excluded.source_id,
                confidence=excluded.confidence,
                updated_at=excluded.updated_at
            """,
            (
                row["sku"],
                row["alias"],
                row["alias_norm"],
                row["alias_type"],
                row["source_table"],
                row["source_id"],
                row["confidence"],
                now,
                now,
            ),
        )
        written += 1
    conn.commit()
    return written


def _alias_ambiguity(alias_rows: list[dict[str, Any]], *, sample_limit: int = 20) -> dict[str, Any]:
    by_norm: dict[str, set[str]] = defaultdict(set)
    displays: dict[str, set[str]] = defaultdict(set)
    for row in alias_rows:
        by_norm[str(row["alias_norm"])].add(str(row["sku"]))
        displays[str(row["alias_norm"])].add(str(row["alias"]))
    ambiguous = [
        {"alias_norm": norm, "skus": sorted(skus), "aliases": sorted(displays[norm])[:5]}
        for norm, skus in by_norm.items()
        if len(skus) > 1
    ]
    ambiguous.sort(key=lambda item: (-len(item["skus"]), item["alias_norm"]))
    return {
        "ambiguous_alias_norm_count": len(ambiguous),
        "sample": ambiguous[: max(1, sample_limit)],
    }


def _launch_probe(alias_rows: list[dict[str, Any]], *, sample_limit: int = 20) -> dict[str, Any]:
    if not _table_exists("vkpi_product_launches"):
        return {"available": False, "reason": "vkpi_product_launches_missing"}
    rows = get_conn().execute(
        """
        SELECT id, product_sku, product_name
        FROM vkpi_product_launches
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC, id DESC
        LIMIT 500
        """
    ).fetchall()
    norm_to_skus: dict[str, set[str]] = defaultdict(set)
    for alias in alias_rows:
        norm_to_skus[str(alias["alias_norm"])].add(str(alias["sku"]))
    hits = 0
    misses: list[dict[str, Any]] = []
    launch_count = 0
    for row in rows:
        item = _row_dict(row)
        launch_count += 1
        values = [_text(item.get("product_sku")), _text(item.get("product_name"))]
        matched = any(normalize_alias(value) in norm_to_skus for value in values if value)
        if matched:
            hits += 1
        elif len(misses) < sample_limit:
            misses.append(
                {
                    "id": item.get("id"),
                    "product_sku": _text(item.get("product_sku")),
                    "product_name": _text(item.get("product_name")),
                }
            )
    return {
        "available": True,
        "launch_count": launch_count,
        "exact_alias_hits": hits,
        "misses": max(0, launch_count - hits),
        "miss_sample": misses,
    }


def build_alias_readiness_report(*, limit: int = 500, apply: bool = False, ensure_schema: bool = False) -> dict[str, Any]:
    if ensure_schema:
        ensure_product_alias_schema()
    products = _fetch_products(limit)
    alias_rows: list[dict[str, Any]] = []
    products_without_aliases: list[str] = []
    for product in products:
        generated = generated_aliases_for_product(product)
        if not generated:
            products_without_aliases.append(_text(product.get("sku")))
        alias_rows.extend(generated)

    alias_type_counts = Counter(str(row.get("alias_type") or "generated") for row in alias_rows)
    ambiguity = _alias_ambiguity(alias_rows)
    launch_probe = _launch_probe(alias_rows)
    written = _upsert_aliases(alias_rows) if apply and alias_rows else 0
    alias_table_exists = _table_exists("vkpi_product_aliases")
    checks = {
        "product_catalog_present": bool(products),
        "aliases_generated": bool(alias_rows),
        "alias_table_exists": alias_table_exists or bool(apply),
        "launch_probe_available_or_not_required": bool(launch_probe.get("available")) or launch_probe.get("reason") == "vkpi_product_launches_missing",
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "sync_blocked": True,
    }
    return {
        "mode": "p5_62_sku_alias_readiness",
        "generated_at": _now(),
        "limit": max(1, min(2000, int(limit or 500))),
        "apply": bool(apply),
        "ensure_schema": bool(ensure_schema),
        "provider_calls": False,
        "llm_calls": False,
        "sync_triggered": False,
        "task_enqueued": False,
        "write_db": bool(apply or ensure_schema),
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "summary": {
            "product_count": len(products),
            "generated_alias_count": len(alias_rows),
            "aliases_written": written,
            "products_without_aliases": len([sku for sku in products_without_aliases if sku]),
            "ambiguous_alias_norm_count": ambiguity["ambiguous_alias_norm_count"],
        },
        "alias_type_counts": dict(sorted(alias_type_counts.items())),
        "ambiguous_aliases": ambiguity,
        "products_without_aliases_sample": [sku for sku in products_without_aliases if sku][:20],
        "launch_probe": launch_probe,
        "sample_aliases": alias_rows[:20],
    }


def resolve_alias(value: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    norm = normalize_alias(value)
    if not norm:
        return []
    if _table_exists("vkpi_product_aliases"):
        rows = get_conn().execute(
            """
            SELECT sku, alias, alias_norm, alias_type, source_table, source_id, confidence
            FROM vkpi_product_aliases
            WHERE alias_norm=?
            ORDER BY confidence DESC, sku ASC
            LIMIT ?
            """,
            (norm, max(1, min(50, int(limit or 10)))),
        ).fetchall()
        return [_row_dict(row) for row in rows]
    products = _fetch_products(1000)
    matches = []
    for product in products:
        for alias in generated_aliases_for_product(product):
            if alias["alias_norm"] == norm:
                matches.append(alias)
    matches.sort(key=lambda item: (-float(item["confidence"]), item["sku"]))
    return matches[: max(1, min(50, int(limit or 10)))]
