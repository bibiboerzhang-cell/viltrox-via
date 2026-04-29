"""
services/memory/l3_store.py — Level 3 structured store helpers
"""
from __future__ import annotations

from typing import Any, Iterable

from app.db.repositories.knowledge import (
    append_product_visual_feature,
    insert_feedback_event,
    record_platform_ingest_event,
    update_platform_ingest_event_status,
    upsert_creator_memory_entry,
    upsert_market_observation,
    upsert_product_knowledge,
    upsert_region_market_fact,
)


def record_ingest_event(**kwargs: Any) -> int:
    return record_platform_ingest_event(**kwargs)


def update_ingest_status(event_id: int, ingest_status: str, error_message: str = "") -> None:
    update_platform_ingest_event_status(event_id, ingest_status, error_message)


def record_creator_memory_fact(
    *,
    user_id: int = 0,
    creator_handle: str = "",
    memory_kind: str,
    fact_key: str,
    fact_value: Any,
    confidence: float = 0.5,
    source_ref: str = "",
) -> int:
    return upsert_creator_memory_entry(
        user_id=user_id,
        creator_handle=creator_handle,
        memory_kind=memory_kind,
        fact_key=fact_key,
        fact_value=fact_value,
        confidence=confidence,
        source_ref=source_ref,
    )


def record_market_observation(
    *,
    source_platform: str,
    subject_type: str,
    subject_key: str,
    observation_type: str,
    summary: str = "",
    metrics: Any = None,
    evidence: Any = None,
    region_code: str = "",
    observed_at: str = "",
) -> int:
    return upsert_market_observation(
        source_platform=source_platform,
        subject_type=subject_type,
        subject_key=subject_key,
        observation_type=observation_type,
        summary=summary,
        metrics=metrics,
        evidence=evidence,
        region_code=region_code,
        observed_at=observed_at,
    )


def record_product_signal(
    *,
    product_key: str,
    label: str,
    family: str = "",
    mount_type: str = "",
    alias_terms: Iterable[str] | None = None,
    feature_tags: Iterable[str] | None = None,
    scene_tags: Iterable[str] | None = None,
    feature_type: str = "",
    feature_vector: Any = None,
    asset_role: str = "",
    storage_key: str = "",
    detector_version: str = "",
) -> str:
    key = upsert_product_knowledge(
        product_key=product_key,
        label=label,
        family=family,
        mount_type=mount_type,
        alias_terms=alias_terms or [],
        feature_tags=feature_tags or [],
        scene_tags=scene_tags or [],
    )
    if feature_type:
        append_product_visual_feature(
            product_key=product_key,
            asset_role=asset_role,
            storage_key=storage_key,
            feature_type=feature_type,
            feature_vector=feature_vector or {},
            detector_version=detector_version,
        )
    return key


def record_region_fact(
    *,
    region_code: str,
    fact_type: str,
    fact_value: Any,
    source_platform: str = "",
    region_level: str = "country",
    observed_at: str = "",
) -> int:
    return upsert_region_market_fact(
        region_code=region_code,
        fact_type=fact_type,
        fact_value=fact_value,
        source_platform=source_platform,
        region_level=region_level,
        observed_at=observed_at,
    )


def record_feedback_signal(
    *,
    source_type: str,
    event_type: str,
    payload: Any = None,
    source_id: str = "",
    actor_role: str = "",
    user_id: int = 0,
    submission_id: int = 0,
) -> int:
    return insert_feedback_event(
        source_type=source_type,
        source_id=source_id,
        event_type=event_type,
        actor_role=actor_role,
        user_id=user_id,
        submission_id=submission_id,
        payload=payload,
    )
