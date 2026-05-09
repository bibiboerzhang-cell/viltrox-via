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
from app.services.vkpi import platform_crawl_settings


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
                   total_orders, success_orders, failed_orders, error_message
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
                except Exception:
                    pass
                result[job] = {
                    "last_run_at": row_dict.get("created_at"),
                    "status": metadata.get("action_status") or "unknown",
                    "detail": row_dict.get("detail") or "",
                }
            else:
                result[job] = {"last_run_at": None, "status": "never_run"}
        
        return result
    except Exception as exc:
        logger.warning("sync_status._cron_status failed: %s", exc)
        return {"error": str(exc)}


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
