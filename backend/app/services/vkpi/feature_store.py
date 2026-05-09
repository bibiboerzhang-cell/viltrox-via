"""Feature snapshots for recommendation and future ML training."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.db.connection import get_conn
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _loads(value: Any, default: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return default


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
