"""Feature snapshots for recommendation and future ML training."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_conn
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


from app.core.coerce import _text


def _catalog_key(value: Any) -> str:
    text = _text(value).lower()
    text = text.replace("f1.2", "f12").replace("f1.4", "f14").replace("f1.7", "f17").replace("f1.8", "f18")
    text = text.replace("f2.0", "f20").replace("f2.5", "f25").replace("f2.8", "f28").replace("f3.5", "f35").replace("f4.0", "f40")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _sku_variants(value: Any) -> set[str]:
    raw = _text(value).upper()
    if not raw:
        return set()
    normalized = raw
    for before, after in (
        ("F1.2", "F12"),
        ("F1.4", "F14"),
        ("F1.7", "F17"),
        ("F1.8", "F18"),
        ("F2.0", "F20"),
        ("F2.5", "F25"),
        ("F2.8", "F28"),
        ("F3.5", "F35"),
        ("F4.0", "F40"),
    ):
        normalized = normalized.replace(before, after)
    slug = re.sub(r"[^A-Z0-9]+", "-", normalized).strip("-")
    return {item for item in {raw, slug} if item}


def _compact_specs(value: Any) -> dict[str, Any]:
    specs = _loads(value, {})
    if not isinstance(specs, dict):
        return {}
    keep = ("lens_mount", "focal_length", "aperture", "lens_elements", "weight", "filter_size")
    return {key: specs.get(key) for key in keep if _text(specs.get(key))}


def _catalog_product(row: Any) -> dict[str, Any]:
    item = dict(row)
    return {
        "sku": _text(item.get("sku")),
        "model_name": _text(item.get("model_name")),
        "marketing_name": _text(item.get("marketing_name")),
        "category_main": _text(item.get("category_main")),
        "category_detail": _text(item.get("category_detail")),
        "series": _text(item.get("series")),
        "mount": _text(item.get("mount")),
        "price_usd": float(item.get("price_usd")) if item.get("price_usd") not in (None, "") else None,
        "product_url": _text(item.get("product_url")),
        "source_confidence": float(item.get("source_confidence") or 0),
        "specs": _compact_specs(item.get("specs_json")),
    }


def _matched_catalog_products(product_sku: Any, product_name: Any, limit: int = 8) -> list[dict[str, Any]]:
    needles = [_catalog_key(product_sku), _catalog_key(product_name)]
    needles = [needle for needle in needles if needle]
    sku_variants = _sku_variants(product_sku) | _sku_variants(product_name)
    if not needles and not sku_variants:
        return []
    required_tokens: set[str] = set()
    for needle in needles:
        required_tokens.update(re.findall(r"\b\d{1,3}mm\b", needle))
        required_tokens.update(re.findall(r"\bf\d{2}\b", needle))
    try:
        rows = get_conn().execute(
            """
            SELECT sku, category_main, category_detail, model_name, marketing_name,
                   price_usd, series, mount, product_url, specs_json, source_confidence
            FROM vkpi_products
            WHERE LOWER(COALESCE(category_main, '')) IN ('lens', 'cine lens')
               OR LOWER(COALESCE(category_detail, '')) LIKE ?
            ORDER BY source_confidence DESC, sku ASC
            LIMIT 600
            """,
            ("%lens%",),
        ).fetchall()
    except Exception:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        product = _catalog_product(row)
        sku = _text(product.get("sku")).upper()
        if not sku or sku in seen:
            continue
        if not _text(product.get("product_url")) and float(product.get("source_confidence") or 0) < 0.5:
            continue
        haystack = _catalog_key(f"{sku} {product.get('model_name')} {product.get('marketing_name')}")
        if required_tokens and not all(token in haystack for token in required_tokens):
            continue
        score = 0.0
        if sku in sku_variants:
            score = 100.0
        elif any(needle and (needle in haystack or haystack in needle) for needle in needles):
            score = 80.0
        else:
            for needle in needles:
                tokens = [token for token in needle.split() if token not in {"viltrox", "lens", "for", "mount", "full", "frame", "aps", "c"}]
                if tokens:
                    overlap = sum(1 for token in tokens if token in haystack)
                    score = max(score, (overlap / len(tokens)) * 70.0)
        if score < 35.0:
            continue
        seen.add(sku)
        source_bonus = (float(product.get("source_confidence") or 0) * 25.0) + (10.0 if _text(product.get("product_url")) else 0.0)
        scored.append((score + source_bonus, product))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [product for _, product in scored[: max(1, min(20, int(limit or 8)))]]


def snapshot_features(recommendation_id: int | None = None, kol_pool_id: int | None = None, launch_id: int | None = None) -> dict[str, Any]:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    kol = conn.execute("SELECT * FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id or 0),)).fetchone() if kol_pool_id else None
    launch = conn.execute("SELECT * FROM vkpi_product_launches WHERE id=?", (int(launch_id or 0),)).fetchone() if launch_id else None
    features: dict[str, Any] = {"snapshot_at": _utcnow(), "recommendation_id": recommendation_id, "kol_pool_id": kol_pool_id, "launch_id": launch_id}
    if kol:
        item = dict(kol)
        features.update(
            {
                "platform": item.get("platform"),
                "handle": item.get("handle"),
                "followers": item.get("followers"),
                "posts_count": item.get("posts_count"),
                "avg_views": item.get("avg_views"),
                "avg_likes": item.get("avg_likes"),
                "avg_comments": item.get("avg_comments"),
                "engagement_rate": item.get("engagement_rate"),
                "primary_topic": item.get("primary_topic"),
                "sync_status": item.get("sync_status"),
                "source_type": item.get("source_type"),
            }
        )
    if launch:
        item = dict(launch)
        features["launch"] = {
            "product_sku": item.get("product_sku"),
            "product_name": item.get("product_name"),
            "category": item.get("category"),
            "target_platforms": _loads(item.get("target_platforms_json"), []),
            "target_market": item.get("target_market"),
        }
        catalog_products = _matched_catalog_products(item.get("product_sku"), item.get("product_name"))
        features["matched_catalog_products"] = catalog_products
        features["matched_catalog_product_count"] = len(catalog_products)
    return features


def get_features_at_time(kol_pool_id: int, timestamp: str | None = None) -> dict[str, Any]:
    return snapshot_features(kol_pool_id=kol_pool_id) | {"requested_at": timestamp or "current"}


def list_feature_names() -> list[str]:
    return [
        "platform",
        "followers",
        "posts_count",
        "avg_views",
        "avg_likes",
        "avg_comments",
        "engagement_rate",
        "primary_topic",
        "sync_status",
        "launch.product_sku",
        "launch.category",
        "launch.target_platforms",
    ]
