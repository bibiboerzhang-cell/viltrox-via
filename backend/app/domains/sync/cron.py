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
        "batch_id",
        "completion_wait_seconds",
        "completion_poll_seconds",
    }
    return {key: payload.get(key) for key in sorted(allowed) if key in payload}


def _result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    keys = (
        "job", "status", "ran_at", "runs", "synced", "failed", "channels_synced",
        "channels_enqueued", "industry_accounts_synced", "industry_accounts_skipped",
        "industry_accounts_failed", "monitor_runs", "count", "batch_id",
    )
    summary = {key: result.get(key) for key in keys if key in result}
    completion = result.get("completion") if isinstance(result.get("completion"), dict) else {}
    for key in ("completion_scope", "provider_completion"):
        if key in result or key in completion:
            summary[key] = result.get(key) or completion.get(key)
    summary.update({
        key: completion.get(key)
        for key in ("tasks_total", "tasks_terminal", "tasks_succeeded", "tasks_partial", "tasks_failed", "tasks_skipped_known", "tasks_pending", "sla_expired")
        if key in completion
    })
    if "tasks_total" not in summary and isinstance(result.get("task_ids"), list):
        summary["tasks_total"] = len(result["task_ids"])
    if isinstance(result.get("digest"), dict):
        digest = result["digest"]
        summary["digest"] = {
            "assigned": digest.get("assigned"),
            "generated": digest.get("generated"),
            "staff_count": digest.get("staff_count"),
        }
    return summary


def _system_staff() -> dict[str, Any]:
    return {"id": 0, "staff_id": 0, "user_id": 0, "role": "admin", "is_owner": 1, "email": ""}


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
    audit_metadata = {"policy": policy, "payload": _payload_summary(payload), "validate_only": validate_only}
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
                "job": canonical, "status": "validated", "ran": False,
                "policy": policy, "ran_at": _stamp(),
            }
        else:
            payload["staff"] = staff
            result = await run_job(canonical, payload, queue=queue)
        result_status = str(result.get("status") or "").strip().lower()
        completion = result.get("completion") if isinstance(result.get("completion"), dict) else {}
        provider = str(result.get("provider_completion") or completion.get("provider_completion") or "").lower()
        has_provider_evidence = "completion" in result or "provider_completion" in result
        provider_terminal = provider == "completed" and bool(completion.get("complete")) and completion.get("completion_scope") == "provider_terminal"
        no_work_completed = (
            result_status == "completed" and provider == "not_run"
            and completion.get("completion_scope") == "no_work" and bool(completion.get("complete"))
            and not int(completion.get("tasks_total") or 0) and "enqueue_failures" in result
            and not int(result.get("enqueue_failures") or 0)
        )
        if validate_only or result_status in {"validated", "planned"}:
            action_type, outcome = "cron_run_validated", "validated"
        elif result_status in {"failed", "blocked", "interrupted", "error"}:
            action_type, outcome = "cron_run_failed", "failed"
        elif result_status in {"queued", "partial"} or (has_provider_evidence and not (provider_terminal or no_work_completed)):
            action_type, outcome = "cron_run_accepted", "accepted"
        else:
            action_type, outcome = "cron_run_completed", "completed"
        _log_cron_audit(
            staff=staff,
            action_type=action_type,
            job_name=canonical,
            detail=f"manual cron {outcome}: {canonical}",
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


async def _run_lineage_snapshot(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    del queue
    from app.domains import lineage as metric_lineage

    result = await asyncio.to_thread(
        metric_lineage.generate_run,
        period_days=int(payload.get("period_days") or 7),
        scope_type=str(payload.get("scope_type") or "all"),
        trigger_source="scheduler_lineage_snapshot",
        metadata={"source": "cron.run_now"},
    )
    return {"job": "lineage_snapshot", "status": "ok", "result": result, "ran_at": _stamp()}


async def _run_kpi_rollup(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    del queue
    from app.domains.staff import kpi_ledger

    result = await asyncio.to_thread(kpi_ledger.generate_daily_rollup, payload.get("ledger_date"))
    return {"job": "kpi_rollup", "status": "ok", "result": result, "ran_at": _stamp()}


async def _run_alerts(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    del payload, queue
    from app.domains import alerts

    result = await asyncio.to_thread(alerts.generate_alerts)
    return {"job": "alerts", "status": "ok", "result": result, "ran_at": _stamp()}


async def _run_weekly_report(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    del queue
    from app.domains import reports

    result = await asyncio.to_thread(
        reports.generate_weekly_report,
        period_days=int(payload.get("period_days") or 7),
        staff=payload.get("staff"),
        filters=payload,
    )
    return {"job": "weekly_report", "status": "ok", "result": {key: value for key, value in result.items() if key != "context"}, "ran_at": _stamp()}


def _monitor_provider_jobs(
    products: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    default_max_videos: int,
    period_days: int | None = None,
    normalize_enabled: bool = False,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for product in products:
        enabled = str(product.get("enabled") or "1")
        disabled = enabled.lower() in {"0", "false", "no"} if normalize_enabled else enabled in {"0", "false"}
        if disabled:
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
                "max_videos": int(payload.get("max_videos") or default_max_videos),
            }
            if period_days is not None:
                body["period_days"] = int(payload.get("period_days") or period_days)
            jobs.append(
                {
                    "job_type": "vkpi_analytics_monitor",
                    "payload": {"body": body, "staff": dict(payload.get("staff") or {})},
                    "lock_key": f"vkpi_analytics_monitor:{body['product_sku']}:{platform}",
                    "timeout_seconds": 1200,
                }
            )
    return jobs


async def _run_analytics_monitor(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    from app.domains import analytics

    products = analytics.list_monitored_products(limit=50).get("products") or []
    jobs = _monitor_provider_jobs(products, payload, default_max_videos=20)
    queued = await _queue_provider_jobs(jobs, queue=queue)
    return {"job": "analytics_monitor", "status": "queued", "runs": queued["enqueued"], **queued, "ran_at": _stamp()}


async def _run_channels_sync(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    from app.domains import channels

    rows = channels.list_channels(staff={}, limit=300).get("channels") or []
    max_posts = int(payload.get("channel_max_posts") or payload.get("max_posts") or 12)
    queued = await _queue_channel_syncs(
        rows,
        payload={**payload, "max_posts": max_posts},
        staff=payload.get("staff"),
        queue=queue,
    )
    return {"job": "channels_sync", "status": "queued", **queued, "ran_at": _stamp()}


def _platform_filter(value: Any, defaults: dict[str, int]) -> set[str]:
    if isinstance(value, str):
        return {item.strip().lower() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value if str(item).strip()}
    return set(defaults)


async def _run_official_full_baseline(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    from app.domains import channels

    platform_limits = {
        "youtube": 1000,
        "instagram": 1000,
        "tiktok": 300,
        "facebook": 250,
        "reddit": 150,
        "x": 200,
    }
    platform_filter = _platform_filter(payload.get("platforms"), platform_limits)
    rows = [
        row
        for row in channels.list_channels(staff={}, limit=300).get("channels") or []
        if str(row.get("platform") or "").lower() in platform_filter
    ]
    max_override = int(payload.get("max_posts") or 0)
    queued_rows = [
        {
            **row,
            "_requested_max_posts": max_override
            or platform_limits.get(str(row.get("platform") or "").lower(), 100),
        }
        for row in rows
    ]
    queued = await _queue_channel_syncs(
        queued_rows,
        payload={**payload, "max_posts": max_override or 100},
        staff=payload.get("staff"),
        queue=queue,
    )
    return {
        "job": "official_full_baseline",
        "status": "queued",
        **queued,
        "platforms": sorted(platform_filter),
        "limits": {key: platform_limits[key] for key in sorted(platform_limits) if key in platform_filter},
        "ran_at": _stamp(),
    }


async def _run_daily_incremental_sync(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    from app.domains.sync import daily_batch, daily_sync, refresh_tier
    if daily_sync._bool(payload.get("dry_run")):
        plan = daily_sync.run_daily_incremental({**payload, "dry_run": True})
        return {
            "job": "daily_incremental_sync", "status": "planned",
            "dry_run": True, "run_id": plan.get("run_id"),
            "official": plan.get("official") or {"skipped": True},
            "kol_pool_light": plan.get("kol_pool_light") or {"skipped": True},
            "health": plan.get("health") or {}, "ran_at": _stamp(),
        }
    daily_sync.check_daily_sync_guard(payload)
    owned_queue = None
    effective_queue = queue
    batch_id = ""
    parent_inserted = False
    try:
        if effective_queue is None:
            from app.services.jobs.queue import build_job_queue

            owned_queue = build_job_queue()
            effective_queue = owned_queue
        if effective_queue is None:
            raise RuntimeError("durable job queue unavailable")
        if str(getattr(effective_queue, "backend_name", "")) == "inprocess":
            raise RuntimeError("durable_queue_required:inprocess_queue_has_no_provider_execution_fence")
        if not callable(getattr(effective_queue, "get_status", None)):
            raise RuntimeError("durable_queue_status_reader_required")
        reconciliation = await daily_batch.reconcile_recent_parents(effective_queue)
        if int(reconciliation.get("pending") or 0):
            raise daily_batch.ActiveDailyBatchError("daily_batch_parent_still_running")
        daily_sync.check_daily_sync_guard(payload)
        official_rows: list[dict[str, Any]] = []
        official_skipped = daily_sync._bool(payload.get("skip_official"))
        if not official_skipped:
            from app.domains import channels

            official_rows = channels.list_channels(staff={}, limit=300).get("channels") or []
        selector = daily_sync._kol_refresh_selector(payload)
        kol_allowed = daily_batch.kol_refresh_allowed(daily_sync, payload, selector)
        kol_rows = daily_batch.kol_rows(daily_sync, refresh_tier, payload, selector) if kol_allowed else []
        batch_id = str(payload.get("batch_id") or "").strip()[:128] or daily_batch.new_batch_id()
        official: dict[str, Any] = {"skipped": True} if official_skipped else {
            "channels_enqueued": 0, "channels_requested": 0, "channels_failed_to_enqueue": 0,
            "task_ids": [], "failed": [],
        }
        kol_result: dict[str, Any] = (
            {"skipped": True} if not kol_allowed
            else {"requested": 0, "enqueued": 0, "failed_to_enqueue": 0, "task_ids": [], "failed": []}
        )
        queued: dict[str, Any] = {
            "official": official, "kol_pool_light": kol_result,
            "task_ids": [], "task_links": [], "scheduler": "round_robin_v1",
        }
        requested = len(official_rows) + len(kol_rows)
        wait_seconds = max(0.0, min(19_800.0, float(payload.get("completion_wait_seconds") or 0.0)))
        poll_seconds = max(0.05, min(60.0, float(payload.get("completion_poll_seconds") or 10.0)))
        daily_batch.insert_parent(batch_id, payload, official_rows, kol_rows)
        parent_inserted = True
        if requested:
            def checkpoint_progress(snapshot: dict[str, Any]) -> None:
                daily_batch.checkpoint_parent(
                    batch_id,
                    daily_batch.checkpoint_summary(
                        batch_id, requested, snapshot,
                        official_skipped=official_skipped, kol_skipped=not kol_allowed,
                        phase="enqueueing",
                    ),
                )

            queued = await daily_batch.queue_batch(
                official_rows,
                kol_rows,
                payload=payload,
                staff=payload.get("staff"),
                queue=effective_queue,
                batch_id=batch_id,
                progress_callback=checkpoint_progress,
            )
        checkpoint = daily_batch.checkpoint_summary(
            batch_id, requested, queued,
            official_skipped=official_skipped, kol_skipped=not kol_allowed,
        )
        batch = checkpoint["batch"]
        official, kol_result = checkpoint["official"], checkpoint["kol_pool_light"]
        task_ids = list(batch["task_ids"])
        enqueue_failures = int(checkpoint["enqueue_failures"])
        daily_batch.checkpoint_parent(batch_id, checkpoint)
        receipt_callback = payload.get("_batch_receipt_callback")
        if callable(receipt_callback):
            try:
                callback_result = receipt_callback({**batch, "phase": "children_enqueued", "official": official, "kol_pool_light": kol_result, "enqueue_failures": enqueue_failures})
                if hasattr(callback_result, "__await__"):
                    await callback_result
            except Exception:
                logger.warning("daily batch enqueue receipt emission failed", exc_info=True)
        completion = await daily_batch.observe(
            effective_queue,
            task_ids,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
        )
        status = daily_batch.result_status(completion, enqueue_failures=enqueue_failures)
        result = {
            "job": "daily_incremental_sync", "status": status, "batch_id": batch_id,
            "task_ids": task_ids, "batch": batch,
            "completion_scope": completion.get("completion_scope"),
            "provider_completion": completion.get("provider_completion"),
            "completion": completion, "official": official, "kol_pool_light": kol_result,
            "enqueue_failures": enqueue_failures, "ran_at": _stamp(),
        }
        parent_status = status if completion.get("complete") or not task_ids else "queued"
        daily_batch.finish_parent(batch_id, parent_status, result)
        return result
    except Exception as exc:
        if parent_inserted:
            try:
                daily_batch.fail_parent(batch_id, exc)
            except Exception:
                logger.warning("daily batch parent failure checkpoint failed", exc_info=True)
        raise
    finally:
        if owned_queue is not None:
            close_fn = getattr(owned_queue, "close", None)
            if close_fn is not None:
                close_result = close_fn()
                if hasattr(close_result, "__await__"):
                    await close_result


async def _run_daily_outreach_digest_only(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    del queue
    from app.domains import analytics

    digest = analytics.generate_daily_staff_outreach_digest(
        target_date=payload.get("date"),
        limit=int(payload.get("limit") or 100),
        staff=payload.get("staff"),
        product_sku=str(payload.get("product_sku") or ""),
    )
    return {"job": "daily_outreach_digest_only", "status": "ok", "digest": digest, "ran_at": _stamp()}


def _industry_refresh_jobs(
    rows: list[dict[str, Any]],
    industry_access: Any,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for row in rows:
        account_id = int(row.get("id") or 0)
        project_id = int(row.get("project_id") or 0)
        if account_id <= 0 or project_id <= 0:
            continue
        capability = industry_access.issue_server_refresh_capability(
            account_id=account_id,
            project_id=project_id,
        )
        jobs.append(
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
    return jobs


async def _run_morning_sync(payload: dict[str, Any], queue: Any | None) -> dict[str, Any]:
    from app.domains import analytics, channels
    from app.domains import industry as industry_domain
    from app.domains.industry import access as industry_access

    channel_rows = channels.list_channels(staff={}, limit=300).get("channels") or []
    channel_max_posts = int(
        payload.get("channel_max_posts") or payload.get("max_posts") or 12
    )
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
    industry_jobs = _industry_refresh_jobs(industry_rows, industry_access)
    industry_sync = await _queue_provider_jobs(industry_jobs, queue=queue)
    products = analytics.list_monitored_products(limit=100).get("products") or []
    monitor_jobs = _monitor_provider_jobs(
        products,
        payload,
        default_max_videos=50,
        period_days=1,
        normalize_enabled=True,
    )
    monitor_runs = await _queue_provider_jobs(monitor_jobs, queue=queue)
    digest = analytics.generate_daily_staff_outreach_digest(
        target_date=payload.get("date"),
        limit=int(payload.get("limit") or 100),
        staff=payload.get("staff"),
        product_sku=str(payload.get("product_sku") or ""),
    )
    return {
        "job": "morning_sync",
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


_JOB_HANDLERS = {
    "lineage_snapshot": _run_lineage_snapshot,
    "kpi_rollup": _run_kpi_rollup,
    "alerts": _run_alerts,
    "weekly_report": _run_weekly_report,
    "analytics_monitor": _run_analytics_monitor,
    "channels_sync": _run_channels_sync,
    "official_full_baseline": _run_official_full_baseline,
    "daily_incremental_sync": _run_daily_incremental_sync,
    "daily_outreach_digest_only": _run_daily_outreach_digest_only,
    "morning_sync": _run_morning_sync,
}


async def run_job(job_name: str, payload: dict[str, Any] | None = None, *, queue: Any | None = None) -> dict[str, Any]:
    payload = payload or {}
    name = normalize_job_name(job_name)
    handler = _JOB_HANDLERS.get(name)
    if handler is None:
        raise ValueError("unsupported V-KPI cron job")
    return await handler(payload, queue)
