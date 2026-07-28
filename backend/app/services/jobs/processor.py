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
from app.workers.tasks.market import process_market_trends_refresh_job
from app.workers.tasks.provider_workflows import (
    process_apify_batch_refresh_job,
    process_comment_intelligence_post_job,
    process_comment_intelligence_recent_job,
    process_comments_batch_collect_job,
    process_comments_collect_post_job,
    process_discovery_federated_search_job,
    process_industry_account_refresh_job,
    process_intel_bh_refresh_job,
    process_intel_bh_reviews_job,
    process_intel_lens_compare_job,
    process_intel_lens_monitor_job,
    process_intel_via_learning_job,
    process_kol_apify_enrich_candidates_job,
    process_kol_apify_enrich_job,
    process_kol_dossier_scan_job,
    process_kol_onboarding_job,
    process_kol_platform_search_job,
    process_official_visual_scan_job,
    process_project_video_metadata_refresh_job,
    process_vkpi_analytics_compare_job,
    process_vkpi_analytics_monitor_job,
)
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
    "comment_intelligence_post": process_comment_intelligence_post_job,
    "comment_intelligence_recent": process_comment_intelligence_recent_job,
    "comments_batch_collect": process_comments_batch_collect_job,
    "comments_collect_post": process_comments_collect_post_job,
    "intel_scan_account": process_scan_account_job,
    "intel_scan_matrix": process_scan_matrix_job,
    "intel_lens_monitor": process_intel_lens_monitor_job,
    "intel_lens_compare": process_intel_lens_compare_job,
    "intel_bh_refresh": process_intel_bh_refresh_job,
    "intel_bh_reviews": process_intel_bh_reviews_job,
    "intel_via_learning": process_intel_via_learning_job,
    "discovery_federated_search": process_discovery_federated_search_job,
    "industry_account_refresh": process_industry_account_refresh_job,
    "vkpi_analytics_monitor": process_vkpi_analytics_monitor_job,
    "vkpi_analytics_compare": process_vkpi_analytics_compare_job,
    "apify_batch_refresh": process_apify_batch_refresh_job,
    "kol_dossier_scan": process_kol_dossier_scan_job,
    "kol_platform_search": process_kol_platform_search_job,
    "kol_apify_enrich": process_kol_apify_enrich_job,
    "kol_apify_enrich_candidates": process_kol_apify_enrich_candidates_job,
    "kol_onboarding": process_kol_onboarding_job,
    "market_trends_refresh": process_market_trends_refresh_job,
    "official_visual_scan": process_official_visual_scan_job,
    "project_video_metadata_refresh": process_project_video_metadata_refresh_job,
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
