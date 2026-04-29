"""
workers/tasks/ingestion.py — platform ingest worker skeletons
"""
from __future__ import annotations

import asyncio

from app.services.ingestion.research import collect_learning_payload
from app.services.ingestion.pipeline import normalize_ingest_payload, summarize_ingest_payload
from app.services.jobs.results import persist_job_result
from app.services.memory.l3_store import (
    record_creator_memory_fact,
    record_feedback_signal,
    record_ingest_event,
    record_market_observation,
    record_product_signal,
    record_region_fact,
    update_ingest_status,
)


async def process_platform_ingest_job(queue, raw_job: dict, source_platform: str) -> None:
    task_id = raw_job["task_id"]
    payload = raw_job.get("payload", {})
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip()
    if source_url and (payload.get("autonomous_fetch") or source_platform in {"reddit", "web"}):
        harvested = await collect_learning_payload(source_url, preferred_source=source_platform)
        merged_payload = dict(payload)
        for key, value in harvested.items():
            if value not in (None, "", [], {}):
                merged_payload[key] = value
        payload = merged_payload
    normalized = normalize_ingest_payload(source_platform, payload)
    source = normalized["source_platform"]

    await queue.set_status(task_id, "processing", job_type=raw_job.get("job_type", ""))
    event_id = await asyncio.to_thread(
        record_ingest_event,
        source_platform=source,
        event_type=normalized["event_type"],
        entity_type=normalized["entity_type"],
        external_id=normalized["external_id"],
        creator_handle=normalized["creator_handle"],
        region_code=normalized["region_code"],
        dedupe_key=normalized["dedupe_key"],
        payload=normalized["payload"],
        ingest_status="processing",
        occurred_at=normalized["observed_at"],
    )

    try:
        l3_refs: dict[str, int | str] = {"ingest_event_id": event_id}
        summary = normalized["summary"] or summarize_ingest_payload(normalized)

        subject_key = normalized["external_id"] or normalized["creator_handle"] or normalized["product_key"] or normalized["dedupe_key"]
        market_id = await asyncio.to_thread(
            record_market_observation,
            source_platform=source,
            subject_type=normalized["entity_type"],
            subject_key=subject_key,
            observation_type=normalized["event_type"],
            summary=summary,
            metrics=normalized["metrics"],
            evidence={"dedupe_key": normalized["dedupe_key"]},
            region_code=normalized["region_code"],
            observed_at=normalized["observed_at"],
        )
        l3_refs["market_observation_id"] = market_id

        if normalized["creator_handle"] or normalized["user_id"]:
            creator_memory_id = await asyncio.to_thread(
                record_creator_memory_fact,
                user_id=normalized["user_id"],
                creator_handle=normalized["creator_handle"],
                memory_kind="platform_ingest",
                fact_key=f"{source}:{normalized['event_type']}",
                fact_value={
                    "entity_type": normalized["entity_type"],
                    "external_id": normalized["external_id"],
                    "metrics": normalized["metrics"],
                    "region_code": normalized["region_code"],
                    "observed_at": normalized["observed_at"],
                },
                confidence=0.72,
                source_ref=f"platform_ingest_events:{event_id}",
            )
            l3_refs["creator_memory_id"] = creator_memory_id

        if normalized["product_key"]:
            product_key = await asyncio.to_thread(
                record_product_signal,
                product_key=normalized["product_key"],
                label=normalized["product_label"] or normalized["product_key"],
                family=normalized["product_family"],
                mount_type=normalized["mount_type"],
                alias_terms=normalized["alias_terms"],
                feature_tags=normalized["feature_tags"],
                scene_tags=normalized["scene_tags"],
                feature_type="ingest_seed",
                feature_vector=normalized["metrics"],
                detector_version="ingest-skeleton-v1",
                asset_role="platform_event",
                storage_key=normalized["external_id"],
            )
            l3_refs["product_key"] = product_key

        if normalized["region_code"]:
            region_fact_id = await asyncio.to_thread(
                record_region_fact,
                region_code=normalized["region_code"],
                fact_type=f"{source}:{normalized['event_type']}",
                fact_value={
                    "entity_type": normalized["entity_type"],
                    "external_id": normalized["external_id"],
                    "metrics": normalized["metrics"],
                    "creator_handle": normalized["creator_handle"],
                },
                source_platform=source,
                observed_at=normalized["observed_at"],
            )
            l3_refs["region_fact_id"] = region_fact_id

        feedback_id = await asyncio.to_thread(
            record_feedback_signal,
            source_type=source,
            source_id=normalized["external_id"],
            event_type=normalized["event_type"],
            actor_role="platform",
            user_id=normalized["user_id"],
            submission_id=normalized["submission_id"],
            payload=normalized["payload"],
        )
        l3_refs["feedback_event_id"] = feedback_id

        if source == "shopify" and normalized["entity_type"] == "order":
            from app.services.commerce.orders import ingest_order_from_event

            order_id = await ingest_order_from_event(int(event_id))
            if order_id:
                l3_refs["order_id"] = order_id

        result = {
            "event_id": event_id,
            "normalized": normalized,
            "l3_refs": l3_refs,
        }
        result_path = persist_job_result(task_id, result)
        await asyncio.to_thread(update_ingest_status, event_id, "done", "")
        await queue.set_status(
            task_id,
            "done",
            job_type=raw_job.get("job_type", ""),
            event_id=event_id,
            result_path=result_path,
            summary=summary,
        )
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
        await asyncio.to_thread(update_ingest_status, event_id, "failed", error_message)
        await queue.set_status(
            task_id,
            "failed",
            job_type=raw_job.get("job_type", ""),
            event_id=event_id,
            error_message=error_message,
        )


async def process_facebook_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "facebook")


async def process_tiktok_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "tiktok")


async def process_instagram_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "instagram")


async def process_youtube_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "youtube")


async def process_shopify_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "shopify")


async def process_amazon_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "amazon")


async def process_bh_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "bh")


async def process_reddit_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "reddit")


async def process_web_ingest_job(queue, raw_job: dict) -> None:
    await process_platform_ingest_job(queue, raw_job, "web")
