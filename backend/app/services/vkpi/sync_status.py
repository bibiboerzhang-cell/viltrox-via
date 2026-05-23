"""backend/app/services/vkpi/sync_status.py

R60: Sync 状态聚合服务

为前端 SyncStatusPanel 提供统一的同步状态视图,
聚合多个数据源:
  - vkpi_industry_accounts (sync_status / last_successful_at / crawl_error_count)
  - vkpi_shopify_sync_runs (Shopify 同步历史)
  - vkpi_business_audit_logs (cron job 历史 - morning_sync 等)
  - vkpi_platform_crawl_settings (各平台抓取开关 + budget 状态)

设计原则:
  - 只读聚合,不修改任何数据
  - 装饰器: 调用方自己加 @firewall_check / @audit_action
  - 失败优雅降级 (单个数据源挂了不影响整体)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.services.vkpi import daily_sync, platform_crawl_settings


logger = get_logger(__name__)


# ─── 主聚合接口 ──────────────────────────────────


def get_overview() -> dict[str, Any]:
    """
    返回 Sync Status 全景:
    
    {
      "industry": {
        "total_accounts": int,
        "sync_status_breakdown": {"ok": N, "failed": N, "pending": N},
        "last_24h_success": int,
        "last_24h_failed": int,
        "platforms": [{"platform": "youtube", "accounts": N, "ok_rate": 0.95, ...}]
      },
      "shopify": {
        "last_run_at": "2026-05-09T...",
        "last_run_status": "ok",
        "recent_runs": [...]
      },
      "cron_jobs": {
        "morning_sync": {"last_run_at": "...", "status": "ok"},
        ...
      },
      "platform_settings": {
        "instagram": {"crawl_enabled": false, "budget_remaining": 100, ...}
      },
      "summary": {
        "overall_health": "healthy" | "degraded" | "down",
        "issues": [{"severity": "warning", "message": "..."}]
      }
    }
    """
    return {
        "industry": _industry_status(),
        "shopify": _shopify_status(),
        "cron_jobs": _cron_status(),
        "daily_sync": _daily_sync_status(),
        "platform_settings": _platform_settings_status(),
        "summary": _summary_health(),
    }


def get_industry_recent_failures(limit: int = 50) -> dict[str, Any]:
    """最近 N 次 industry 抓取失败,前端用于详情查看"""
    rows = get_conn().execute(
        """
        SELECT a.id, a.platform, a.handle, a.display_name AS account_name,
               a.sync_status, a.last_crawled_at, a.last_successful_at,
               a.crawl_error_count, '' AS last_error_message
        FROM vkpi_industry_accounts a
        WHERE LOWER(COALESCE(a.sync_status, '')) IN ('failed', 'error')
           OR COALESCE(a.crawl_error_count, 0) > 0
        ORDER BY a.last_crawled_at DESC NULLS LAST
        LIMIT ?
        """,
        (max(1, min(500, int(limit or 50))),),
    ).fetchall()
    return {"failures": [dict(row) for row in rows]}


# ─── industry 状态 ──────────────────────────────


def _industry_status() -> dict[str, Any]:
    """从 vkpi_industry_accounts 聚合 sync 状态"""
    try:
        conn = get_conn()
        
        # 总账号数 + 状态分布
        rows = conn.execute(
            """
            SELECT
                COALESCE(LOWER(sync_status), 'unknown') AS status,
                COUNT(*) AS n
            FROM vkpi_industry_accounts
            WHERE COALESCE(is_active, false) = true
            GROUP BY LOWER(sync_status)
            """,
        ).fetchall()
        
        breakdown = {}
        total = 0
        for row in rows:
            row_dict = dict(row)
            status = row_dict.get("status") or "unknown"
            count = int(row_dict.get("n") or 0)
            breakdown[status] = count
            total += count
        
        # 最近 24h 成功 / 失败
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_24h = conn.execute(
            """
            SELECT
                SUM(CASE WHEN last_successful_at >= ? THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN last_crawled_at >= ?
                         AND (last_successful_at IS NULL OR last_successful_at < last_crawled_at)
                    THEN 1 ELSE 0 END) AS fail_count
            FROM vkpi_industry_accounts
            WHERE COALESCE(is_active, false) = true
            """,
            (cutoff_24h, cutoff_24h),
        ).fetchone()
        
        # 按平台聚合
        platform_rows = conn.execute(
            """
            SELECT platform,
                   COUNT(*) AS total_accounts,
                   SUM(CASE WHEN LOWER(COALESCE(sync_status, '')) = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                   SUM(CASE WHEN LOWER(COALESCE(sync_status, '')) IN ('failed', 'error') THEN 1 ELSE 0 END) AS failed_count
            FROM vkpi_industry_accounts
            WHERE COALESCE(is_active, false) = true
            GROUP BY platform
            ORDER BY platform
            """,
        ).fetchall()
        
        platforms = []
        for row in platform_rows:
            row_dict = dict(row)
            total_p = int(row_dict.get("total_accounts") or 0)
            ok_count = int(row_dict.get("ok_count") or 0)
            failed_count = int(row_dict.get("failed_count") or 0)
            ok_rate = (ok_count / total_p) if total_p > 0 else 0.0
            platforms.append({
                "platform": row_dict.get("platform"),
                "total_accounts": total_p,
                "ok_count": ok_count,
                "failed_count": failed_count,
                "ok_rate": round(ok_rate, 3),
            })
        
        return {
            "total_accounts": total,
            "sync_status_breakdown": breakdown,
            "last_24h_success": int(recent_24h["success_count"] or 0) if recent_24h else 0,
            "last_24h_failed": int(recent_24h["fail_count"] or 0) if recent_24h else 0,
            "platforms": platforms,
        }
    except Exception as exc:
        logger.warning("sync_status._industry_status failed: %s", exc)
        return {"error": str(exc), "total_accounts": 0}


# ─── shopify 状态 ──────────────────────────────


def _shopify_status() -> dict[str, Any]:
    """从 vkpi_shopify_sync_runs 取最近运行"""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT id, started_at, completed_at, status,
                   orders_received, orders_matched, orders_unmatched,
                   orders_failed, error_message
            FROM vkpi_shopify_sync_runs
            ORDER BY started_at DESC, id DESC
            LIMIT 10
            """,
        ).fetchall()
        
        if not rows:
            return {"last_run_at": None, "last_run_status": "never_run", "recent_runs": []}
        
        recent = [dict(row) for row in rows]
        last = recent[0]
        return {
            "last_run_at": last.get("started_at"),
            "last_run_status": last.get("status"),
            "recent_runs": recent,
        }
    except Exception as exc:
        logger.warning("sync_status._shopify_status failed: %s", exc)
        return {"error": str(exc), "last_run_at": None}


# ─── cron 历史 (从 audit log 抓取) ────────────


_CRON_JOBS_TO_TRACK = [
    "morning_sync",
    "kpi_rollup",
    "lineage_snapshot",
    "channels_sync",
    "weekly_report",
    "alerts",
    "analytics_monitor",
]


def _cron_status() -> dict[str, Any]:
    """
    从 vkpi_business_audit_logs 抓取 cron job 运行历史.
    
    依赖: cron_run endpoint 通过 audit_decorator 落审计.
    如果之前没装饰,就只能取到部分数据.
    """
    try:
        conn = get_conn()
        result = {}
        
        for job in _CRON_JOBS_TO_TRACK:
            row = conn.execute(
                """
                SELECT created_at, metadata_json, detail
                FROM vkpi_business_audit_logs
                WHERE action_type = 'cron_run_completed'
                  AND target_type = 'cron_job'
                  AND target_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (job,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT created_at, metadata_json, detail
                    FROM vkpi_business_audit_logs
                    WHERE action_type = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (f"cron_run_{job}",),
                ).fetchone()
            
            if row:
                row_dict = dict(row)
                metadata = {}
                try:
                    metadata = json.loads(row_dict.get("metadata_json") or "{}")
                except Exception as exc:
                    logger.warning("sync_status cron metadata parse failed for %s: %s", job, exc)
                result[job] = {
                    "last_run_at": row_dict.get("created_at"),
                    "status": metadata.get("action_status") or metadata.get("result", {}).get("status") or "unknown",
                    "detail": row_dict.get("detail") or "",
                }
            else:
                result[job] = {"last_run_at": None, "status": "never_run"}
        
        return result
    except Exception as exc:
        logger.warning("sync_status._cron_status failed: %s", exc)
        return {"error": str(exc)}


# ─── daily sync guard 状态 ───────────────────────


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except Exception as exc:
        logger.warning("sync_status json parse failed: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    summary = _json_dict(row.get("summary_json"))
    health = summary.get("health") if isinstance(summary.get("health"), dict) else {}
    if not health:
        health = daily_sync._sync_health_from_summary(summary)
    return {
        "run_id": row.get("run_id"),
        "job_name": row.get("job_name"),
        "stage": row.get("stage"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "status": row.get("status"),
        "total_targets": int(row.get("total_targets") or 0),
        "last_success_index": int(row.get("last_success_index") or 0),
        "interrupted_at_index": row.get("interrupted_at_index"),
        "interrupted_kol_pool_id": row.get("interrupted_kol_pool_id"),
        "reason": row.get("reason") or "",
        "error_type": row.get("error_type") or "",
        "error_class": row.get("error_class") or "",
        "error_message": row.get("error_message") or "",
        "health": health,
    }


def _daily_sync_status() -> dict[str, Any]:
    """Expose daily sync guard state without mutating the guard ledger."""
    try:
        conn = get_conn()
        rows = conn.execute(
            """
            SELECT run_id, job_name, stage, started_at, finished_at, status,
                   total_targets, last_success_index, interrupted_at_index,
                   interrupted_kol_pool_id, reason, error_type, error_class,
                   error_message, summary_json
            FROM vkpi_sync_runs
            WHERE job_name = ?
            ORDER BY started_at DESC NULLS LAST, created_at DESC NULLS LAST
            LIMIT 10
            """,
            ("daily_incremental_sync",),
        ).fetchall()
        recent_runs = [_serialize_run(dict(row)) for row in rows]
        latest_summary = next((row for row in recent_runs if row.get("stage") == "daily_summary"), None)
        latest_run = recent_runs[0] if recent_runs else None
        latest_ack = daily_sync._latest_sync_ack("daily_incremental_sync")
        blocking_run = daily_sync._blocking_sync_run("daily_incremental_sync")
        return {
            "guard_allowed": blocking_run is None,
            "ack_required": blocking_run is not None,
            "failure_rate_threshold": daily_sync.SYNC_FAILURE_RATE_THRESHOLD,
            "blocking_run": blocking_run,
            "latest_ack": latest_ack,
            "latest_summary": latest_summary,
            "latest_run": latest_run,
            "recent_runs": recent_runs,
        }
    except Exception as exc:
        logger.warning("sync_status._daily_sync_status failed: %s", exc)
        return {
            "error": str(exc),
            "guard_allowed": True,
            "ack_required": False,
            "failure_rate_threshold": daily_sync.SYNC_FAILURE_RATE_THRESHOLD,
            "recent_runs": [],
        }


# ─── 平台设置 + budget 状态 ──────────────────


def _platform_settings_status() -> dict[str, Any]:
    """各平台抓取开关 + budget 余额"""
    try:
        settings_data = platform_crawl_settings.platform_settings()
        budget_data = platform_crawl_settings.budget_settings()
        
        platforms = settings_data.get("platforms", [])
        budgets = {row["budget_key"]: row for row in budget_data.get("budgets", [])}
        
        result = {}
        for p in platforms:
            platform = p.get("platform")
            monthly_budget = float(p.get("monthly_budget_usd") or 0)
            crawl_enabled = bool(int(p.get("crawl_enabled") or 0))
            
            result[platform] = {
                "crawl_enabled": crawl_enabled,
                "monthly_budget_usd": monthly_budget,
                "daily_account_limit": int(p.get("daily_account_limit") or 0),
                "last_test_status": p.get("last_test_status") or "not_configured",
            }
        
        # 全局 budget
        result["_global_budgets"] = [
            {
                "budget_key": b.get("budget_key"),
                "monthly_limit_usd": float(b.get("monthly_limit_usd") or 0),
                "current_month_spent": float(b.get("current_month_spent") or 0),
                "alert_threshold_pct": int(b.get("alert_threshold_pct") or 80),
                "enabled": bool(int(b.get("enabled") or 0)),
            }
            for b in budget_data.get("budgets", [])
        ]
        
        return result
    except Exception as exc:
        logger.warning("sync_status._platform_settings_status failed: %s", exc)
        return {"error": str(exc)}


# ─── 整体健康度 ──────────────────────────────


def _summary_health() -> dict[str, Any]:
    """
    根据各子系统状态计算整体健康度.
    
    healthy:  全部正常
    degraded: 部分平台失败,但核心功能可用
    down:     核心系统不可用
    """
    issues = []
    
    try:
        industry = _industry_status()
        last_24h_failed = industry.get("last_24h_failed", 0)
        if last_24h_failed > 5:
            issues.append({
                "severity": "warning",
                "category": "industry",
                "message": f"Industry 同步过去 24h 有 {last_24h_failed} 次失败",
            })

        daily = _daily_sync_status()
        if daily.get("ack_required"):
            run = daily.get("blocking_run") if isinstance(daily.get("blocking_run"), dict) else {}
            issues.append({
                "severity": "critical",
                "category": "daily_sync",
                "message": f"Daily sync 已被 guard 暂停，需要 ack：{run.get('run_id') or 'unknown'}",
            })
        
        # 检查平台 budget 是否快用完
        ps = _platform_settings_status()
        for budget in ps.get("_global_budgets", []):
            limit = budget.get("monthly_limit_usd", 0)
            spent = budget.get("current_month_spent", 0)
            threshold = budget.get("alert_threshold_pct", 80)
            if limit > 0:
                used_pct = (spent / limit) * 100
                if used_pct >= threshold:
                    issues.append({
                        "severity": "warning" if used_pct < 100 else "critical",
                        "category": "budget",
                        "message": f"预算 {budget['budget_key']} 已用 {used_pct:.1f}% (阈值 {threshold}%)",
                    })
    except Exception as exc:
        logger.warning("sync_status._summary_health failed: %s", exc)
        issues.append({"severity": "error", "category": "system", "message": str(exc)})
    
    # 整体级别
    if any(i.get("severity") == "critical" for i in issues):
        overall = "down"
    elif any(i.get("severity") == "warning" for i in issues):
        overall = "degraded"
    else:
        overall = "healthy"
    
    return {
        "overall_health": overall,
        "issues": issues,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
