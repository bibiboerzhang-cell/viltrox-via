"""Feature flags, crawl limits, and budget settings for V-KPI v2."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.services.vkpi import audit
from app.services.vkpi.schema_product_industry import ensure_vkpi_product_industry_schema
from app.services.vkpi.workflow import staff_id as resolve_staff_id

DEFAULT_FLAGS = {
    "product_analysis": "产品分析入口",
    "daily_staff_digest": "每日 8 点员工候选内容同步",
    "comment_intelligence_alerts": "评论风险告警阈值和开关",
    "audience_graph_l1": "粉丝图谱 L1 聚合匹配",
    "audience_graph_l2": "粉丝图谱 L2 相似受众",
    "audience_graph_l3": "粉丝图谱 L3 明细抓取，默认关闭",
    "auto_budget_allocation": "预算自动分配，默认关闭",
    "ml_scoring": "机器学习评分，默认关闭",
    "llm_summary": "大模型总结润色，默认关闭",
    "youtube_kpi_reserved": "YouTube KPI 接入预留",
}

DEFAULT_FLAG_ENABLED = {
    "comment_intelligence_alerts": True,
}

DEFAULT_COMMENT_ALERT_SETTINGS = {
    "window_days": 7,
    "min_negative": 3,
    "min_critical": 2,
    "min_hostile": 1,
}

DEFAULT_PLATFORMS = [
    "youtube",
    "instagram",
    "tiktok",
    "xiaohongshu",
    "bilibili",
    "facebook",
    "reddit",
    "x",
    "twitch",
    "threads",
    "pinterest",
    "website",
    "other",
]

DEFAULT_BUDGETS = {
    "apify": 0,
    "llm": 0,
    "crawl_total": 0,
    "audience_graph": 0,
}

APIFY_CRAWL_PLATFORMS = {
    "instagram",
    "tiktok",
    "xiaohongshu",
    "bilibili",
    "facebook",
    "reddit",
    "x",
}
logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _load_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception as exc:
        logger.warning("vkpi platform crawl settings json parse failed: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bool(value: Any) -> bool | int:
    if isinstance(value, str):
        value = value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    value = bool(value)
    return value if is_postgres_runtime() else (1 if value else 0)


def _enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _budget_available(row: dict[str, Any] | None) -> tuple[bool, float, float, float]:
    row = row or {}
    monthly = float(row.get("monthly_limit_usd") or 0)
    spent = float(row.get("current_month_spent") or 0)
    remaining = max(monthly - spent, 0)
    return _enabled(row.get("enabled")) and monthly > 0 and remaining > 0, monthly, spent, remaining


def ensure_defaults() -> None:
    ensure_vkpi_product_industry_schema()
    conn = get_conn()
    now = _utcnow()
    for key, desc in DEFAULT_FLAGS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO vkpi_feature_flags
                (flag_key, enabled, description, updated_at, metadata_json)
            VALUES (?,?,?,?,?)
            """,
            (
                key,
                _bool(DEFAULT_FLAG_ENABLED.get(key, False)),
                desc,
                now,
                _json(DEFAULT_COMMENT_ALERT_SETTINGS if key == "comment_intelligence_alerts" else {}),
            ),
        )
    for platform in DEFAULT_PLATFORMS:
        conn.execute(
            """
            INSERT OR IGNORE INTO vkpi_platform_crawl_settings
                (platform, crawl_enabled, daily_account_limit, posts_per_account, crawl_comments,
                 crawl_followers, crawl_audience_graph, only_uncontacted_kols, monthly_budget_usd,
                 last_test_status, updated_at, metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (platform, _bool(False), 0, 0, _bool(False), _bool(False), _bool(False), _bool(True), 0, "not_configured", now, "{}"),
        )
    for key, limit in DEFAULT_BUDGETS.items():
        conn.execute(
            """
            INSERT OR IGNORE INTO vkpi_budget_settings
                (budget_key, monthly_limit_usd, current_month_spent, alert_threshold_pct, enabled, updated_at, metadata_json)
            VALUES (?,?,?,?,?,?,?)
            """,
            (key, float(limit), 0, 80, _bool(False), now, "{}"),
        )
    conn.commit()


def feature_flags() -> dict[str, Any]:
    ensure_defaults()
    rows = get_conn().execute("SELECT * FROM vkpi_feature_flags ORDER BY flag_key").fetchall()
    return {"flags": [dict(row) for row in rows]}


def _comment_alert_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    metadata = _load_json((row or {}).get("metadata_json"))
    settings = dict(DEFAULT_COMMENT_ALERT_SETTINGS)
    for key in settings:
        if key in metadata:
            settings[key] = metadata[key]
    return {
        "enabled": bool((row or {}).get("enabled", True)),
        "description": (row or {}).get("description") or DEFAULT_FLAGS["comment_intelligence_alerts"],
        "window_days": max(1, min(90, int(settings.get("window_days") or DEFAULT_COMMENT_ALERT_SETTINGS["window_days"]))),
        "min_negative": max(1, min(999, int(settings.get("min_negative") or DEFAULT_COMMENT_ALERT_SETTINGS["min_negative"]))),
        "min_critical": max(1, min(999, int(settings.get("min_critical") or DEFAULT_COMMENT_ALERT_SETTINGS["min_critical"]))),
        "min_hostile": max(1, min(999, int(settings.get("min_hostile") or DEFAULT_COMMENT_ALERT_SETTINGS["min_hostile"]))),
    }


def comment_alert_settings() -> dict[str, Any]:
    ensure_defaults()
    row = get_conn().execute(
        "SELECT * FROM vkpi_feature_flags WHERE flag_key='comment_intelligence_alerts'"
    ).fetchone()
    return {"settings": _comment_alert_payload(dict(row) if row else None)}


def update_comment_alert_settings(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_defaults()
    actor = resolve_staff_id(staff)
    conn = get_conn()
    old = conn.execute("SELECT * FROM vkpi_feature_flags WHERE flag_key='comment_intelligence_alerts'").fetchone()
    current = _comment_alert_payload(dict(old) if old else None)

    def pick(name: str) -> Any:
        return payload[name] if name in payload else current.get(name)

    next_settings = {
        "window_days": max(1, min(90, int(pick("window_days") or DEFAULT_COMMENT_ALERT_SETTINGS["window_days"]))),
        "min_negative": max(1, min(999, int(pick("min_negative") or DEFAULT_COMMENT_ALERT_SETTINGS["min_negative"]))),
        "min_critical": max(1, min(999, int(pick("min_critical") or DEFAULT_COMMENT_ALERT_SETTINGS["min_critical"]))),
        "min_hostile": max(1, min(999, int(pick("min_hostile") or DEFAULT_COMMENT_ALERT_SETTINGS["min_hostile"]))),
    }
    enabled = payload.get("enabled", current.get("enabled", True))
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO vkpi_feature_flags
            (flag_key, enabled, description, updated_by_staff_id, updated_at, metadata_json)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(flag_key) DO UPDATE SET
            enabled=excluded.enabled,
            description=excluded.description,
            updated_by_staff_id=excluded.updated_by_staff_id,
            updated_at=excluded.updated_at,
            metadata_json=excluded.metadata_json
        """,
        (
            "comment_intelligence_alerts",
            _bool(enabled),
            DEFAULT_FLAGS["comment_intelligence_alerts"],
            actor or None,
            now,
            _json(next_settings),
        ),
    )
    if actor:
        audit.log_settings_change(
            staff_id=actor,
            change_type="comment_alert_threshold",
            setting_key="comment_intelligence_alerts",
            old_value_redacted=_json(current),
            new_value_redacted=_json({"enabled": bool(enabled), **next_settings}),
            metadata={"flag_key": "comment_intelligence_alerts"},
        )
    conn.commit()
    return comment_alert_settings()


def update_feature_flags(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_defaults()
    actor = resolve_staff_id(staff)
    conn = get_conn()
    now = _utcnow()
    updates = payload.get("flags") if isinstance(payload.get("flags"), list) else []
    for item in updates:
        if not isinstance(item, dict) or not str(item.get("flag_key") or "").strip():
            continue
        key = str(item.get("flag_key")).strip()
        old = conn.execute("SELECT * FROM vkpi_feature_flags WHERE flag_key=?", (key,)).fetchone()
        conn.execute(
            """
            INSERT INTO vkpi_feature_flags
                (flag_key, enabled, description, updated_by_staff_id, updated_at, metadata_json)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(flag_key) DO UPDATE SET
                enabled=excluded.enabled,
                description=COALESCE(NULLIF(excluded.description, ''), vkpi_feature_flags.description),
                updated_by_staff_id=excluded.updated_by_staff_id,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                key,
                _bool(item.get("enabled")),
                str(item.get("description") or ""),
                actor or None,
                now,
                _json(item.get("metadata") or item.get("metadata_json") or {}),
            ),
        )
        if actor:
            audit.log_settings_change(
                staff_id=actor,
                change_type="feature_flag",
                setting_key=key,
                old_value_redacted=str(dict(old) if old else {}),
                new_value_redacted=str({"enabled": _bool(item.get("enabled"))}),
                metadata={"metadata": item.get("metadata") or item.get("metadata_json") or {}, "flag_key": key},
            )
    conn.commit()
    return feature_flags()


def platform_settings() -> dict[str, Any]:
    ensure_defaults()
    rows = get_conn().execute("SELECT * FROM vkpi_platform_crawl_settings ORDER BY platform").fetchall()
    return {"platforms": [dict(row) for row in rows], "daily_sync_time": "08:00", "timezone": "Asia/Shanghai"}


def update_platform_settings(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_defaults()
    actor = resolve_staff_id(staff)
    conn = get_conn()
    now = _utcnow()
    updates = payload.get("platforms") if isinstance(payload.get("platforms"), list) else []
    for item in updates:
        if not isinstance(item, dict) or not str(item.get("platform") or "").strip():
            continue
        platform = str(item.get("platform")).strip().lower()
        old = conn.execute("SELECT * FROM vkpi_platform_crawl_settings WHERE platform=?", (platform,)).fetchone()
        old_data = dict(old) if old else {}

        def pick(name: str, default: Any = None) -> Any:
            return item[name] if name in item else old_data.get(name, default)

        metadata_json = _json(item.get("metadata")) if "metadata" in item else str(old_data.get("metadata_json") or "{}")

        conn.execute(
            """
            INSERT INTO vkpi_platform_crawl_settings
                (platform, crawl_enabled, daily_account_limit, posts_per_account, crawl_comments,
                 crawl_followers, crawl_audience_graph, only_uncontacted_kols, include_company_accounts,
                 include_competitor_accounts, include_candidate_kols, monthly_budget_usd, failure_threshold,
                 last_test_status, updated_by_staff_id, updated_at, metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform) DO UPDATE SET
                crawl_enabled=excluded.crawl_enabled,
                daily_account_limit=excluded.daily_account_limit,
                posts_per_account=excluded.posts_per_account,
                crawl_comments=excluded.crawl_comments,
                crawl_followers=excluded.crawl_followers,
                crawl_audience_graph=excluded.crawl_audience_graph,
                only_uncontacted_kols=excluded.only_uncontacted_kols,
                include_company_accounts=excluded.include_company_accounts,
                include_competitor_accounts=excluded.include_competitor_accounts,
                include_candidate_kols=excluded.include_candidate_kols,
                monthly_budget_usd=excluded.monthly_budget_usd,
                failure_threshold=excluded.failure_threshold,
                last_test_status=excluded.last_test_status,
                updated_by_staff_id=excluded.updated_by_staff_id,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                platform,
                _bool(pick("crawl_enabled", False)),
                int(pick("daily_account_limit", 0) or 0),
                int(pick("posts_per_account", 0) or 0),
                _bool(pick("crawl_comments", False)),
                _bool(pick("crawl_followers", False)),
                _bool(pick("crawl_audience_graph", False)),
                _bool(pick("only_uncontacted_kols", True)),
                _bool(pick("include_company_accounts", True)),
                _bool(pick("include_competitor_accounts", True)),
                _bool(pick("include_candidate_kols", True)),
                float(pick("monthly_budget_usd", 0) or 0),
                int(pick("failure_threshold", 5) or 5),
                str(pick("last_test_status", "not_configured") or "not_configured"),
                actor or None,
                now,
                metadata_json,
            ),
        )
        if actor:
            new_audit_value = {
                "crawl_enabled": _bool(pick("crawl_enabled", False)),
                "daily_account_limit": int(pick("daily_account_limit", 0) or 0),
                "posts_per_account": int(pick("posts_per_account", 0) or 0),
                "monthly_budget_usd": float(pick("monthly_budget_usd", 0) or 0),
            }
            audit.log_settings_change(
                staff_id=actor,
                change_type="platform_crawl",
                setting_key=platform,
                old_value_redacted=str(dict(old) if old else {}),
                new_value_redacted=str(new_audit_value),
                metadata={"metadata": item.get("metadata") or {}, "platform": platform},
            )
    conn.commit()
    return platform_settings()


def budget_settings() -> dict[str, Any]:
    ensure_defaults()
    rows = get_conn().execute("SELECT * FROM vkpi_budget_settings ORDER BY budget_key").fetchall()
    return {"budgets": [dict(row) for row in rows]}


def crawl_budget_gate(platform: str) -> dict[str, Any]:
    """Return the global budget gate used by live platform crawls.

    A platform-level monthly budget is not enough by itself. Live crawling also
    needs the global crawl budget enabled, and Apify-backed platforms need the
    Apify budget enabled. This keeps Settings and Data Analysis refresh behavior
    aligned with the management budget controls.
    """
    platform_key = str(platform or "other").strip().lower()
    budgets = {str(row.get("budget_key") or "").lower(): dict(row) for row in budget_settings().get("budgets") or []}

    crawl_total_ok, crawl_monthly, crawl_spent, crawl_remaining = _budget_available(budgets.get("crawl_total"))
    if not crawl_total_ok:
        return {
            "allowed": False,
            "reason": "crawl_total_budget_disabled",
            "message": "全局 crawl_total 预算未启用或余额为 0，未执行外部抓取。",
            "budget_key": "crawl_total",
            "monthly_limit_usd": crawl_monthly,
            "current_month_spent_usd": crawl_spent,
            "remaining_usd": crawl_remaining,
        }

    if platform_key in APIFY_CRAWL_PLATFORMS:
        apify_ok, apify_monthly, apify_spent, apify_remaining = _budget_available(budgets.get("apify"))
        if not apify_ok:
            return {
                "allowed": False,
                "reason": "apify_budget_disabled",
                "message": "该平台走 Apify 链路，apify 预算未启用或余额为 0，未执行外部抓取。",
                "budget_key": "apify",
                "monthly_limit_usd": apify_monthly,
                "current_month_spent_usd": apify_spent,
                "remaining_usd": apify_remaining,
            }

    return {"allowed": True, "reason": "passed", "message": "预算闸门通过。"}


def control_status() -> dict[str, Any]:
    """Return the management control summary for high-cost automation.

    This endpoint is intentionally read-only: it lets management verify which
    expensive capabilities are enabled without triggering crawls, LLM calls, or
    budget allocation.
    """
    flags = feature_flags().get("flags") or []
    platforms = platform_settings().get("platforms") or []
    budgets = budget_settings().get("budgets") or []
    flag_map = {str(row.get("flag_key")): row for row in flags}
    high_cost_keys = {
        "audience_graph_l1",
        "audience_graph_l2",
        "audience_graph_l3",
        "auto_budget_allocation",
        "ml_scoring",
        "llm_summary",
    }
    high_cost_controls = []
    for key in sorted(high_cost_keys):
        row = flag_map.get(key) or {"flag_key": key, "enabled": 0, "description": DEFAULT_FLAGS.get(key, "")}
        high_cost_controls.append(
            {
                "flag_key": key,
                "enabled": bool(row.get("enabled")),
                "description": row.get("description") or DEFAULT_FLAGS.get(key, ""),
                "requires_budget": key in {"audience_graph_l2", "audience_graph_l3", "auto_budget_allocation", "ml_scoring", "llm_summary"},
            }
        )
    enabled_platforms = [row for row in platforms if bool(row.get("crawl_enabled"))]
    youtube_row = next((row for row in platforms if str(row.get("platform")) == "youtube"), {})
    budget_total = sum(float(row.get("monthly_limit_usd") or 0) for row in budgets if bool(row.get("enabled")))
    budget_spent = sum(float(row.get("current_month_spent") or 0) for row in budgets if bool(row.get("enabled")))
    return {
        "sync_policy": {
            "daily_sync_time": "08:00",
            "timezone": "Asia/Shanghai",
            "candidate_limit_per_staff": 100,
            "only_uncontacted_kols": True,
            "company_accounts_default": "included_for_brand_tracking",
            "external_crawl_default": "off",
        },
        "summary": {
            "enabled_feature_flags": sum(1 for row in flags if bool(row.get("enabled"))),
            "enabled_platforms": len(enabled_platforms),
            "enabled_high_cost_controls": sum(1 for row in high_cost_controls if row["enabled"]),
            "enabled_budget_usd": budget_total,
            "current_month_spent_usd": budget_spent,
            "budget_remaining_usd": max(budget_total - budget_spent, 0),
            "risk_level": "high" if any(row["enabled"] for row in high_cost_controls if row["requires_budget"]) else "controlled",
        },
        "high_cost_controls": high_cost_controls,
        "platform_controls": [
            {
                "platform": row.get("platform"),
                "crawl_enabled": bool(row.get("crawl_enabled")),
                "daily_account_limit": int(row.get("daily_account_limit") or 0),
                "posts_per_account": int(row.get("posts_per_account") or 0),
                "crawl_followers": bool(row.get("crawl_followers")),
                "crawl_audience_graph": bool(row.get("crawl_audience_graph")),
                "only_uncontacted_kols": bool(row.get("only_uncontacted_kols")),
                "monthly_budget_usd": float(row.get("monthly_budget_usd") or 0),
                "last_test_status": row.get("last_test_status") or "not_configured",
            }
            for row in platforms
        ],
        "youtube_kpi": {
            "reserved": True,
            "flag_enabled": bool((flag_map.get("youtube_kpi_reserved") or {}).get("enabled")),
            "platform_enabled": bool(youtube_row.get("crawl_enabled")),
            "last_test_status": youtube_row.get("last_test_status") or "not_configured",
            "source": "reserved_slot",
        },
        "budgets": budgets,
    }


def update_budget_settings(payload: dict[str, Any], *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_defaults()
    actor = resolve_staff_id(staff)
    conn = get_conn()
    now = _utcnow()
    updates = payload.get("budgets") if isinstance(payload.get("budgets"), list) else []
    for item in updates:
        if not isinstance(item, dict) or not str(item.get("budget_key") or "").strip():
            continue
        key = str(item.get("budget_key")).strip().lower()
        old = conn.execute("SELECT * FROM vkpi_budget_settings WHERE budget_key=?", (key,)).fetchone()
        old_data = dict(old) if old else {}

        def pick(name: str, default: Any = None) -> Any:
            return item[name] if name in item else old_data.get(name, default)

        metadata_json = _json(item.get("metadata")) if "metadata" in item else str(old_data.get("metadata_json") or "{}")

        conn.execute(
            """
            INSERT INTO vkpi_budget_settings
                (budget_key, monthly_limit_usd, current_month_spent, alert_threshold_pct, enabled, updated_by_staff_id, updated_at, metadata_json)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(budget_key) DO UPDATE SET
                monthly_limit_usd=excluded.monthly_limit_usd,
                current_month_spent=excluded.current_month_spent,
                alert_threshold_pct=excluded.alert_threshold_pct,
                enabled=excluded.enabled,
                updated_by_staff_id=excluded.updated_by_staff_id,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                key,
                float(pick("monthly_limit_usd", 0) or 0),
                float(pick("current_month_spent", 0) or 0),
                int(pick("alert_threshold_pct", 80) or 80),
                _bool(pick("enabled", False)),
                actor or None,
                now,
                metadata_json,
            ),
        )
        if actor:
            new_audit_value = {
                "monthly_limit_usd": float(pick("monthly_limit_usd", 0) or 0),
                "alert_threshold_pct": int(pick("alert_threshold_pct", 80) or 80),
                "enabled": _bool(pick("enabled", False)),
            }
            audit.log_settings_change(
                staff_id=actor,
                change_type="budget_setting",
                setting_key=key,
                old_value_redacted=str(dict(old) if old else {}),
                new_value_redacted=str(new_audit_value),
                metadata={"metadata": item.get("metadata") or {}, "budget_key": key},
            )
    conn.commit()
    return budget_settings()
