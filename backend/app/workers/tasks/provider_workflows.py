"""Durable Redis handlers for paid-provider workflows.

User routes and schedulers enqueue these jobs instead of calling Apify inside
the web or scheduler process.  ``worker_main`` installs the durable execution
fence before dispatching any handler in this module.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from app.services.jobs.results import persist_job_result


async def _run(
    queue: Any,
    raw_job: dict[str, Any],
    operation: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    *,
    summary: Callable[[dict[str, Any]], str] | None = None,
) -> None:
    task_id = str(raw_job.get("task_id") or "")
    job_type = str(raw_job.get("job_type") or "")
    payload = raw_job.get("payload") if isinstance(raw_job.get("payload"), dict) else {}
    await queue.set_status(task_id, "processing", job_type=job_type)
    result = await operation(payload)
    result_path = await asyncio.to_thread(persist_job_result, task_id, result)
    await queue.set_status(
        task_id,
        "done",
        job_type=job_type,
        result_path=result_path,
        result_json=json.dumps(
            {"status": result.get("status") or "done", "result_path": result_path},
            ensure_ascii=False,
        ),
        summary=(summary(result) if summary else str(result.get("message") or "completed"))[:300],
    )


async def process_intel_lens_monitor_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.intelligence.lens_monitor import monitor_lens_market

        return await monitor_lens_market(
            str(payload.get("query") or ""),
            max_videos=max(1, min(200, int(payload.get("max_videos") or 30))),
            platform=str(payload.get("platform") or "youtube"),
            market=str(payload.get("market") or ""),
            date_from=str(payload.get("date_from") or ""),
            date_to=str(payload.get("date_to") or ""),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"videos={int((r.get('overview') or {}).get('total_videos') or 0)}")


async def process_intel_lens_compare_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.intelligence.lens_compare import compare_two_lenses

        return await compare_two_lenses(
            str(payload.get("lens_a") or ""),
            str(payload.get("lens_b") or ""),
            max_videos=max(1, min(100, int(payload.get("max_videos") or 15))),
            platform=str(payload.get("platform") or "youtube"),
            market=str(payload.get("market") or ""),
            date_from=str(payload.get("date_from") or ""),
            date_to=str(payload.get("date_to") or ""),
        )

    await _run(queue, raw_job, operation, summary=lambda r: "lens comparison completed")


async def process_intel_bh_refresh_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.intelligence import fetch_bh_viltrox_products, save_bh_snapshot
        from app.services.cache.memory_cache import cache_clear
        from app.services.intelligence.bh_repository import get_bh_summary

        products = await fetch_bh_viltrox_products(
            max_items=max(1, min(1000, int(payload.get("max_items") or 100))),
            force_refresh=True,
        )
        saved = await save_bh_snapshot(products) if products else 0
        cache_clear(prefix="intel:")
        return {
            "status": "done" if products else "no_results",
            "fetched": len(products),
            "saved": int(saved or 0),
            "summary": get_bh_summary(),
        }

    await _run(queue, raw_job, operation, summary=lambda r: f"fetched={r.get('fetched', 0)} saved={r.get('saved', 0)}")


async def process_intel_bh_reviews_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.intelligence.bh_scraper import fetch_bh_reviews

        return await fetch_bh_reviews(
            product_urls=payload.get("product_urls") if isinstance(payload.get("product_urls"), list) else None,
            limit_per_product=max(1, min(100, int(payload.get("limit_per_product") or 30))),
            max_products=max(1, min(20, int(payload.get("max_products") or 1))),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"reviews={r.get('reviews_fetched', 0)}")


async def process_intel_via_learning_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(_payload: dict[str, Any]) -> dict[str, Any]:
        from app.services.memory import run_via_daily_learning

        return await run_via_daily_learning()

    await _run(queue, raw_job, operation, summary=lambda r: f"accounts={len(r.get('official_accounts') or [])}")


async def process_discovery_federated_search_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.discovery import federation

        return await asyncio.to_thread(
            federation.federated_search,
            str(payload.get("query") or ""),
            limit=max(1, min(100, int(payload.get("limit") or 20))),
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else None,
            include_external=True,
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"results={len(r.get('results') or [])}")


async def process_vkpi_analytics_monitor_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains import analytics

        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else None
        return await analytics.monitor_product(body, staff=staff)

    await _run(queue, raw_job, operation, summary=lambda r: f"run_id={r.get('run_id', 0)}")


async def process_vkpi_analytics_compare_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains import analytics

        body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
        staff = payload.get("staff") if isinstance(payload.get("staff"), dict) else None
        return await analytics.compare_products(body, staff=staff)

    await _run(queue, raw_job, operation, summary=lambda r: f"run_id={r.get('run_id', 0)}")


async def process_apify_batch_refresh_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.sync.apify_batch_refresh import execute_apify_batch_plan

        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        return await execute_apify_batch_plan(
            plan,
            allow_provider_calls=True,
            timeout_secs=max(30, min(1800, int(payload.get("timeout_secs") or 300))),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"matched={r.get('matched_items', 0)} failed={r.get('failed_batches', 0)}")


async def process_kol_dossier_scan_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.kol import account as account_domain

        return await account_domain.scan_account_for_request(
            int(payload.get("kol_id") or 0),
            max_posts=max(1, min(500, int(payload.get("max_posts") or 50))),
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"posts={r.get('content_count', 0)}")


async def process_kol_platform_search_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.api.routers.kol_ops import _execute_platform_search

        return await _execute_platform_search(
            payload.get("body") if isinstance(payload.get("body"), dict) else {},
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"candidates={len(r.get('candidate_ids') or [])}")


async def process_kol_apify_enrich_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.discovery import apify_enrich

        return await asyncio.to_thread(
            apify_enrich.enrich_kol,
            int(payload.get("kol_pool_id") or 0),
            force=bool(payload.get("force", True)),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"enrichment={r.get('status', 'done')}")


async def process_kol_apify_enrich_candidates_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.discovery import apify_enrich

        return await asyncio.to_thread(
            apify_enrich.enrich_candidates,
            max(1, min(30, int(payload.get("limit") or 10))),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"enriched={r.get('enriched', 0)}")


async def process_kol_onboarding_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.kol import onboarding_workflow

        return await asyncio.to_thread(
            onboarding_workflow.start_kol_onboarding,
            str(payload.get("query") or ""),
            payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
            limit=max(1, min(100, int(payload.get("limit") or 20))),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"status={r.get('status', 'done')}")


async def process_official_visual_scan_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.channels import official_visual_analysis

        return await asyncio.to_thread(
            official_visual_analysis.process_pending_official_visuals,
            max_total=max(1, min(30, int(payload.get("max_total") or 5))),
        )

    await _run(queue, raw_job, operation, summary=lambda r: f"processed={r.get('processed', 0)}")


async def process_industry_account_refresh_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.industry import snapshot_collector

        account_id = int(payload.get("account_id") or 0)
        if account_id <= 0:
            raise ValueError("account_id required")
        return await asyncio.to_thread(
            snapshot_collector.collect_account_snapshot,
            account_id,
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
        )

    await _run(
        queue,
        raw_job,
        operation,
        summary=lambda r: f"account={int(((r.get('account') or {}).get('id')) or 0)} status={r.get('sync_status', 'unknown')}",
    )


async def process_project_video_metadata_refresh_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.projects import workflow_evidence

        evidence_id = int(payload.get("evidence_id") or 0)
        if evidence_id <= 0:
            raise ValueError("evidence_id required")
        return await asyncio.to_thread(
            workflow_evidence.refresh_project_video_evidence_metadata,
            evidence_id,
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
        )

    await _run(
        queue,
        raw_job,
        operation,
        summary=lambda r: f"evidence={r.get('evidence_id', 0)} status={r.get('status', 'unknown')}",
    )


async def process_comments_collect_post_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.comments import collector

        return await asyncio.to_thread(
            collector.collect_post_comments,
            int(payload.get("post_id") or 0),
            post_table=str(payload.get("post_table") or "industry_posts"),
            max_comments=payload.get("max_comments"),
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
            triggered_by=str(payload.get("triggered_by") or "durable_worker"),
        )

    await _run(
        queue,
        raw_job,
        operation,
        summary=lambda r: f"post={r.get('post_id', 0)} status={r.get('status', 'unknown')}",
    )


async def process_comments_batch_collect_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.comments import collector

        return await asyncio.to_thread(
            collector.batch_collect_pending,
            platform=str(payload.get("platform") or ""),
            days=max(1, min(30, int(payload.get("days") or 7))),
            limit=max(1, min(500, int(payload.get("limit") or 100))),
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
        )

    await _run(
        queue,
        raw_job,
        operation,
        summary=lambda r: f"processed={r.get('processed', r.get('total', 0))}",
    )


async def process_comment_intelligence_post_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.comments import intelligence

        return await asyncio.to_thread(
            intelligence.process_post,
            int(payload.get("post_id") or 0),
            post_table=str(payload.get("post_table") or "industry_posts"),
            max_comments=payload.get("max_comments"),
            collect_comments=bool(payload.get("collect_comments", True)),
            analyze_sentiment=bool(payload.get("analyze_sentiment", True)),
            classify_pillar=bool(payload.get("classify_pillar", True)),
            force_reprocess=bool(payload.get("force_reprocess", False)),
            comment_limit=max(1, min(1000, int(payload.get("comment_limit") or 100))),
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
            triggered_by=str(payload.get("triggered_by") or "durable_worker"),
            retry_of_run_id=int(payload.get("retry_of_run_id") or 0) or None,
        )

    await _run(
        queue,
        raw_job,
        operation,
        summary=lambda r: f"run={r.get('run_id', 0)} status={r.get('status', 'unknown')}",
    )


async def process_comment_intelligence_recent_job(queue: Any, raw_job: dict[str, Any]) -> None:
    async def operation(payload: dict[str, Any]) -> dict[str, Any]:
        from app.domains.comments import intelligence

        return await asyncio.to_thread(
            intelligence.process_recent_posts,
            platform=str(payload.get("platform") or ""),
            days=max(1, min(90, int(payload.get("days") or 7))),
            limit=max(1, min(250, int(payload.get("limit") or 25))),
            collect_comments=bool(payload.get("collect_comments", False)),
            analyze_sentiment=bool(payload.get("analyze_sentiment", True)),
            classify_pillar=bool(payload.get("classify_pillar", True)),
            force_reprocess=bool(payload.get("force_reprocess", False)),
            staff=payload.get("staff") if isinstance(payload.get("staff"), dict) else {},
        )

    await _run(
        queue,
        raw_job,
        operation,
        summary=lambda r: f"posts={r.get('total_posts', 0)} status={r.get('status', 'unknown')}",
    )
