"""
services/jobs/processor.py — 后台任务统一分发器
"""
from __future__ import annotations

from app.workers.tasks.audit import process_audit_submission_job
from app.workers.tasks.ingestion import (
    process_amazon_ingest_job,
    process_bh_ingest_job,
    process_facebook_ingest_job,
    process_instagram_ingest_job,
    process_reddit_ingest_job,
    process_shopify_ingest_job,
    process_tiktok_ingest_job,
    process_web_ingest_job,
    process_youtube_ingest_job,
)
from app.workers.tasks.intelligence import process_scan_account_job, process_scan_matrix_job
from app.workers.tasks.kol_score import process_score_kol_content_job
from app.workers.tasks.vkpi import (
    process_vkpi_kol_pool_on_demand_refresh_job,
    process_vkpi_official_channel_sync_job,
    process_vkpi_video_cache_job,
)
from app.workers.tasks.verification import (
    process_verification_comment_job,
    process_verification_single_scan_job,
    process_verification_scan_job,
)


JOB_HANDLERS = {
    "audit_submission": process_audit_submission_job,
    "intel_scan_account": process_scan_account_job,
    "intel_scan_matrix": process_scan_matrix_job,
    "score_kol_content": process_score_kol_content_job,
    "verification_scan_pending": process_verification_scan_job,
    "verification_scan_single": process_verification_single_scan_job,
    "verification_prepare_comment": process_verification_comment_job,
    "platform_ingest_facebook": process_facebook_ingest_job,
    "platform_ingest_tiktok": process_tiktok_ingest_job,
    "platform_ingest_instagram": process_instagram_ingest_job,
    "platform_ingest_youtube": process_youtube_ingest_job,
    "platform_ingest_shopify": process_shopify_ingest_job,
    "platform_ingest_amazon": process_amazon_ingest_job,
    "platform_ingest_bh": process_bh_ingest_job,
    "platform_ingest_reddit": process_reddit_ingest_job,
    "platform_ingest_web": process_web_ingest_job,
    "vkpi_official_channel_sync": process_vkpi_official_channel_sync_job,
    "vkpi_video_cache": process_vkpi_video_cache_job,
    "vkpi_kol_pool_on_demand_refresh": process_vkpi_kol_pool_on_demand_refresh_job,
}


async def process_background_job(queue, raw_job: dict) -> None:
    job_type = raw_job.get("job_type")
    handler = JOB_HANDLERS.get(job_type)
    if handler is None:
        await queue.set_status(
            raw_job.get("task_id", ""),
            "failed",
            job_type=job_type or "",
            error_message=f"Unsupported job_type: {job_type}",
        )
        return
    await handler(queue, raw_job)
