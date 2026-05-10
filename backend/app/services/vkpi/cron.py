"""Run-now wrappers for V-KPI scheduled jobs."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any


def _stamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


async def run_job(job_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    name = str(job_name or "").strip().lower().replace("-", "_")
    if name in {"lineage", "lineage_snapshot"}:
        from app.services.vkpi import metric_lineage

        result = await asyncio.to_thread(metric_lineage.generate_run, period_days=int(payload.get("period_days") or 7), scope_type=str(payload.get("scope_type") or "all"), trigger_source="scheduler_lineage_snapshot", metadata={"source": "cron.run_now"})
        return {"job": name, "status": "ok", "result": result, "ran_at": _stamp()}
    if name in {"kpi", "kpi_rollup", "rollup"}:
        from app.services.vkpi import kpi_ledger

        result = await asyncio.to_thread(kpi_ledger.generate_daily_rollup, payload.get("ledger_date"))
        return {"job": name, "status": "ok", "result": result, "ran_at": _stamp()}
    if name in {"alerts", "alert"}:
        from app.services.vkpi import alerts

        result = await asyncio.to_thread(alerts.generate_alerts)
        return {"job": name, "status": "ok", "result": result, "ran_at": _stamp()}
    if name in {"weekly_report", "report"}:
        from app.services.vkpi import reports

        result = await asyncio.to_thread(reports.generate_weekly_report, period_days=int(payload.get("period_days") or 7), staff=payload.get("staff"), filters=payload)
        return {"job": name, "status": "ok", "result": {k: v for k, v in result.items() if k != "context"}, "ran_at": _stamp()}
    if name in {"analytics_monitor", "product_monitor"}:
        from app.services.vkpi import analytics

        products = analytics.list_monitored_products(limit=50).get("products") or []
        ran = []
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
                ran.append(await analytics.monitor_product({"product_sku": product.get("product_sku"), "platform": platform, "max_videos": int(payload.get("max_videos") or 20)}))
        return {"job": name, "status": "ok", "runs": len(ran), "ran_at": _stamp()}
    if name in {"channels_sync", "channel_sync"}:
        from app.services.vkpi import channels

        rows = channels.list_channels(staff={}, limit=300).get("channels") or []
        results = []
        for row in rows:
            results.append(channels.sync_now(int(row["id"])))
        return {"job": name, "status": "ok", "synced": len(results), "results": results[:20], "ran_at": _stamp()}
    if name in {"daily_outreach_digest_only", "outreach_digest_only"}:
        from app.services.vkpi import analytics

        digest = analytics.generate_daily_staff_outreach_digest(
            target_date=payload.get("date"),
            limit=int(payload.get("limit") or 100),
            staff=payload.get("staff"),
            product_sku=str(payload.get("product_sku") or ""),
        )
        return {"job": name, "status": "ok", "digest": digest, "ran_at": _stamp()}
    if name in {"morning_sync", "daily_morning_sync", "daily_outreach_digest"}:
        from app.services.vkpi import analytics, channels, industry_snapshot_collector

        channel_rows = channels.list_channels(staff={}, limit=300).get("channels") or []
        channel_results = []
        for row in channel_rows:
            channel_results.append(channels.sync_now(int(row["id"])))

        industry_sync = industry_snapshot_collector.sync_enabled_accounts(
            limit=int(payload.get("industry_account_limit") or 100),
            staff=payload.get("staff"),
        )

        products = analytics.list_monitored_products(limit=100).get("products") or []
        monitor_runs = []
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
                monitor_runs.append(
                    await analytics.monitor_product(
                        {
                            "product_sku": product.get("product_sku"),
                            "platform": platform,
                            "max_videos": int(payload.get("max_videos") or 50),
                            "period_days": int(payload.get("period_days") or 1),
                        },
                        staff=payload.get("staff"),
                    )
                )

        digest = analytics.generate_daily_staff_outreach_digest(
            target_date=payload.get("date"),
            limit=int(payload.get("limit") or 100),
            staff=payload.get("staff"),
            product_sku=str(payload.get("product_sku") or ""),
        )
        return {
            "job": name,
            "status": "ok",
            "channels_synced": len(channel_results),
            "industry_accounts_synced": industry_sync.get("synced", 0),
            "industry_accounts_skipped": industry_sync.get("skipped", 0),
            "industry_accounts_failed": industry_sync.get("failed", 0),
            "industry_sync": industry_sync,
            "monitor_runs": len(monitor_runs),
            "digest": digest,
            "ran_at": _stamp(),
        }
    raise ValueError("unsupported V-KPI cron job")
