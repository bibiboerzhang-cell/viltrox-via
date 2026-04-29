"""
services/via/stock_watch.py — public stock watch snippets for Via
"""
from __future__ import annotations

from typing import Any

from app.services.intelligence import get_latest_bh_products


def build_via_stock_watch(limit: int = 4) -> dict[str, Any]:
    requested = max(1, min(int(limit or 4), 8))
    rows = get_latest_bh_products(limit=max(12, requested * 3))
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        title = str(row.get("title") or "").strip()
        sku = str(row.get("sku") or "").strip()
        key = f"{title}|{sku}".lower()
        if not title or key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "title": title,
                "price": float(row.get("price") or 0.0),
                "rating": float(row.get("rating") or 0.0),
                "review_count": int(row.get("review_count") or 0),
                "url": str(row.get("url") or "").strip(),
                "image_url": str(row.get("image_url") or "").strip(),
                "sku": sku,
                "in_stock": bool(row.get("in_stock")),
                "snapshot_at": str(row.get("snapshot_at") or "").strip(),
            }
        )

    in_stock = [item for item in items if item["in_stock"]]
    chosen = (in_stock or items)[:requested]
    latest_snapshot = next((item["snapshot_at"] for item in chosen if item.get("snapshot_at")), "")

    return {
        "items": chosen,
        "count": len(chosen),
        "snapshot_at": latest_snapshot,
        "has_live_signal": bool(chosen),
    }
