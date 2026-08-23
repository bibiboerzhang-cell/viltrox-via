"""Feature snapshots for recommendation and future ML training."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.platform.db.schema_product_industry import ensure_vkpi_product_industry_schema

logger = get_logger(__name__)


class HistoricalFeatureSnapshotUnavailable(LookupError):
    """Raised when a point-in-time request cannot be proven from frozen data."""


# v2(2026-08-23,L 车道):快照追加 ``derived`` 子字典(feature_store_derived,≤20 个只读派生强特征)
# 与 ``derived_feature_version``;v1 快照仍可被 get_features_at_time 读回(_COMPATIBLE_SCHEMA_VERSIONS)。
FEATURE_SNAPSHOT_SCHEMA_VERSION = "vkpi_kol_feature_snapshot_v2"
_COMPATIBLE_SCHEMA_VERSIONS = frozenset({"", "vkpi_kol_feature_snapshot_v1", FEATURE_SNAPSHOT_SCHEMA_VERSION})
_HISTORICAL_CANDIDATE_LIMIT = 1000
_STANDARD_KOL_FEATURE_KEYS = frozenset(
    {
        "platform",
        "followers",
        "posts_count",
        "avg_views",
        "avg_likes",
        "avg_comments",
        "engagement_rate",
        "primary_topic",
        "sync_status",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_utc(value: Any, *, field: str) -> tuple[datetime, str]:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat().replace("+00:00", "Z")


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
    features: dict[str, Any] = {
        "feature_schema_version": FEATURE_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_at": _utcnow(),
        "recommendation_id": recommendation_id,
        "kol_pool_id": kol_pool_id,
        "launch_id": launch_id,
    }
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
        # v2 派生强特征(只读;任一来源缺料 = None;失败不拖垮快照本体)。
        try:
            from app.domains.recommendations import feature_store_derived

            features["derived"] = feature_store_derived.derived_features(int(kol_pool_id or 0), conn=conn, pool_row=item)
            features["derived_feature_version"] = feature_store_derived.DERIVED_FEATURE_VERSION
        except Exception:
            logger.warning("feature_store.derived_failed kol_pool_id=%s", kol_pool_id, exc_info=True)
            features["derived"] = {}
            features["derived_feature_version"] = ""
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


def get_features_at_time(
    kol_pool_id: int,
    timestamp: str | None = None,
    *,
    launch_id: int | None = None,
) -> dict[str, Any]:
    """Return only features that were frozen on or before ``timestamp``.

    A historical request must never fall back to the current KOL row: doing so
    would let training/evaluation observe facts that appeared after the decision
    time.  Existing persisted recommendation feature snapshots are the current
    frozen-by-application-convention source.  Callers receive an explicit exception when no usable
    snapshot exists so they can exclude the row instead of silently leaking the
    future.  A missing/current timestamp preserves the live-snapshot behavior,
    while labelling it as non-historical.
    """

    entity_id = int(kol_pool_id or 0)
    if entity_id <= 0:
        raise ValueError("kol_pool_id must be a positive integer")
    launch_scope = int(launch_id or 0)
    if launch_id is not None and launch_scope <= 0:
        raise ValueError("launch_id must be a positive integer when provided")
    requested = str(timestamp or "").strip()
    if not requested or requested.lower() == "current":
        current = snapshot_features(kol_pool_id=entity_id, launch_id=launch_scope or None)
        return current | {
            "requested_at": "current",
            "_point_in_time": {
                "status": "current_not_historical",
                "point_in_time": False,
                "source": "live_kol_snapshot",
                "entity_scope": "kol_launch" if launch_scope else "kol_only",
            },
        }

    requested_dt, requested_at = _as_utc(requested, field="timestamp")
    where_launch = " AND launch_id=?" if launch_scope else ""
    params: tuple[Any, ...] = (
        (entity_id, launch_scope, _HISTORICAL_CANDIDATE_LIMIT + 1)
        if launch_scope
        else (entity_id, _HISTORICAL_CANDIDATE_LIMIT + 1)
    )
    rows = get_conn().execute(
        f"""
        SELECT id, launch_id, feature_snapshot_json, created_at
        FROM vkpi_kol_recommendations
        WHERE kol_pool_id=?
          {where_launch}
          AND COALESCE(feature_snapshot_json, '{{}}')<>'{{}}'
        ORDER BY id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    if not rows:
        raise HistoricalFeatureSnapshotUnavailable(
            "no frozen recommendation feature snapshot exists at or before the requested time"
        )
    if len(rows) > _HISTORICAL_CANDIDATE_LIMIT:
        raise HistoricalFeatureSnapshotUnavailable(
            "historical feature candidate window is incomplete; indexed pagination is required"
        )

    valid: list[tuple[datetime, datetime, int, dict[str, Any], str, str, str]] = []
    rejected = 0
    for row in rows:
        item = dict(row)
        snapshot = _loads(item.get("feature_snapshot_json"), {})
        if not isinstance(snapshot, dict) or not snapshot:
            rejected += 1
            continue
        try:
            recorded_dt, recorded_at = _as_utc(item.get("created_at"), field="snapshot created_at")
            snapshot_dt, snapshot_at = _as_utc(snapshot.get("snapshot_at"), field="feature snapshot_at")
        except ValueError:
            rejected += 1
            continue
        if recorded_dt > requested_dt or snapshot_dt > requested_dt or snapshot_dt > recorded_dt:
            rejected += 1
            continue
        try:
            snapshot_entity_id = int(snapshot.get("kol_pool_id") or 0)
        except (TypeError, ValueError, OverflowError):
            rejected += 1
            continue
        if snapshot_entity_id != entity_id:
            rejected += 1
            continue
        if launch_scope:
            try:
                snapshot_launch_id = int(snapshot.get("launch_id") or 0)
                row_launch_id = int(item.get("launch_id") or 0)
            except (TypeError, ValueError, OverflowError):
                rejected += 1
                continue
            if snapshot_launch_id != launch_scope or row_launch_id != launch_scope:
                rejected += 1
                continue
        if not _STANDARD_KOL_FEATURE_KEYS.issubset(snapshot):
            rejected += 1
            continue
        schema_version = str(snapshot.get("feature_schema_version") or "").strip()
        if schema_version not in _COMPATIBLE_SCHEMA_VERSIONS:
            rejected += 1
            continue
        valid.append(
            (
                snapshot_dt,
                recorded_dt,
                int(item["id"]),
                snapshot,
                recorded_at,
                snapshot_at,
                schema_version or "legacy_unversioned_standard",
            )
        )

    if not valid:
        raise HistoricalFeatureSnapshotUnavailable(
            "no schema-compatible frozen feature snapshot exists at or before the requested time"
        )
    _, _, source_row_id, snapshot, recorded_at, snapshot_at, schema_version = max(
        valid,
        key=lambda candidate: (candidate[0], candidate[1], candidate[2]),
    )
    snapshot_sha256 = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if launch_scope:
        selected_features = dict(snapshot)
    else:
        selected_features = {
            key: snapshot.get(key)
            for key in (
                "feature_schema_version",
                "snapshot_at",
                "kol_pool_id",
                "derived",
                "derived_feature_version",
                *_STANDARD_KOL_FEATURE_KEYS,
            )
            if key in snapshot
        }

    return selected_features | {
        "requested_at": requested_at,
        "_point_in_time": {
            "status": "historical_frozen_snapshot",
            "point_in_time": True,
            "source": "vkpi_kol_recommendations.feature_snapshot_json",
            "source_row_id": source_row_id,
            "source_launch_id": launch_scope or None,
            "entity_scope": "kol_launch" if launch_scope else "kol_only",
            "recorded_at": recorded_at,
            "snapshot_at": snapshot_at,
            "snapshot_sha256": snapshot_sha256,
            "storage_mutability": "application_convention_not_db_enforced",
            "feature_schema_version": schema_version,
            "candidate_rows_examined": len(rows),
            "candidate_rows_rejected": rejected,
            "candidate_limit": _HISTORICAL_CANDIDATE_LIMIT,
        },
    }


def list_feature_names(*, include_derived: bool = True) -> list[str]:
    base = [
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
    if not include_derived:
        return base
    from app.domains.recommendations import feature_store_derived

    return base + [f"derived.{key}" for key in feature_store_derived.DERIVED_FEATURE_KEYS]


def derived_nonnull_rates(*, sample_limit: int = 300) -> dict[str, Any]:
    """v2 派生特征非空率(评估链用;只读)。"""
    from app.domains.recommendations import feature_store_derived

    return feature_store_derived.feature_coverage(sample_limit=sample_limit)
