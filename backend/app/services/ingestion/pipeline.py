"""
services/ingestion/pipeline.py — platform ingest normalization
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from app.core.config import PLATFORM_INGEST_SOURCES


JOB_TYPE_BY_SOURCE = {
    source: f"platform_ingest_{source}"
    for source in PLATFORM_INGEST_SOURCES
}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def build_platform_job_type(source_platform: str) -> str:
    source = (source_platform or "").strip().lower()
    if source not in JOB_TYPE_BY_SOURCE:
        raise ValueError(f"Unsupported ingest source: {source_platform}")
    return JOB_TYPE_BY_SOURCE[source]


def _first_truthy(payload: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = payload.get(key)
        if value:
            return value
    return default


def normalize_ingest_payload(source_platform: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    source = (source_platform or "").strip().lower()
    if source not in JOB_TYPE_BY_SOURCE:
        raise ValueError(f"Unsupported ingest source: {source_platform}")

    event_type = str(
        _first_truthy(payload, "event_type", "topic", "action", default="upsert")
    ).strip().lower()
    default_entity = "order" if source == "shopify" else "content"
    entity_type = str(
        _first_truthy(payload, "entity_type", "resource_type", default=default_entity)
    ).strip().lower()
    external_id = str(
        _first_truthy(
            payload,
            "external_id",
            "id",
            "order_id",
            "post_id",
            "content_id",
            "sku",
        )
    ).strip()
    creator_handle = str(
        _first_truthy(
            payload,
            "creator_handle",
            "handle",
            "username",
            "creator_code",
        )
    ).strip()
    region_code = str(
        _first_truthy(payload, "region_code", "country", "market")
    ).strip().upper()
    observed_value = _first_truthy(
        payload,
        "occurred_at",
        "observed_at",
        "published_at",
        "created_at",
    )
    observed_at = str(observed_value or _now()).strip()
    product_key = str(
        _first_truthy(payload, "product_key", "product_series", "sku")
    ).strip()
    product_label = str(
        _first_truthy(
            payload,
            "product_label",
            "product_name",
            default=product_key,
        )
    ).strip()
    identity = external_id or creator_handle or observed_at
    default_dedupe_key = f"{source}:{event_type}:{entity_type}:{identity}"
    dedupe_key = str(
        _first_truthy(payload, "dedupe_key", default=default_dedupe_key)
    ).strip()

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    return {
        "source_platform": source,
        "event_type": event_type,
        "entity_type": entity_type,
        "external_id": external_id,
        "source_url": str(
            _first_truthy(payload, "source_url", "url", default=external_id)
        ).strip(),
        "creator_handle": creator_handle,
        "user_id": int(payload.get("user_id") or 0),
        "submission_id": int(payload.get("submission_id") or 0),
        "region_code": region_code,
        "observed_at": observed_at,
        "dedupe_key": dedupe_key,
        "product_key": product_key,
        "product_label": product_label,
        "product_family": str(payload.get("product_family") or "").strip(),
        "mount_type": str(payload.get("mount_type") or "").strip(),
        "scene_tags": payload.get("scene_tags") or [],
        "feature_tags": payload.get("feature_tags") or [],
        "alias_terms": payload.get("alias_terms") or [],
        "metrics": metrics,
        "summary": str(payload.get("summary") or "").strip(),
        "note": str(payload.get("note") or "").strip(),
        "payload": payload,
    }


def summarize_ingest_payload(normalized: Dict[str, Any]) -> str:
    parts = [
        normalized.get("source_platform", ""),
        normalized.get("event_type", ""),
        normalized.get("entity_type", ""),
        normalized.get("external_id", "") or normalized.get("creator_handle", "") or normalized.get("source_url", ""),
    ]
    return " | ".join(part for part in parts if part)


async def enqueue_platform_ingest(queue, source_platform: str, payload: Dict[str, Any]) -> str:
    normalized = normalize_ingest_payload(source_platform, payload)
    job_type = build_platform_job_type(source_platform)
    return await queue.enqueue(
        job_type,
        normalized,
        submission_id=normalized.get("submission_id") or None,
    )
