"""Import verified Viltrox official SKU/spec rows into vkpi_products.

Sales price and product specs are intentionally stored in vkpi_products.
Internal unit/sample cost remains in vkpi_product_cost_catalog.

Usage:
    PYTHONPATH=backend .venv/bin/python -m scripts.vkpi_import_viltrox_product_catalog --dry-run
    PYTHONPATH=backend .venv/bin/python -m scripts.vkpi_import_viltrox_product_catalog --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.connection import close_db_runtime, get_conn
from app.domains.costs import ensure_product_catalog_schema


DEFAULT_SEED = Path(__file__).resolve().parents[1] / "backend" / "app" / "services" / "vkpi" / "viltrox_product_catalog_seed.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any, fallback: Any) -> str:
    payload = value if value not in (None, "") else fallback
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _price(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _confidence(value: Any) -> float:
    try:
        parsed = float(value if value is not None else 0)
    except (TypeError, ValueError):
        parsed = 0.0
    return round(max(0.0, min(1.0, parsed)), 2)


def _load_seed(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("seed file must contain a JSON array")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _validate(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _text(row.get("sku")):
        errors.append("missing_sku")
    if not _text(row.get("model_name")):
        errors.append("missing_model_name")
    if not _text(row.get("category_main")):
        errors.append("missing_category_main")
    if not _text(row.get("source_url") or row.get("product_url")):
        errors.append("missing_source_url")
    specs = row.get("specs") or row.get("specs_json") or {}
    if not isinstance(specs, dict) or not specs:
        errors.append("missing_specs")
    return errors


def import_catalog(seed_path: Path = DEFAULT_SEED, *, apply: bool = False, limit: int = 500) -> dict[str, Any]:
    ensure_product_catalog_schema()
    rows = _load_seed(seed_path)[: max(1, min(1000, int(limit or 500)))]
    now = _utcnow()
    prepared: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        item_errors = _validate(row)
        if item_errors:
            errors.append({"sku": _text(row.get("sku")), "errors": item_errors})
            continue
        prepared.append(row)

    if not apply:
        return {
            "dry_run": True,
            "seed_path": str(seed_path),
            "rows": len(rows),
            "valid": len(prepared),
            "invalid": len(errors),
            "errors": errors,
            "sample": prepared[:3],
        }

    conn = get_conn()
    upserted = 0
    for row in prepared:
        sku = _text(row.get("sku")).upper()
        specs = row.get("specs") or row.get("specs_json") or {}
        fit_tags = row.get("fit_tags") or row.get("fit_tags_json") or []
        conn.execute(
            """
            INSERT INTO vkpi_products (
                sku, category_main, category_detail, model_name, marketing_name,
                price_usd, status, description, source_file, series, mount,
                product_url, specs_json, fit_tags_json, source_url, source_checked_at,
                source_confidence, imported_at, updated_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(sku) DO UPDATE SET
                category_main=excluded.category_main,
                category_detail=excluded.category_detail,
                model_name=excluded.model_name,
                marketing_name=excluded.marketing_name,
                price_usd=excluded.price_usd,
                status=excluded.status,
                description=excluded.description,
                source_file=excluded.source_file,
                series=excluded.series,
                mount=excluded.mount,
                product_url=excluded.product_url,
                specs_json=excluded.specs_json,
                fit_tags_json=excluded.fit_tags_json,
                source_url=excluded.source_url,
                source_checked_at=excluded.source_checked_at,
                source_confidence=excluded.source_confidence,
                updated_at=excluded.updated_at
            """,
            (
                sku,
                _text(row.get("category_main")),
                _text(row.get("category_detail")),
                _text(row.get("model_name")),
                _text(row.get("marketing_name")),
                _price(row.get("price_usd")),
                _text(row.get("status") or "verified"),
                _text(row.get("description")),
                _text(row.get("source_file")),
                _text(row.get("series")),
                _text(row.get("mount")),
                _text(row.get("product_url")),
                _json(specs, {}),
                _json(fit_tags, []),
                _text(row.get("source_url") or row.get("product_url")),
                _text(row.get("source_checked_at")) or now,
                _confidence(row.get("source_confidence")),
                now,
                now,
            ),
        )
        upserted += 1
    conn.commit()
    return {
        "dry_run": False,
        "seed_path": str(seed_path),
        "rows": len(rows),
        "upserted": upserted,
        "invalid": len(errors),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.dry_run and args.apply:
        raise SystemExit("--dry-run and --apply cannot be used together")
    result = import_catalog(Path(args.seed), apply=bool(args.apply), limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    asyncio.run(close_db_runtime())


if __name__ == "__main__":
    main()
