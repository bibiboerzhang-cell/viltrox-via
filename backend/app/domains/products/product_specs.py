"""Normalized official product spec facts for Product Fit."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.domains.products import product_aliases


logger = get_logger(__name__)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _is_lens_like(product: dict[str, Any], specs: dict[str, Any]) -> bool:
    text = " ".join(
        [
            _text(product.get("category_main")),
            _text(product.get("category_detail")),
            _text(product.get("model_name")),
            _text(product.get("marketing_name")),
            _text(specs.get("official_product_type")),
            " ".join(str(tag) for tag in (_loads(product.get("fit_tags_json"), []) or [])),
        ]
    ).lower()
    if any(token in text for token in ("lens adapter", "mount adapter", "teleconverter")):
        return False
    return "lens" in text or "cine" in text


def _mount_norm(value: Any) -> str:
    norm = product_aliases.normalize_alias(value)
    if norm in {"fe mount", "e mount", "sony e mount"}:
        return "sony_e"
    if norm == "z mount":
        return "nikon_z"
    if norm == "x mount":
        return "fuji_x"
    if norm in {"m43", "m4 3", "mft", "micro four thirds"}:
        return "m43"
    if norm == "l mount":
        return "l_mount"
    if norm == "pl mount":
        return "pl_mount"
    return norm.replace(" ", "_")


def _parse_mm_values(*values: Any) -> tuple[str, float | None, float | None]:
    text = " ".join(_text(value) for value in values if _text(value))
    matches = re.findall(r"(?<![A-Za-z0-9.])(\d{1,4}(?:\.\d+)?)\s*mm\b", text, flags=re.IGNORECASE)
    values_mm: list[float] = []
    for raw in matches:
        parsed = _float(raw)
        if parsed is not None and 0 < parsed <= 2000:
            values_mm.append(parsed)
    if not values_mm:
        return "", None, None
    unique = sorted(set(values_mm))
    label = "/".join(f"{value:g}mm" for value in unique[:8])
    return label, unique[0], unique[-1]


def _parse_aperture(*values: Any) -> tuple[str, float | None, str]:
    text = " ".join(_text(value) for value in values if _text(value))
    matches = re.findall(r"\b[FT]\s*/?\s*(\d+(?:\.\d+)?)\b", text, flags=re.IGNORECASE)
    parsed: list[float] = []
    for raw in matches:
        value = _float(raw)
        if value is not None and 0.3 <= value <= 64:
            parsed.append(value)
    if not parsed:
        return "", None, ""
    unique = sorted(set(parsed))
    return f"F{unique[0]:g}", unique[0], f"F{unique[-1]:g}"


def _parse_weight_grams(*values: Any) -> int | None:
    for value in values:
        parsed = _float(value)
        if parsed is not None and parsed > 0:
            return int(round(parsed))
    text = " ".join(_text(value) for value in values if _text(value))
    matches = re.findall(r"(\d{1,5}(?:\.\d+)?)\s*g\b", text, flags=re.IGNORECASE)
    for raw in matches:
        parsed = _float(raw)
        if parsed is not None and parsed > 0:
            return int(round(parsed))
    return None


def _parse_filter_size_mm(*values: Any) -> int | None:
    text = " ".join(_text(value) for value in values if _text(value))
    matches = re.findall(r"(?:filter|phi|diameter|[Φø])\s*[:：]?\s*(\d{1,3})\s*mm", text, flags=re.IGNORECASE)
    if not matches:
        matches = re.findall(r"[Φø]\s*(\d{1,3})\s*mm", text, flags=re.IGNORECASE)
    for raw in matches:
        parsed = _float(raw)
        if parsed is not None and 10 <= parsed <= 200:
            return int(round(parsed))
    return None


def normalized_spec_fact(product: dict[str, Any]) -> dict[str, Any]:
    specs = _loads(product.get("specs_json") or product.get("specs"), {})
    fit_tags = _loads(product.get("fit_tags_json") or product.get("fit_tags"), [])
    if not isinstance(specs, dict):
        specs = {}
    if not isinstance(fit_tags, list):
        fit_tags = []
    sku = _text(product.get("sku")).upper()
    fit_tag_text = " ".join(str(tag) for tag in fit_tags)
    focal_label, focal_min, focal_max = _parse_mm_values(
        product.get("sku"),
        product.get("model_name"),
        product.get("marketing_name"),
        fit_tag_text,
        specs.get("focal_length"),
    )
    aperture_label, max_aperture, min_aperture_label = _parse_aperture(
        product.get("sku"),
        product.get("model_name"),
        product.get("marketing_name"),
        fit_tag_text,
        specs.get("aperture"),
        specs.get("maximum_aperture"),
    )
    mount = _text(product.get("mount"))
    lens_mount = _text(specs.get("lens_mount"))
    weight_grams = _parse_weight_grams(specs.get("variant_weight_grams"), specs.get("weight"), specs.get("lens_size"))
    filter_size_mm = _parse_filter_size_mm(specs.get("filter_size"), specs.get("lens_size"))
    price_usd = _float(product.get("price_usd"))
    source_confidence = _float(product.get("source_confidence")) or 0.0
    lens_like = _is_lens_like(product, specs)
    required = ["price_usd", "product_url", "source_confidence"]
    if lens_like:
        required.extend(["mount", "focal_length", "max_aperture", "weight_grams", "fit_tags"])
    fields = {
        "price_usd": price_usd,
        "product_url": _text(product.get("product_url")),
        "source_confidence": source_confidence if source_confidence > 0 else None,
        "mount": mount or lens_mount,
        "focal_length": focal_min,
        "max_aperture": max_aperture,
        "weight_grams": weight_grams,
        "fit_tags": fit_tags,
    }
    missing = [field for field in required if not fields.get(field)]
    completeness = round(((len(required) - len(missing)) / max(1, len(required))) * 100, 2)
    return {
        "sku": sku,
        "category_main": _text(product.get("category_main")),
        "category_detail": _text(product.get("category_detail")),
        "series": _text(product.get("series")),
        "mount": mount,
        "mount_norm": _mount_norm(mount or lens_mount),
        "lens_mount": lens_mount,
        "lens_mount_norm": _mount_norm(lens_mount or mount),
        "focal_length_label": focal_label,
        "focal_length_min_mm": focal_min,
        "focal_length_max_mm": focal_max,
        "max_aperture_label": aperture_label,
        "max_aperture_f": max_aperture,
        "min_aperture_label": min_aperture_label,
        "weight_grams": weight_grams,
        "filter_size_mm": filter_size_mm,
        "price_usd": price_usd,
        "product_url": _text(product.get("product_url")),
        "fit_tags_json": _json(fit_tags),
        "source_confidence": source_confidence,
        "completeness_score": completeness,
        "missing_fields_json": _json(missing),
        "raw_spec_fields_json": _json(
            {
                "lens_like": lens_like,
                "focal_length": specs.get("focal_length"),
                "aperture": specs.get("aperture"),
                "lens_mount": specs.get("lens_mount"),
                "weight": specs.get("weight"),
                "variant_weight_grams": specs.get("variant_weight_grams"),
                "filter_size": specs.get("filter_size"),
                "official_tags": specs.get("official_tags"),
            }
        ),
        "lens_like": lens_like,
    }


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


def ensure_product_spec_fact_schema() -> None:
    conn = get_conn()
    if is_postgres_runtime():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_product_spec_facts (
                sku TEXT PRIMARY KEY REFERENCES vkpi_products(sku) ON DELETE CASCADE,
                category_main TEXT NOT NULL DEFAULT '',
                category_detail TEXT NOT NULL DEFAULT '',
                series TEXT NOT NULL DEFAULT '',
                mount TEXT NOT NULL DEFAULT '',
                mount_norm TEXT NOT NULL DEFAULT '',
                lens_mount TEXT NOT NULL DEFAULT '',
                lens_mount_norm TEXT NOT NULL DEFAULT '',
                focal_length_label TEXT NOT NULL DEFAULT '',
                focal_length_min_mm NUMERIC(8,2),
                focal_length_max_mm NUMERIC(8,2),
                max_aperture_label TEXT NOT NULL DEFAULT '',
                max_aperture_f NUMERIC(5,2),
                min_aperture_label TEXT NOT NULL DEFAULT '',
                weight_grams INTEGER,
                filter_size_mm INTEGER,
                price_usd NUMERIC(10,2),
                product_url TEXT NOT NULL DEFAULT '',
                fit_tags_json TEXT NOT NULL DEFAULT '[]',
                source_confidence NUMERIC(4,2) NOT NULL DEFAULT 0,
                completeness_score NUMERIC(5,2) NOT NULL DEFAULT 0,
                missing_fields_json TEXT NOT NULL DEFAULT '[]',
                raw_spec_fields_json TEXT NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vkpi_product_spec_facts (
                sku TEXT PRIMARY KEY REFERENCES vkpi_products(sku) ON DELETE CASCADE,
                category_main TEXT NOT NULL DEFAULT '',
                category_detail TEXT NOT NULL DEFAULT '',
                series TEXT NOT NULL DEFAULT '',
                mount TEXT NOT NULL DEFAULT '',
                mount_norm TEXT NOT NULL DEFAULT '',
                lens_mount TEXT NOT NULL DEFAULT '',
                lens_mount_norm TEXT NOT NULL DEFAULT '',
                focal_length_label TEXT NOT NULL DEFAULT '',
                focal_length_min_mm REAL,
                focal_length_max_mm REAL,
                max_aperture_label TEXT NOT NULL DEFAULT '',
                max_aperture_f REAL,
                min_aperture_label TEXT NOT NULL DEFAULT '',
                weight_grams INTEGER,
                filter_size_mm INTEGER,
                price_usd REAL,
                product_url TEXT NOT NULL DEFAULT '',
                fit_tags_json TEXT NOT NULL DEFAULT '[]',
                source_confidence REAL NOT NULL DEFAULT 0,
                completeness_score REAL NOT NULL DEFAULT 0,
                missing_fields_json TEXT NOT NULL DEFAULT '[]',
                raw_spec_fields_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_mount ON vkpi_product_spec_facts(mount_norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_focal ON vkpi_product_spec_facts(focal_length_min_mm, focal_length_max_mm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_aperture ON vkpi_product_spec_facts(max_aperture_f)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vkpi_product_spec_facts_completeness ON vkpi_product_spec_facts(completeness_score DESC)")
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


def _upsert_facts(facts: list[dict[str, Any]]) -> int:
    ensure_product_spec_fact_schema()
    conn = get_conn()
    now = _now()
    written = 0
    columns = [
        "sku",
        "category_main",
        "category_detail",
        "series",
        "mount",
        "mount_norm",
        "lens_mount",
        "lens_mount_norm",
        "focal_length_label",
        "focal_length_min_mm",
        "focal_length_max_mm",
        "max_aperture_label",
        "max_aperture_f",
        "min_aperture_label",
        "weight_grams",
        "filter_size_mm",
        "price_usd",
        "product_url",
        "fit_tags_json",
        "source_confidence",
        "completeness_score",
        "missing_fields_json",
        "raw_spec_fields_json",
        "updated_at",
    ]
    update_columns = [column for column in columns if column != "sku"]
    for fact in facts:
        values = [fact.get(column) for column in columns]
        values[-1] = now
        conn.execute(
            f"""
            INSERT INTO vkpi_product_spec_facts ({', '.join(columns)})
            VALUES ({', '.join(['?'] * len(columns))})
            ON CONFLICT(sku) DO UPDATE SET
                {', '.join(f'{column}=excluded.{column}' for column in update_columns)}
            """,
            tuple(values),
        )
        written += 1
    conn.commit()
    return written


def _missing_counts(facts: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for fact in facts:
        for field in _loads(fact.get("missing_fields_json"), []):
            counts[str(field)] += 1
    return dict(sorted(counts.items()))


def build_spec_readiness_report(*, limit: int = 500, apply: bool = False, ensure_schema: bool = False) -> dict[str, Any]:
    if ensure_schema:
        ensure_product_spec_fact_schema()
    products = _fetch_products(limit)
    facts = [normalized_spec_fact(product) for product in products if _text(product.get("sku"))]
    lens_facts = [fact for fact in facts if bool(fact.get("lens_like"))]
    complete_facts = [fact for fact in facts if float(fact.get("completeness_score") or 0) >= 100]
    complete_lens_facts = [fact for fact in lens_facts if float(fact.get("completeness_score") or 0) >= 100]
    facts_written = _upsert_facts(facts) if apply and facts else 0
    fact_table_exists = _table_exists("vkpi_product_spec_facts")
    checks = {
        "product_catalog_present": bool(products),
        "spec_facts_generated": bool(facts),
        "spec_fact_table_exists": fact_table_exists or bool(apply),
        "lens_core_specs_present": bool(complete_lens_facts),
        "provider_calls_blocked": True,
        "llm_calls_blocked": True,
        "sync_blocked": True,
    }
    avg_completeness = round(sum(float(fact.get("completeness_score") or 0) for fact in facts) / max(1, len(facts)), 2)
    avg_lens_completeness = round(sum(float(fact.get("completeness_score") or 0) for fact in lens_facts) / max(1, len(lens_facts)), 2)
    low_samples = [
        {
            "sku": fact.get("sku"),
            "category": fact.get("category_detail") or fact.get("category_main"),
            "completeness_score": fact.get("completeness_score"),
            "missing_fields": _loads(fact.get("missing_fields_json"), []),
        }
        for fact in sorted(facts, key=lambda item: float(item.get("completeness_score") or 0))[:20]
    ]
    return {
        "mode": "p5_63_sku_spec_readiness",
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
            "fact_count": len(facts),
            "facts_written": facts_written,
            "lens_like_count": len(lens_facts),
            "complete_count": len(complete_facts),
            "complete_lens_count": len(complete_lens_facts),
            "avg_completeness_score": avg_completeness,
            "avg_lens_completeness_score": avg_lens_completeness,
        },
        "missing_field_counts": _missing_counts(facts),
        "low_completeness_sample": low_samples,
        "sample_facts": facts[:20],
    }
