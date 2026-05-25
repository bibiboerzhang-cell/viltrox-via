#!/usr/bin/env python3
"""Audit and repair Daily Top100 candidate source plumbing.

This script is intentionally conservative:
- default mode is read-only;
- it never treats the bridge placeholder product_sku=kol_pool as a real monitored product;
- it does not call live crawlers or external APIs;
- writes only happen with --apply.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import asyncio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ENVIRONMENT", "local")

from app.db.connection import close_db_runtime, get_conn  # noqa: E402
from app.domains import analytics  # noqa: E402
from app.domains.analytics.schema import ensure_vkpi_analytics_schema  # noqa: E402

BRIDGE_SKUS = {"", "kol_pool", "kol-pool", "pool", "unknown", "n/a", "none"}
DEFAULT_PLATFORMS = ["youtube", "instagram", "tiktok", "xiaohongshu"]


@dataclass(frozen=True)
class ProductCandidate:
    product_sku: str
    product_name: str
    source: str
    evidence_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "product_sku": self.product_sku,
            "product_name": self.product_name,
            "source": self.source,
            "evidence_count": self.evidence_count,
        }


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in get_conn().execute(query, params).fetchall()]
    except Exception:
        return []


def _is_real_sku(value: Any) -> bool:
    sku = str(value or "").strip()
    lower = sku.lower()
    if not sku or lower in BRIDGE_SKUS:
        return False
    if lower.isdigit():
        return False
    if lower.startswith(("smoke-", "vkpi-smoke-", "vkpi-")) or "smoke" in lower:
        return False
    return True


def _dedupe(candidates: list[ProductCandidate], limit: int) -> list[ProductCandidate]:
    seen: set[str] = set()
    out: list[ProductCandidate] = []
    for candidate in candidates:
        key = candidate.product_sku.strip().lower()
        if not key or key in seen or not _is_real_sku(key):
            continue
        seen.add(key)
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


def monitored_products() -> list[dict[str, Any]]:
    ensure_vkpi_analytics_schema()
    return list(analytics.list_monitored_products(limit=300).get("products") or [])


def suggestion_sku_counts() -> list[dict[str, Any]]:
    ensure_vkpi_analytics_schema()
    return _rows(
        """
        SELECT COALESCE(NULLIF(source_product_sku, ''), '(blank)') AS product_sku,
               status,
               platform,
               COUNT(*) AS count
        FROM vkpi_outreach_suggestions
        GROUP BY COALESCE(NULLIF(source_product_sku, ''), '(blank)'), status, platform
        ORDER BY count DESC, product_sku ASC
        LIMIT 100
        """
    )


def digest_snapshot() -> dict[str, Any]:
    ensure_vkpi_analytics_schema()
    digest_rows = _rows(
        """
        SELECT digest_date,
               COUNT(*) AS staff_digest_count,
               COALESCE(SUM(item_count), 0) AS item_count,
               MAX(generated_at) AS last_generated_at
        FROM vkpi_staff_outreach_digests
        GROUP BY digest_date
        ORDER BY digest_date DESC
        LIMIT 5
        """
    )
    item_rows = _rows(
        """
        SELECT s.source_product_sku AS product_sku,
               COUNT(*) AS assigned_count
        FROM vkpi_staff_outreach_digest_items i
        JOIN vkpi_outreach_suggestions s ON s.id = i.suggestion_id
        GROUP BY s.source_product_sku
        ORDER BY assigned_count DESC
        LIMIT 20
        """
    )
    return {"recent_digests": digest_rows, "assigned_by_product": item_rows}


def discover_product_candidates(limit: int = 20) -> list[ProductCandidate]:
    """Find real product SKUs already present in local business tables."""
    safe_limit = max(1, min(100, int(limit or 20)))
    candidates: list[ProductCandidate] = []

    for row in _rows(
        """
        SELECT product_sku, COALESCE(NULLIF(product_name, ''), NULLIF(name, ''), product_sku) AS product_name, COUNT(*) AS count
        FROM vkpi_product_launches
        WHERE COALESCE(product_sku, '') <> ''
        GROUP BY product_sku, COALESCE(NULLIF(product_name, ''), NULLIF(name, ''), product_sku)
        ORDER BY count DESC, product_sku ASC
        LIMIT ?
        """,
        (safe_limit,),
    ):
        candidates.append(ProductCandidate(str(row.get("product_sku") or ""), str(row.get("product_name") or row.get("product_sku") or ""), "product_launches", _safe_int(row.get("count"))))

    for row in _rows(
        """
        SELECT product_sku, COALESCE(NULLIF(product_name, ''), product_sku) AS product_name, COUNT(*) AS count
        FROM vkpi_product_cost_catalog
        WHERE COALESCE(product_sku, '') <> ''
        GROUP BY product_sku, COALESCE(NULLIF(product_name, ''), product_sku)
        ORDER BY count DESC, product_sku ASC
        LIMIT ?
        """,
        (safe_limit,),
    ):
        candidates.append(ProductCandidate(str(row.get("product_sku") or ""), str(row.get("product_name") or row.get("product_sku") or ""), "product_cost_catalog", _safe_int(row.get("count"))))

    for row in _rows(
        """
        SELECT product_sku, COALESCE(NULLIF(product_name, ''), product_sku) AS product_name, COUNT(*) AS count
        FROM vkpi_projects
        WHERE COALESCE(product_sku, '') <> ''
        GROUP BY product_sku, COALESCE(NULLIF(product_name, ''), product_sku)
        ORDER BY count DESC, product_sku ASC
        LIMIT ?
        """,
        (safe_limit,),
    ):
        candidates.append(ProductCandidate(str(row.get("product_sku") or ""), str(row.get("product_name") or row.get("product_sku") or ""), "projects", _safe_int(row.get("count"))))

    for row in _rows(
        """
        SELECT source_product_sku AS product_sku, source_product_sku AS product_name, COUNT(*) AS count
        FROM vkpi_outreach_suggestions
        WHERE COALESCE(source_product_sku, '') <> ''
        GROUP BY source_product_sku
        ORDER BY count DESC, source_product_sku ASC
        LIMIT ?
        """,
        (safe_limit,),
    ):
        sku = str(row.get("product_sku") or "")
        if _is_real_sku(sku):
            candidates.append(ProductCandidate(sku, sku, "outreach_suggestions", _safe_int(row.get("count"))))

    return _dedupe(candidates, safe_limit)


def audit_state(limit: int = 20) -> dict[str, Any]:
    products = monitored_products()
    suggestion_counts = suggestion_sku_counts()
    candidates = discover_product_candidates(limit=limit)
    enabled_products = [row for row in products if str(row.get("enabled") or "1").lower() not in {"0", "false", "no"}]
    real_suggestion_skus = sorted({str(row.get("product_sku") or "").strip() for row in suggestion_counts if _is_real_sku(row.get("product_sku"))})
    bridge_count = sum(_safe_int(row.get("count")) for row in suggestion_counts if not _is_real_sku(row.get("product_sku")))

    blockers: list[str] = []
    if not products:
        blockers.append("no_monitored_products")
    elif not enabled_products:
        blockers.append("all_monitored_products_disabled")
    if not real_suggestion_skus and bridge_count:
        blockers.append("suggestions_are_bridge_only")
    if not candidates and not products:
        blockers.append("no_local_product_candidates")

    return {
        "status": "blocked" if blockers else "ok",
        "blockers": blockers,
        "monitored_products_count": len(products),
        "enabled_monitored_products_count": len(enabled_products),
        "monitored_products": products[:20],
        "suggestion_sku_counts": suggestion_counts,
        "real_suggestion_skus": real_suggestion_skus,
        "bridge_or_blank_suggestion_count": bridge_count,
        "product_candidates": [candidate.as_dict() for candidate in candidates],
        "digest_snapshot": digest_snapshot(),
        "next_actions": _next_actions(blockers, candidates),
    }


def _next_actions(blockers: list[str], candidates: list[ProductCandidate]) -> list[str]:
    actions: list[str] = []
    if "no_monitored_products" in blockers:
        if candidates:
            actions.append("Run with --apply --from-catalog to seed monitored products from existing product/project tables.")
        actions.append("Or run with --apply --product-sku '<real product sku>' --product-name '<name>' to seed one explicit product.")
    if "suggestions_are_bridge_only" in blockers:
        actions.append("Current Daily Top100 candidates are KOL Pool bridge rows only; run analytics monitor after real products are configured to create product-specific suggestions.")
    if not actions:
        actions.append("No source blocker detected; continue with endpoint/browser QA for Daily Top100.")
    return actions


def _parse_platforms(value: str) -> list[str]:
    items = [item.strip().lower() for item in str(value or "").split(",") if item.strip()]
    return items or list(DEFAULT_PLATFORMS)


def upsert_candidates(candidates: list[ProductCandidate], *, platforms: list[str], apply: bool) -> dict[str, Any]:
    products_before = {str(row.get("product_sku") or "").strip().lower() for row in monitored_products()}
    planned: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in candidates:
        sku = candidate.product_sku.strip()
        if not _is_real_sku(sku):
            skipped.append({**candidate.as_dict(), "reason": "not_real_product_sku"})
            continue
        if sku.lower() in products_before:
            skipped.append({**candidate.as_dict(), "reason": "already_monitored"})
            continue
        payload = {
            "product_sku": sku,
            "product_name": candidate.product_name or sku,
            "platforms": platforms,
            "keywords": [candidate.product_name or sku, sku],
            "enabled": True,
        }
        planned.append({**candidate.as_dict(), "platforms": platforms})
        if apply:
            result = analytics.upsert_monitored_product(payload, staff={"id": 0, "role": "admin", "is_owner": 1})
            applied.append(result.get("product") or payload)
    return {"apply": apply, "planned": planned, "applied": applied, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit/repair Daily Top100 product source plumbing")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS), help="comma-separated monitor platforms")
    parser.add_argument("--from-catalog", action="store_true", help="seed monitored products from local product/project tables")
    parser.add_argument("--product-sku", default="", help="explicit product sku to monitor")
    parser.add_argument("--product-name", default="", help="explicit product name")
    parser.add_argument("--apply", action="store_true", help="write monitored products; default is dry-run")
    args = parser.parse_args()

    ensure_vkpi_analytics_schema()
    platforms = _parse_platforms(args.platforms)
    result: dict[str, Any] = {"audit": audit_state(limit=args.limit)}

    candidates: list[ProductCandidate] = []
    explicit_sku = str(args.product_sku or "").strip()
    if explicit_sku:
        candidates.append(ProductCandidate(explicit_sku, str(args.product_name or explicit_sku), "explicit", 1))
    if args.from_catalog:
        candidates.extend(discover_product_candidates(limit=args.limit))
    if candidates:
        result["repair"] = upsert_candidates(_dedupe(candidates, args.limit), platforms=platforms, apply=bool(args.apply))
        result["audit_after"] = audit_state(limit=args.limit) if args.apply else None

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        audit = result["audit"]
        print(f"status={audit['status']}")
        print(f"blockers={','.join(audit['blockers']) or 'none'}")
        print(f"monitored_products={audit['monitored_products_count']} enabled={audit['enabled_monitored_products_count']}")
        print(f"bridge_or_blank_suggestions={audit['bridge_or_blank_suggestion_count']}")
        print("product_candidates:")
        for item in audit["product_candidates"][: args.limit]:
            print(f"- {item['product_sku']} | {item['product_name']} | {item['source']} | evidence={item['evidence_count']}")
        print("next_actions:")
        for action in audit["next_actions"]:
            print(f"- {action}")
        if "repair" in result:
            repair = result["repair"]
            print(f"repair_apply={repair['apply']} planned={len(repair['planned'])} applied={len(repair['applied'])} skipped={len(repair['skipped'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        asyncio.run(close_db_runtime())
