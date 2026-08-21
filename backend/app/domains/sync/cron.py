"""Run-now wrappers for V-KPI scheduled jobs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains import audit
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)


_JOB_ALIASES: dict[str, str] = {
    "lineage": "lineage_snapshot",
    "lineage_snapshot": "lineage_snapshot",
    "kpi": "kpi_rollup",
    "kpi_rollup": "kpi_rollup",
    "rollup": "kpi_rollup",
    "alerts": "alerts",
    "alert": "alerts",
    "weekly_report": "weekly_report",
    "report": "weekly_report",
    "analytics_monitor": "analytics_monitor",
    "product_monitor": "analytics_monitor",
    "channels_sync": "channels_sync",
    "channel_sync": "channels_sync",
    "official_full_baseline": "official_full_baseline",
    "full_baseline": "official_full_baseline",
    "daily_incremental_sync": "daily_incremental_sync",
    "vkpi_daily_incremental": "daily_incremental_sync",
    "official_kol_daily": "daily_incremental_sync",
    "daily_outreach_digest_only": "daily_outreach_digest_only",
    "outreach_digest_only": "daily_outreach_digest_only",
    "morning_sync": "morning_sync",
    "daily_morning_sync": "morning_sync",
    "daily_outreach_digest": "morning_sync",
}

_MANUAL_JOB_POLICIES: dict[str, dict[str, Any]] = {
    "alerts": {
        "risk": "medium",
        "description": "Generate alert rows from existing projects/comments.",
    },
    "lineage_snapshot": {
        "risk": "medium",
        "description": "Generate metric lineage snapshots.",
    },
    "kpi_rollup": {
        "risk": "medium",
        "description": "Generate KPI daily rollup rows.",
    },
    "weekly_report": {
        "risk": "high",
        "description": "Generate weekly report context and summary.",
    },
    "analytics_monitor": {
        "risk": "high",
        "description": "Run product monitoring jobs that can call platform providers.",
    },
    "channels_sync": {
        "risk": "high",
        "description": "Sync all bound channels with the configured recent-content limit.",
    },
    "official_full_baseline": {
        "risk": "high",
        "description": "Run the first full official-account baseline with higher per-platform limits.",
    },
    "daily_incremental_sync": {
        "risk": "high",
        "description": "Run 18 official-account recent refresh; legacy KOL Pool refresh is skipped unless explicitly allowed.",
    },
    "daily_outreach_digest_only": {
        "risk": "medium",
        "description": "Generate Daily Top100 outreach digest without full morning sync.",
    },
    "morning_sync": {
        "risk": "high",
        "description": "Run channel sync, industry account sync, product monitor, and Daily Top100 digest.",
    },
}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_job_name(job_name: str) -> str:
    """Return canonical cron job name or raise for unsupported names."""
    raw = str(job_name or "").strip().lower().replace("-", "_")
    canonical = _JOB_ALIASES.get(raw)
    if not canonical:
        raise ValueError("unsupported V-KPI cron job")
    return canonical


def manual_job_names() -> list[str]:
    """Cron jobs allowed through admin manual-trigger endpoints."""
    return sorted(_MANUAL_JOB_POLICIES)


def manual_job_policy(job_name: str) -> dict[str, Any]:
    canonical = normalize_job_name(job_name)
    policy = dict(_MANUAL_JOB_POLICIES.get(canonical) or {})
    if not policy:
        raise ValueError("unsupported V-KPI cron job")
    policy["job"] = canonical
    policy["confirm_text"] = f"RUN {canonical}"
    return policy


def manual_job_catalog() -> dict[str, Any]:
    return {"jobs": [manual_job_policy(name) for name in manual_job_names()]}


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "date",
        "ledger_date",
        "period_days",
        "scope_type",
        "product_sku",
        "limit",
        "max_videos",
        "max_posts",
        "channel_max_posts",
        "dry_run",
        "kol_limit",
        "kol_max_posts",
        "kol_error_stop_threshold",
        "kol_platforms",
        "kol_refresh_selector",
        "kol_source_type",
        "kol_tiers",
        "platforms",
        "official_max_posts",
        "official_platforms",
        "skip_kol",
        "allow_legacy_kol_full_refresh",
        "allow_qualified_kol_refresh",
        "skip_official",
        "industry_account_limit",
        "validate_only",
    }
    return {key: payload.get(key) for key in sorted(allowed) if key in payload}


def _result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    summary: dict[str, Any] = {}
    for key in (
        "job",
        "status",
        "ran_at",
        "runs",
        "synced",
        "failed",
        "channels_synced",
        "channels_enqueued",
        "industry_accounts_synced",
        "industry_accounts_skipped",
        "industry_accounts_failed",
        "monitor_runs",
        "count",
    ):
        if key in result:
            summary[key] = result.get(key)
    if isinstance(result.get("digest"), dict):
        digest = result["digest"]
        summary["digest"] = {
            "assigned": digest.get("assigned"),
            "generated": digest.get("generated"),
            "staff_count": digest.get("staff_count"),
        }
    return summary


def _system_staff() -> dict[str, Any]:
    return {
        "id": 0,
        "staff_id": 0,
        "user_id": 0,
        "role": "admin",
        "is_owner": 1,
        "email": "",
    }


async def _queue_channel_syncs(
    rows: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    queue: Any | None,
) -> dict[str, Any]:
    owned_queue = None
    effective_queue = queue
    if effective_queue is None:
        from app.services.jobs.queue import build_job_queue

        owned_queue = build_job_queue()
        effective_queue = owned_queue
    if effective_queue is None:
        raise RuntimeError("job queue unavailable")
    if str(getattr(effective_queue, "backend_name", "")) == "inprocess":
        raise RuntimeError("durable_queue_required:inprocess_queue_has_no_provider_execution_fence")

    import app.domains.tasks.enqueue as task_enqueue

    enqueue_staff = staff or _system_staff()
    max_posts = int(payload.get("max_posts") or payload.get("limit") or 12)
    task_ids: list[str] = []
    failed: list[dict[str, Any]] = []
    try:
        for row in rows:
            channel_id = int(row.get("id") or 0)
            if not channel_id:
                continue
            try:
                queued = await task_enqueue.enqueue_official_channel_sync(
                    effective_queue,
                    channel_id,
                    max_posts=int(row.get("_requested_max_posts") or max_posts),
                    staff=enqueue_staff,
                    priority=5,
                )
                task_ids.append(str(queued.get("task_id") or ""))
            except Exception as exc:  # Continue enqueueing other channels.
                failed.append({"channel_id": channel_id, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
    finally:
        if owned_queue is not None:
            close_fn = getattr(owned_queue, "close", None)
            if close_fn is not None:
                result = close_fn()
                if hasattr(result, "__await__"):
                    await result
    unique_task_ids = [item for item in dict.fromkeys(task_ids) if item]
    return {
        "channels_enqueued": len(unique_task_ids),
        "channels_requested": len(rows),
        "channels_failed_to_enqueue": len(failed),
        "task_ids": unique_task_ids[:20],
        "failed": failed[:20],
    }


async def _queue_provider_jobs(
    jobs: list[dict[str, Any]],
    *,
    queue: Any | None,
) -> dict[str, Any]:
    """Enqueue reviewed provider jobs and never execute them in cron."""

    owned_queue = None
    effective_queue = queue
    if effective_queue is None:
        from app.services.jobs.queue import build_job_queue

        owned_queue = build_job_queue()
        effective_queue = owned_queue
    if effective_queue is None:
        raise RuntimeError("durable job queue unavailable")
    if str(getattr(effective_queue, "backend_name", "")) == "inprocess":
        raise RuntimeError("durable_queue_required:inprocess_queue_has_no_provider_execution_fence")
    task_ids: list[str] = []
    failed: list[dict[str, Any]] = []
    try:
        for spec in jobs:
            try:
                task_id = await effective_queue.enqueue(
                    str(spec.get("job_type") or ""),
                    dict(spec.get("payload") or {}),
                    lock_key=str(spec.get("lock_key") or "") or None,
                    timeout_seconds=int(spec.get("timeout_seconds") or 1200),
                )
                task_ids.append(str(task_id))
            except Exception as exc:
                failed.append(
                    {
                        "job_type": spec.get("job_type"),
                        "target_id": (spec.get("payload") or {}).get("account_id")
                        or (spec.get("payload") or {}).get("body", {}).get("product_sku"),
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
    finally:
        if owned_queue is not None:
            close_fn = getattr(owned_queue, "close", None)
            if close_fn is not None:
                result = close_fn()
                if hasattr(result, "__await__"):
                    await result
    return {
        "requested": len(jobs),
        "enqueued": len(task_ids),
        "failed_to_enqueue": len(failed),
        "task_ids": task_ids[:50],
        "failed": failed[:20],
    }


async def _queue_kol_refreshes(
    rows: list[dict[str, Any]],
    *,
    payload: dict[str, Any],
    staff: dict[str, Any] | None,
    queue: Any | None,
) -> dict[str, Any]:
    owned_queue = None
    effective_queue = queue
    if effective_queue is None:
        from app.services.jobs.queue import build_job_queue

        owned_queue = build_job_queue()
        effective_queue = owned_queue
    if effective_queue is None:
        raise RuntimeError("durable job queue unavailable")
    if str(getattr(effective_queue, "backend_name", "")) == "inprocess":
        raise RuntimeError("durable_queue_required:inprocess_queue_has_no_provider_execution_fence")
    import app.domains.tasks.enqueue as task_enqueue

    task_ids: list[str] = []
    failed: list[dict[str, Any]] = []
    max_posts = max(1, min(3, int(payload.get("kol_max_posts") or payload.get("max_posts") or 1)))
    try:
        for row in rows:
            kol_pool_id = int(row.get("id") or 0)
            if not kol_pool_id:
                continue
            try:
                queued = await task_enqueue.enqueue_kol_pool_on_demand_refresh(
                    effective_queue,
                    kol_pool_id,
                    reason="daily_incremental_sync",
                    max_posts=max_posts,
                    staff=staff or _system_staff(),
                    priority=5,
                )
                task_ids.append(str(queued.get("task_id") or ""))
            except Exception as exc:
                failed.append(
                    {
                        "kol_pool_id": kol_pool_id,
                        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    }
                )
    finally:
        if owned_queue is not None:
            close_fn = getattr(owned_queue, "close", None)
            if close_fn is not None:
                result = close_fn()
                if hasattr(result, "__await__"):
                    await result
    return {
        "requested": len(rows),
        "enqueued": len([item for item in task_ids if item]),
        "failed_to_enqueue": len(failed),
        "task_ids": [item for item in task_ids if item][:50],
        "failed": failed[:20],
    }


def _log_cron_audit(
    *,
    staff: dict[str, Any] | None,
    action_type: str,
    job_name: str,
    detail: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    staff_id = resolve_staff_id(staff)
    if not staff_id:
        return
    try:
        audit.log_business_event(
            staff_id=int(staff_id),
            action_type=action_type,
            target_type="cron_job",
            target_id=job_name,
            detail=detail,
            metadata=metadata or {},
        )
    except Exception as exc:  # pragma: no cover - audit must not break cron.
        logger.warning("cron business audit failed for %s/%s: %s", action_type, job_name, exc)


async def run_manual_job(job_name: str, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None, queue: Any | None = None) -> dict[str, Any]:
    """Run a cron job from an admin endpoint with allow-list, confirm text, and audit."""
    payload = dict(payload or {})
    staff = staff or payload.get("staff") or {}
    canonical = normalize_job_name(job_name)
    policy = manual_job_policy(canonical)
    confirm = str(payload.get("confirm") or payload.get("confirm_text") or "").strip()
    required_confirm = str(policy["confirm_text"])
    if confirm != required_confirm:
        raise ValueError(f"confirmation required: {required_confirm}")

    validate_only = bool(payload.get("validate_only"))
    audit_metadata = {
        "policy": policy,
        "payload": _payload_summary(payload),
        "validate_only": validate_only,
    }
    _log_cron_audit(
        staff=staff,
        action_type="cron_run_requested",
        job_name=canonical,
        detail=f"manual cron requested: {canonical}",
        metadata=audit_metadata,
    )
    try:
        if validate_only:
            result = {
                "job": canonical,
                "status": "validated",
                "ran": False,
                "policy": policy,
                "ran_at": _stamp(),
            }
        else:
            payload["staff"] = staff
            result = await run_job(canonical, payload, queue=queue)
        _log_cron_audit(
            staff=staff,
            action_type="cron_run_completed",
            job_name=canonical,
            detail=f"manual cron completed: {canonical}",
            metadata={**audit_metadata, "result": _result_summary(result)},
        )
        return result
    except Exception as exc:
        _log_cron_audit(
            staff=staff,
            action_type="cron_run_failed",
            job_name=canonical,
            detail=f"manual cron failed: {canonical}",
            metadata={**audit_metadata, "error": str(exc)[:500]},
        )
        raise


async def run_job(job_name: str, payload: dict[str, Any] | None = None, *, queue: Any | None = None) -> dict[str, Any]:
    payload = payload or {}
    name = normalize_job_name(job_name)
    if name == "lineage_snapshot":
        from app.domains import lineage as metric_lineage

        result = await asyncio.to_thread(metric_lineage.generate_run, period_days=int(payload.get("period_days") or 7), scope_type=str(payload.get("scope_type") or "all"), trigger_source="scheduler_lineage_snapshot", metadata={"source": "cron.run_now"})
        return {"job": name, "status": "ok", "result": result, "ran_at": _stamp()}
    if name == "kpi_rollup":
        from app.domains.staff import kpi_ledger

        result = await asyncio.to_thread(kpi_ledger.generate_daily_rollup, payload.get("ledger_date"))
        return {"job": name, "status": "ok", "result": result, "ran_at": _stamp()}
    if name == "alerts":
        from app.domains import alerts

        result = await asyncio.to_thread(alerts.generate_alerts)
        return {"job": name, "status": "ok", "result": result, "ran_at": _stamp()}
    if name == "weekly_report":
        from app.domains import reports

        result = await asyncio.to_thread(reports.generate_weekly_report, period_days=int(payload.get("period_days") or 7), staff=payload.get("staff"), filters=payload)
        return {"job": name, "status": "ok", "result": {k: v for k, v in result.items() if k != "context"}, "ran_at": _stamp()}
    if name == "analytics_monitor":
        from app.domains import analytics

        products = analytics.list_monitored_products(limit=50).get("products") or []
        jobs: list[dict[str, Any]] = []
        for product in products:
            if str(product.get("enabled") or "1") in {"0", "false"}:
                continue
            platforms = product.get("monitor_platforms_json") or "[]"
            try:
                import json

                platform_list = json.loads(platforms) if isinstance(platforms, str) else platforms
            except Exception:
                platform_list = ["youtube"]
            for platform in (platform_list or ["youtube"]):
                body = {
                    "product_sku": product.get("product_sku"),
                    "platform": platform,
                    "max_videos": int(payload.get("max_videos") or 20),
                }
                jobs.append(
                    {
                        "job_type": "vkpi_analytics_monitor",
                        "payload": {"body": body, "staff": dict(payload.get("staff") or {})},
                        "lock_key": f"vkpi_analytics_monitor:{body['product_sku']}:{platform}",
                        "timeout_seconds": 1200,
                    }
                )
        queued = await _queue_provider_jobs(jobs, queue=queue)
        return {"job": name, "status": "queued", "runs": queued["enqueued"], **queued, "ran_at": _stamp()}
    if name == "channels_sync":
        from app.domains import channels

        rows = channels.list_channels(staff={}, limit=300).get("channels") or []
        max_posts = int(payload.get("channel_max_posts") or payload.get("max_posts") or 12)
        queued = await _queue_channel_syncs(
            rows,
            payload={**payload, "max_posts": max_posts},
            staff=payload.get("staff"),
            queue=queue,
        )
        return {"job": name, "status": "queued", **queued, "ran_at": _stamp()}
    if name == "official_full_baseline":
        from app.domains import channels

        platform_limits = {
            "youtube": 1000,
            "instagram": 1000,
            "tiktok": 300,
            "facebook": 250,
            "reddit": 150,
            "x": 200,
        }
        requested_platforms = payload.get("platforms")
        if isinstance(requested_platforms, str):
            platform_filter = {item.strip().lower() for item in requested_platforms.split(",") if item.strip()}
        elif isinstance(requested_platforms, list):
            platform_filter = {str(item).strip().lower() for item in requested_platforms if str(item).strip()}
        else:
            platform_filter = set(platform_limits)
        rows = [
            row for row in channels.list_channels(staff={}, limit=300).get("channels") or []
            if str(row.get("platform") or "").lower() in platform_filter
        ]
        max_override = int(payload.get("max_posts") or 0)
        queued_rows: list[dict[str, Any]] = []
        for row in rows:
            platform_key = str(row.get("platform") or "").lower()
            max_posts = max_override or platform_limits.get(platform_key, 100)
            queued_rows.append({**row, "_requested_max_posts": max_posts})
        queued = await _queue_channel_syncs(
            queued_rows,
            payload={**payload, "max_posts": max_override or 100},
            staff=payload.get("staff"),
            queue=queue,
        )
        return {
            "job": name,
            "status": "queued",
            **queued,
            "platforms": sorted(platform_filter),
            "limits": {key: platform_limits[key] for key in sorted(platform_limits) if key in platform_filter},
            "ran_at": _stamp(),
        }
    if name == "daily_incremental_sync":
        from app.domains.sync import daily_sync
        from app.domains.sync import refresh_tier

        daily_sync.check_daily_sync_guard(payload)
        official: dict[str, Any] = {"skipped": True}
        if not daily_sync._bool(payload.get("skip_official")):
            from app.domains import channels

            rows = channels.list_channels(staff={}, limit=300).get("channels") or []
            official = await _queue_channel_syncs(
                rows,
                payload={
                    **payload,
                    "max_posts": int(payload.get("official_max_posts") or payload.get("channel_max_posts") or payload.get("max_posts") or 50),
                },
                staff=payload.get("staff"),
                queue=queue,
            )

        kol_result: dict[str, Any] = {"skipped": True}
        selector = daily_sync._kol_refresh_selector(payload)
        allow_legacy = daily_sync._bool(payload.get("allow_legacy_kol_full_refresh")) or daily_sync._bool(payload.get("include_legacy_kol"))
        allow_qualified = daily_sync._bool(payload.get("allow_qualified_kol_refresh")) or daily_sync._bool(payload.get("include_qualified_kol"))
        if not daily_sync._bool(payload.get("skip_kol")) and ((selector == "qualified" and allow_qualified) or (selector != "qualified" and allow_legacy)):
            limit = max(1, min(1200, int(payload.get("kol_limit") or 1200)))
            platforms = daily_sync._platform_filter(payload.get("kol_platforms") or payload.get("platforms"))
            if selector == "qualified":
                rows = refresh_tier.qualified_refresh_rows(
                    limit=limit,
                    offset=max(0, int(payload.get("kol_offset") or 0)),
                    stale_before=str(payload.get("kol_stale_before") or ""),
                    platforms=platforms,
                    tiers=daily_sync._tier_filter(payload.get("kol_tiers") or payload.get("refresh_tiers")),
                )
            else:
                rows = daily_sync._kol_light_rows(
                    limit=limit,
                    offset=max(0, int(payload.get("kol_offset") or 0)),
                    stale_before=str(payload.get("kol_stale_before") or ""),
                    platforms=platforms,
                    source_type=str(payload.get("kol_source_type") or "legacy_excel_p2d"),
                )
            kol_result = await _queue_kol_refreshes(
                rows,
                payload=payload,
                staff=payload.get("staff"),
                queue=queue,
            )
        return {
            "job": name,
            "status": "queued",
            "official": official,
            "kol_pool_light": kol_result,
            "ran_at": _stamp(),
        }
    if name == "daily_outreach_digest_only":
        from app.domains import analytics

        digest = analytics.generate_daily_staff_outreach_digest(
            target_date=payload.get("date"),
            limit=int(payload.get("limit") or 100),
            staff=payload.get("staff"),
            product_sku=str(payload.get("product_sku") or ""),
        )
        return {"job": name, "status": "ok", "digest": digest, "ran_at": _stamp()}
    if name == "morning_sync":
        from app.domains import analytics
        from app.domains import channels
        from app.domains import industry as industry_domain
        from app.domains.industry import access as industry_access

        channel_rows = channels.list_channels(staff={}, limit=300).get("channels") or []
        channel_enqueue: dict[str, Any] = {}
        channel_max_posts = int(payload.get("channel_max_posts") or payload.get("max_posts") or 12)
        channel_enqueue = await _queue_channel_syncs(
            channel_rows,
            payload={**payload, "max_posts": channel_max_posts},
            staff=payload.get("staff"),
            queue=queue,
        )

        industry_rows = [
            row
            for row in industry_domain.list_accounts(
                limit=int(payload.get("industry_account_limit") or 100)
            ).get("accounts")
            or []
            if bool(row.get("crawl_enabled"))
        ]
        industry_jobs: list[dict[str, Any]] = []
        for row in industry_rows:
            account_id = int(row.get("id") or 0)
            project_id = int(row.get("project_id") or 0)
            if account_id <= 0 or project_id <= 0:
                continue
            capability = industry_access.issue_server_refresh_capability(
                account_id=account_id,
                project_id=project_id,
            )
            industry_jobs.append(
                {
                    "job_type": "industry_account_refresh",
                    "payload": industry_access.build_refresh_payload(
                        account_id,
                        server_capability=capability,
                    ),
                    "lock_key": f"industry_account_refresh:{account_id}",
                    "timeout_seconds": 1200,
                }
            )
        industry_sync = await _queue_provider_jobs(industry_jobs, queue=queue)

        products = analytics.list_monitored_products(limit=100).get("products") or []
        monitor_jobs: list[dict[str, Any]] = []
        for product in products:
            if str(product.get("enabled") or "1").lower() in {"0", "false", "no"}:
                continue
            platforms = product.get("monitor_platforms_json") or "[]"
            try:
                import json

                platform_list = json.loads(platforms) if isinstance(platforms, str) else platforms
            except Exception:
                platform_list = ["youtube"]
            for platform in (platform_list or ["youtube"]):
                body = {
                    "product_sku": product.get("product_sku"),
                    "platform": platform,
                    "max_videos": int(payload.get("max_videos") or 50),
                    "period_days": int(payload.get("period_days") or 1),
                }
                monitor_jobs.append(
                    {
                        "job_type": "vkpi_analytics_monitor",
                        "payload": {"body": body, "staff": dict(payload.get("staff") or {})},
                        "lock_key": f"vkpi_analytics_monitor:{body['product_sku']}:{platform}",
                        "timeout_seconds": 1200,
                    }
                )
        monitor_runs = await _queue_provider_jobs(monitor_jobs, queue=queue)

        digest = analytics.generate_daily_staff_outreach_digest(
            target_date=payload.get("date"),
            limit=int(payload.get("limit") or 100),
            staff=payload.get("staff"),
            product_sku=str(payload.get("product_sku") or ""),
        )
        return {
            "job": name,
            "status": "queued",
            "channels_synced": 0,
            "channels_enqueued": channel_enqueue.get("channels_enqueued", 0),
            "channels_failed_to_enqueue": channel_enqueue.get("channels_failed_to_enqueue", 0),
            "channel_task_ids": channel_enqueue.get("task_ids", []),
            "industry_accounts_synced": 0,
            "industry_accounts_enqueued": industry_sync.get("enqueued", 0),
            "industry_accounts_skipped": 0,
            "industry_accounts_failed": industry_sync.get("failed_to_enqueue", 0),
            "industry_sync": industry_sync,
            "monitor_runs": monitor_runs.get("enqueued", 0),
            "digest": digest,
            "ran_at": _stamp(),
        }
    raise ValueError("unsupported V-KPI cron job")
