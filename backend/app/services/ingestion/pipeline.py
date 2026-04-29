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


def normalize_ingest_payload(source_platform: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    source = (source_platform or "").strip().lower()
    if source not in JOB_TYPE_BY_SOURCE:
        raise ValueError(f"Unsupported ingest source: {source_platform}")

    event_type = str(payload.get("event_type") or payload.get("topic") or payload.get("action") or "upsert").strip().lower()
    entity_type = str(payload.get("entity_type") or payload.get("resource_type") or ("order" if source == "shopify" else "content")).strip().lower()
    external_id = str(
        payload.get("external_id")
        or payload.get("id")
        or payload.get("order_id")
        or payload.get("post_id")
        or payload.get("content_id")
        or payload.get("sku")
        or ""
    ).strip()
    creator_handle = str(
        payload.get("creator_handle")
        or payload.get("handle")
        or payload.get("username")
        or payload.get("creator_code")
        or ""
    ).strip()
    region_code = str(
        payload.get("region_code")
        or payload.get("country")
        or payload.get("market")
        or ""
    ).strip().upper()
    observed_at = str(
        payload.get("occurred_at")
        or payload.get("observed_at")
        or payload.get("published_at")
        or payload.get("created_at")
        or _now()
    ).strip()
    product_key = str(
        payload.get("product_key")
        or payload.get("product_series")
        or payload.get("sku")
        or ""
    ).strip()
    product_label = str(
        payload.get("product_label")
        or payload.get("product_name")
        or product_key
        or ""
    ).strip()
    dedupe_key = str(
        payload.get("dedupe_key")
        or f"{source}:{event_type}:{entity_type}:{external_id or creator_handle or observed_at}"
    ).strip()

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    return {
        "source_platform": source,
        "event_type": event_type,
        "entity_type": entity_type,
        "external_id": external_id,
        "source_url": str(payload.get("source_url") or payload.get("url") or external_id or "").strip(),
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
