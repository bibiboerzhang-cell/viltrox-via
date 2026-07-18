"""AI/provider budget guard foundation for V-KPI automation."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime, table_exists
from app.domains.projects.workflow import staff_id as resolve_staff_id

logger = get_logger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        logger.warning("vkpi budget guard json parse failed: %s", exc)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed or None


def _clamp_ratio(value: Any, default: float) -> float:
    parsed = _float(value, default)
    return max(0.0, min(1.0, parsed))


def _normalize_scope(scope: str) -> str:
    return str(scope or "").strip().lower().replace(" ", "_")


def _is_single_call_ceiling_scope(scope: str) -> bool:
    """single_call[_*] scopes are per-request ceilings, not cumulative pools.

    They gate one call against cap_usd via estimated_cost only; current_spend is
    never accumulated for them and never used to hard-stop (that would flap as
    unrelated single calls pump the shared row past cap). Cumulative budgets live
    in monthly_total / provider:* / cron:* instead.
    """
    key = _normalize_scope(scope)
    return key == "single_call" or key.startswith("single_call_")


def _single_call_ceiling_allowed(cap: float, estimated_cost: float) -> bool:
    return float(cap or 0) <= 0 or float(estimated_cost or 0) <= float(cap or 0)


def _resolve_staff(value: Any) -> int | None:
    if isinstance(value, dict):
        return resolve_staff_id(value) or None
    return _int(value)


def _clean_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _clean_row(row: Any) -> dict[str, Any]:
    return {key: _clean_value(value) for key, value in dict(row).items()}


def ensure_budget_schema() -> None:
    """Create the local SQLite compatibility schema.

    Postgres uses migrations/057_vkpi_ai_cost_budget.sql through the normal
    migration sequence.
    """
    if is_postgres_runtime():
        return
    conn = get_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vkpi_ai_cost_ledger (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          cron_task TEXT,
          ai_provider TEXT,
          model_name TEXT,
          cost_usd REAL,
          tokens_in INTEGER,
          tokens_out INTEGER,
          kol_pool_id INTEGER,
          staff_id INTEGER,
          task_item_id INTEGER,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_vkpi_ai_cost_ledger_time
          ON vkpi_ai_cost_ledger (occurred_at);

        CREATE INDEX IF NOT EXISTS idx_vkpi_ai_cost_ledger_cron_time
          ON vkpi_ai_cost_ledger (cron_task, occurred_at);

        CREATE TABLE IF NOT EXISTS vkpi_provider_budget_caps (
          scope TEXT PRIMARY KEY,
          cap_usd REAL,
          current_spend REAL DEFAULT 0,
          warning_at REAL DEFAULT 0.80,
          hard_stop_at REAL DEFAULT 1.00,
          reset_at TEXT,
          fallback_action TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        INSERT OR IGNORE INTO vkpi_provider_budget_caps
            (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
        VALUES
            ('single_call', 0.50, 0, 0.80, 1.00, NULL, 'fallback_to_rule_v0', '{"seeded_by":"budget_guard_sqlite","tier":"hard_stop"}'),
            ('cron:p4_evidence_summary', 10.00, 0, 0.80, 1.00, NULL, 'fallback_to_evidence_only', '{"seeded_by":"budget_guard_sqlite","tier":"cron","package":"P4"}'),
            ('cron:p4_gemini_single_kol', 3.00, 0, 0.80, 1.00, NULL, 'fallback_to_preflight_only', '{"seeded_by":"budget_guard_sqlite","tier":"cron","package":"P4","provider":"gemini"}'),
            ('cron:market_provider_smoke', 1.00, 0, 0.80, 1.00, NULL, 'fallback_to_preflight_only', '{"seeded_by":"budget_guard_sqlite","tier":"cron","package":"market_intelligence","provider":"llm"}');
        """
    )
    conn.commit()


def _budget_payload(row: dict[str, Any], *, estimated_cost: float = 0.0) -> dict[str, Any]:
    cap = _float(row.get("cap_usd"))
    current = _float(row.get("current_spend"))
    warning_at = _clamp_ratio(row.get("warning_at"), 0.8)
    hard_stop_at = _clamp_ratio(row.get("hard_stop_at"), 1.0)
    projected = current + max(0.0, float(estimated_cost or 0))
    usage_ratio = (current / cap) if cap > 0 else 0.0
    projected_ratio = (projected / cap) if cap > 0 else 0.0
    hard_stopped = cap > 0 and projected_ratio >= hard_stop_at
    warning = cap > 0 and projected_ratio >= warning_at
    return {
        "scope": row.get("scope") or "",
        "cap_usd": cap,
        "current_spend": current,
        "estimated_cost_usd": max(0.0, float(estimated_cost or 0)),
        "projected_spend_usd": projected,
        "remaining_usd": max(cap - current, 0.0) if cap > 0 else None,
        "projected_remaining_usd": max(cap - projected, 0.0) if cap > 0 else None,
        "usage_ratio": usage_ratio,
        "projected_usage_ratio": projected_ratio,
        "warning_at": warning_at,
        "hard_stop_at": hard_stop_at,
        "warning": warning,
        "hard_stopped": hard_stopped,
        "allowed": not hard_stopped,
        "reset_at": row.get("reset_at"),
        "fallback_action": row.get("fallback_action") or "",
        "metadata": _load_json(row.get("metadata_json")),
    }


# 设计为日窗(而非终身池)的非 cron scope:report_analysis 是 $3/日 的按需闸。
_DAILY_WINDOW_SCOPES = ("dashboard:report_analysis",)


def _is_daily_window_scope(scope_key: str) -> bool:
    return scope_key.startswith("cron:") or scope_key in _DAILY_WINDOW_SCOPES


def _is_monthly_window_scope(scope_key: str) -> bool:
    return scope_key == "monthly_total" or scope_key.startswith("provider:")


def _maybe_roll_budget_window(conn: Any, row: dict[str, Any]) -> dict[str, Any]:
    """窗口池惰性滚动(不依赖调度器;非窗口 scope 原样返回)。

    日窗(cron:*、dashboard:report_analysis):reset_at 过期(或从未设置)即清零并
    推进到下一个 UTC 零点。根因修复:官号日报『$4/日』被实现成『$4/终身』——39 笔后
    永久 hard stop(06-19 停摆悬案);全库原本无任何重置写方。

    月窗(monthly_total、provider:*):同样此前无任何重置写方=『$X/月』实为『$X/终身』。
    reset_at 过期即清零并推进到下月 1 日(UTC);reset_at 从未设置时只补窗口锚点、
    不清零 —— 既有累计发生在锚点之前,凭空清零等于部署瞬间放开一整月预算,也会
    回归既有台账断言(record→读回 current_spend 累计口径)。"""
    scope_key = str(row.get("scope") or "")
    daily = _is_daily_window_scope(scope_key)
    monthly = _is_monthly_window_scope(scope_key)
    if not daily and not monthly:
        return row
    now = datetime.now(timezone.utc)
    reset_raw = str(row.get("reset_at") or "").strip()
    due = True
    if reset_raw:
        try:
            due = now >= datetime.fromisoformat(reset_raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            due = True
    if not due:
        return row
    if daily:
        next_reset = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    elif now.month == 12:
        next_reset = f"{now.year + 1:04d}-01-01T00:00:00Z"
    else:
        next_reset = f"{now.year:04d}-{now.month + 1:02d}-01T00:00:00Z"
    zero_spend = daily or bool(reset_raw)
    try:
        if zero_spend:
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET current_spend = 0, reset_at = ? WHERE scope = ?",
                (next_reset, scope_key),
            )
        else:
            conn.execute(
                "UPDATE vkpi_provider_budget_caps SET reset_at = ? WHERE scope = ?",
                (next_reset, scope_key),
            )
        conn.commit()
        row = dict(row)
        if zero_spend:
            row["current_spend"] = 0
        row["reset_at"] = next_reset
    except Exception:
        logger.warning("budget window roll failed scope=%s", scope_key, exc_info=True)
    return row


def check_budget(scope: str, estimated_cost: float, *, require_configured: bool = False) -> bool:
    """Return whether a call can proceed for the given budget scope."""
    scope_key = _normalize_scope(scope)
    if not scope_key:
        return not require_configured
    ensure_budget_schema()
    row = get_conn().execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", (scope_key,)).fetchone()
    if not row:
        return not require_configured
    payload = _budget_payload(_maybe_roll_budget_window(get_conn(), _clean_row(row)), estimated_cost=float(estimated_cost or 0))
    if _is_single_call_ceiling_scope(scope_key):
        # Per-request ceiling: compare estimated_cost to cap, ignore current_spend.
        return _single_call_ceiling_allowed(_float(payload.get("cap_usd")), float(estimated_cost or 0))
    return bool(payload.get("allowed"))


def check_budget_scopes(
    scopes: list[str] | tuple[str, ...],
    estimated_cost: float,
    *,
    require_configured: bool = True,
) -> dict[str, Any]:
    """Return a read-only hard-gate plan across multiple budget scopes."""

    ensure_budget_schema()
    clean_scopes = [scope for scope in dict.fromkeys(_normalize_scope(scope) for scope in (scopes or [])) if scope]
    checks: list[dict[str, Any]] = []
    allowed = True
    for scope in clean_scopes:
        status = get_budget_status(scope, estimated_cost=estimated_cost)
        configured = bool(status.get("configured", False))
        scope_allowed = bool(status.get("allowed", True))
        if _is_single_call_ceiling_scope(scope) and configured:
            # single_call[_*] is a per-request ceiling, not a cumulative budget pool.
            cap = _float(status.get("cap_usd"))
            scope_allowed = _single_call_ceiling_allowed(cap, float(estimated_cost or 0))
            status["allowed"] = scope_allowed
            status["hard_stopped"] = not scope_allowed
        if require_configured and not configured:
            scope_allowed = False
        check = {
            **status,
            "scope": scope,
            "configured": configured,
            "allowed": scope_allowed,
            "required": bool(require_configured),
        }
        checks.append(check)
        if not scope_allowed:
            allowed = False
    if require_configured and not clean_scopes:
        allowed = False
    return {
        "allowed": allowed,
        "estimated_cost_usd": max(0.0, float(estimated_cost or 0)),
        "require_configured": bool(require_configured),
        "scopes": clean_scopes,
        "checks": checks,
    }


def get_budget_status(scope: str | None = None, *, estimated_cost: float = 0.0) -> dict[str, Any]:
    ensure_budget_schema()
    conn = get_conn()
    scope_key = _normalize_scope(scope or "")
    if scope_key:
        row = conn.execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", (scope_key,)).fetchone()
        if not row:
            return {
                "scope": scope_key,
                "configured": False,
                "allowed": True,
                "estimated_cost_usd": max(0.0, float(estimated_cost or 0)),
            }
        payload = _budget_payload(_maybe_roll_budget_window(conn, _clean_row(row)), estimated_cost=float(estimated_cost or 0))
        if _is_single_call_ceiling_scope(scope_key):
            # Per-request ceiling: allowed reflects estimated_cost<=cap, not current_spend.
            ceiling_allowed = _single_call_ceiling_allowed(_float(payload.get("cap_usd")), float(estimated_cost or 0))
            payload["allowed"] = ceiling_allowed
            payload["hard_stopped"] = not ceiling_allowed
        return {"configured": True, **payload}

    rows = conn.execute("SELECT * FROM vkpi_provider_budget_caps ORDER BY scope").fetchall()
    budgets = [_budget_payload(_clean_row(row), estimated_cost=0.0) for row in rows]
    return {
        "budgets": budgets,
        "summary": {
            "scopes": len(budgets),
            "cap_usd": sum(float(row.get("cap_usd") or 0) for row in budgets),
            "current_spend_usd": sum(float(row.get("current_spend") or 0) for row in budgets),
            "warnings": sum(1 for row in budgets if row.get("warning")),
            "hard_stopped": sum(1 for row in budgets if row.get("hard_stopped")),
        },
    }


def update_budget(scope: str, payload: dict[str, Any] | None = None, *, staff: dict[str, Any] | None = None) -> dict[str, Any]:
    del staff
    scope_key = _normalize_scope(scope)
    if not scope_key:
        raise ValueError("scope required")

    ensure_budget_schema()
    payload = payload or {}
    conn = get_conn()
    old = conn.execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", (scope_key,)).fetchone()
    old_data = _clean_row(old) if old else {}

    def pick(name: str, default: Any = None) -> Any:
        return payload[name] if name in payload else old_data.get(name, default)

    cap_usd = max(0.0, _float(pick("cap_usd", 0)))
    current_spend = max(0.0, _float(pick("current_spend", 0)))
    warning_at = _clamp_ratio(pick("warning_at", 0.8), 0.8)
    hard_stop_at = max(warning_at, _clamp_ratio(pick("hard_stop_at", 1.0), 1.0))
    reset_at = pick("reset_at")
    fallback_action = str(pick("fallback_action", "") or "")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else _load_json(old_data.get("metadata_json"))

    # 2026-07-18 竞态修:payload 未显式带 current_spend 时(管理员改 cap 的
    # 常态),DO UPDATE 绝不能用 SELECT 读到的旧值回写——否则会清掉 SELECT 与
    # upsert 之间并发 record_cost 的原子累加,花费回退→预算闸失效超支。
    # 只有 payload 显式给 current_spend(重置场景)才覆写该列。
    spend_explicit = 1 if "current_spend" in payload else 0
    conn.execute(
        """
        INSERT INTO vkpi_provider_budget_caps
            (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(scope) DO UPDATE SET
            cap_usd=excluded.cap_usd,
            current_spend=CASE WHEN ?=1 THEN excluded.current_spend
                               ELSE vkpi_provider_budget_caps.current_spend END,
            warning_at=excluded.warning_at,
            hard_stop_at=excluded.hard_stop_at,
            reset_at=excluded.reset_at,
            fallback_action=excluded.fallback_action,
            metadata_json=excluded.metadata_json
        """,
        (scope_key, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, _json(metadata), spend_explicit),
    )
    conn.commit()
    return get_budget_status(scope_key)


def record_cost(
    *,
    scope: str = "",
    cron_task: str = "",
    ai_provider: str = "",
    model_name: str = "",
    cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    kol_pool_id: int | None = None,
    staff_id: int | None = None,
    task_item_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    triggered_by: Any = None,
    extra_scopes: list[str] | tuple[str, ...] | None = None,
    update_budget_scopes: bool = True,
) -> dict[str, Any]:
    ensure_budget_schema()
    scope_key = _normalize_scope(scope)
    actor_staff_id = staff_id or _resolve_staff(triggered_by)
    cost = max(0.0, float(cost_usd or 0))
    provider = str(ai_provider or "unknown").strip().lower() or "unknown"
    # 供应商标签归一:llm_gateway 侧叫 google、视频侧叫 gemini,台账统一 gemini 防汇总分裂。
    if provider == "google":
        provider = "gemini"
    now = _utcnow()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO vkpi_ai_cost_ledger
            (cron_task, ai_provider, model_name, cost_usd, tokens_in, tokens_out,
             kol_pool_id, staff_id, task_item_id, metadata_json, occurred_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            str(cron_task or scope_key or ""),
            provider,
            str(model_name or ""),
            cost,
            int(tokens_in or 0),
            int(tokens_out or 0),
            _int(kol_pool_id),
            _int(actor_staff_id),
            _int(task_item_id),
            _json({**(metadata or {}), "scope": scope_key} if scope_key else (metadata or {})),
            now,
        ),
    )
    requested_scopes = [scope for scope in [scope_key, *(_normalize_scope(scope) for scope in (extra_scopes or []))] if scope]
    # single_call[_*] are per-request ceilings: never accumulate current_spend on
    # them or the shared row creeps past cap and hard-stops every later call
    # (the flapping bug). Cumulative accounting stays on monthly_total/provider:*/cron:*.
    scopes_to_update = (
        [scope for scope in dict.fromkeys(requested_scopes) if not _is_single_call_ceiling_scope(scope)]
        if update_budget_scopes
        else []
    )
    for budget_scope in scopes_to_update:
        conn.execute(
            """
            UPDATE vkpi_provider_budget_caps
            SET current_spend=COALESCE(current_spend, 0) + ?
            WHERE scope=?
            """,
            (cost, budget_scope),
        )
    conn.commit()
    return {
        "recorded": True,
        "scope": scope_key,
        "scopes_updated": list(dict.fromkeys(scopes_to_update)),
        "ai_provider": provider,
        "model_name": str(model_name or ""),
        "cost_usd": cost,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "occurred_at": now,
    }


def usage_by_provider(limit: int = 50) -> dict[str, Any]:
    ensure_budget_schema()
    rows = get_conn().execute(
        """
        SELECT COALESCE(NULLIF(ai_provider, ''), 'unknown') AS ai_provider,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               COALESCE(SUM(tokens_in), 0) AS tokens_in,
               COALESCE(SUM(tokens_out), 0) AS tokens_out,
               MAX(occurred_at) AS last_seen_at
        FROM vkpi_ai_cost_ledger
        GROUP BY COALESCE(NULLIF(ai_provider, ''), 'unknown')
        ORDER BY cost_usd DESC, calls DESC
        LIMIT ?
        """,
        (max(1, min(200, int(limit or 50))),),
    ).fetchall()
    return {"rows": [_clean_row(row) for row in rows]}


def usage_by_cron(limit: int = 50) -> dict[str, Any]:
    ensure_budget_schema()
    rows = get_conn().execute(
        """
        SELECT COALESCE(NULLIF(cron_task, ''), 'manual') AS cron_task,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd,
               COALESCE(SUM(tokens_in), 0) AS tokens_in,
               COALESCE(SUM(tokens_out), 0) AS tokens_out,
               MAX(occurred_at) AS last_seen_at
        FROM vkpi_ai_cost_ledger
        GROUP BY COALESCE(NULLIF(cron_task, ''), 'manual')
        ORDER BY cost_usd DESC, calls DESC
        LIMIT ?
        """,
        (max(1, min(200, int(limit or 50))),),
    ).fetchall()
    return {"rows": [_clean_row(row) for row in rows]}


# ──────────────────────────────────────────────
# C5 成本记账收口(2026-07-03)
# 背景:Apify 单号 $199 只剩 $3.4,内部记账只见 $26/$195 = 巨大盲区。
# 根因:run.usageTotalUsd 只覆盖「平台用量」(compute/代理),pay-per-result /
# 付费 actor 的 actor 费不在里面 → 散点埋点全部系统性低记。
# 本段只做三件事:统一记账口(幂等)/ 总览聚合 / 每日快照。
# 红线:只记账,绝不预检拦截、绝不改 actor 调用行为、绝不碰预算闸逻辑。
# ──────────────────────────────────────────────

_APIFY_PRICING_ENV = "APIFY_ACTOR_PRICING_JSON"

# 内部估算价目表(USD)——只为「记账可见」服务的保守口径,非权威账单(权威以 Apify console 为准)。
# key=actor_id(斜杠形式,小写);per_1000_items=按结果计费单价,per_run=每次固定费。
# env APIFY_ACTOR_PRICING_JSON 可覆盖/扩充,如:
#   {"clockworks/tiktok-scraper": {"per_1000_items": 4.0}, "some/actor": 2.5}(裸数字=per_1000_items)。
# 2026-07-06 全表按线上 run pricingInfo 实测校准(旧值高估 2-6.3x);
# 该表只在 usage 未结算时作先记估算,结算现值由 reconcile_apify_costs 覆盖。
_APIFY_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "apify/instagram-scraper": {"per_1000_items": 1.9},
    "apify/instagram-comment-scraper": {"per_1000_items": 2.1},
    "apify/instagram-profile-scraper": {"per_1000_items": 2.0},
    "apify/instagram-hashtag-scraper": {"per_1000_items": 2.1},
    "streamers/youtube-scraper": {"per_1000_items": 2.6},
    "streamers/youtube-channel-scraper": {"per_1000_items": 0.8},
    "streamers/youtube-comments-scraper": {"per_1000_items": 1.2},
    # tiktok-scraper 2.3/1000 结果 + 0.8/1000 视频下载,合并口径 3.1
    "clockworks/tiktok-scraper": {"per_1000_items": 3.1},
    "clockworks/free-tiktok-scraper": {"per_1000_items": 3.1},
    "clockworks/tiktok-comments-scraper": {"per_1000_items": 0.75},
    "apify/facebook-posts-scraper": {"per_run": 0.001, "per_1000_items": 2.5},
    "apify/facebook-pages-scraper": {"per_1000_items": 6.5},
    "trudax/reddit-scraper-lite": {"per_run": 0.02, "per_1000_items": 3.6},
    # X 监听(迸发⑤开闸刀):2026-07-16 实测结算 100 条=$0.256 → $2.56/1k
    # (设计单标价 $0.40/1k 与实测差 6x,按实测口径记;缺行会被预检 estimate_unavailable 整路挡死)。
    "apidojo/twitter-scraper-lite": {"per_1000_items": 2.6},
    "apidojo/tweet-scraper": {"per_1000_items": 2.6},
    # B&H 付费 actor:实测 $0.09/start + $0.005/item ≈ $0.29-0.34/run
    "powerai/bhphotovideo-product-search-scraper": {"per_run": 0.32},
    "powerai/bhphotovideo-product-reviews-scraper": {"per_run": 0.32},
}

# 快照键(persistent_cache,003 baseline 表;health_sentinel 同款模式,零新表零迁移)。
_COST_SNAPSHOT_LATEST_KEY = "vkpi:cost_snapshot:latest"
_COST_SNAPSHOT_DAY_PREFIX = "vkpi:cost_snapshot:day:"
_COST_SNAPSHOT_KEEP_DAYS = 62


def _apify_actor_pricing(actor_id: str) -> dict[str, float]:
    """查内部估算价目表(env 覆盖优先);查不到返回 {}(= 只记 usageTotalUsd 口径)。"""
    key = str(actor_id or "").strip().replace("~", "/").lower()
    if not key:
        return {}
    table: dict[str, dict[str, float]] = dict(_APIFY_DEFAULT_PRICING)
    raw = os.getenv(_APIFY_PRICING_ENV, "").strip()
    if raw:
        try:
            overrides = json.loads(raw)
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    kk = str(k).strip().replace("~", "/").lower()
                    if isinstance(v, dict):
                        table[kk] = {str(sk): _float(sv) for sk, sv in v.items()}
                    else:
                        table[kk] = {"per_1000_items": _float(v)}
        except Exception:
            logger.warning("apify pricing env parse failed", exc_info=True)
    return table.get(key) or {}


def _apify_run_already_recorded(conn: Any, run_id: str) -> bool:
    """幂等去重:同 apify_run_id 是否已有记账行(只回看近 14 天,吃 occurred_at 索引)。

    metadata_json 里旧散点埋点也写 "apify_run_id" 同款键 → 新旧埋点互不双记。
    LIKE 模式走绑定参数(health_sentinel 同款先例,compat 层安全)。
    """
    clean = str(run_id or "").strip()
    if not clean or not clean.replace("-", "").isalnum():
        # run_id 含 LIKE 通配符(%/_)等异常字符 → 放弃去重(宁可重记一笔,不做半吊子匹配)。
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pattern = '%"apify_run_id": "' + clean + '"%'
    try:
        row = conn.execute(
            """
            SELECT id FROM vkpi_ai_cost_ledger
            WHERE ai_provider='apify' AND occurred_at >= ? AND metadata_json LIKE ?
            LIMIT 1
            """,
            (cutoff, pattern),
        ).fetchone()
    except Exception:
        logger.warning("apify run dedupe lookup failed", exc_info=True)
        return False
    return row is not None


def record_apify_run(
    run: Any = None,
    *,
    actor_id: str = "",
    platform: str = "",
    operation: str = "",
    source: str = "",
    dataset_item_count: int | None = None,
) -> dict[str, Any]:
    """C5 统一记账口:所有 Apify actor run 结束后走这里落 vkpi_ai_cost_ledger。

    - 幂等:同 apify_run_id 近 14 天只记一笔(散点埋点/重试不会双记);
    - 成本口径:精确的 run.usageTotalUsd(平台用量)+ 内部价目表估算的 actor 费
      (pay-per-result / 付费 actor 费不在 usageTotalUsd 里 → metadata.estimated=true);
    - run-sync HTTP 调用拿不到 run 对象时允许 run=None(pricing_basis=no_run_object);
    - 只记账不拦截:绝不做预算预检、绝不改调用行为;任何异常吞掉返回 recorded=False。
    """
    try:
        run_obj = run if isinstance(run, dict) else {}
        reservation_key = str(run_obj.get("_vkpi_budget_reservation_key") or "").strip()
        actor_key = str(actor_id or "").strip().replace("~", "/")
        run_id = str(run_obj.get("id") or "").strip()
        run_status = str(run_obj.get("status") or "")
        usage = run_obj.get("usageTotalUsd")
        if usage is None and isinstance(run_obj.get("usageUsd"), dict):
            usage = sum(
                float(value or 0)
                for value in run_obj["usageUsd"].values()
                if isinstance(value, (int, float))
            )
        try:
            usage_usd: float | None = float(usage) if usage is not None else None
        except (TypeError, ValueError):
            usage_usd = None
        items: int | None = None
        if dataset_item_count is not None:
            try:
                items = max(0, int(dataset_item_count))
            except (TypeError, ValueError):
                items = None
        pricing = _apify_actor_pricing(actor_key)
        fee = 0.0
        if pricing:
            fee += max(0.0, _float(pricing.get("per_run")))
            per_k = _float(pricing.get("per_1000_items"))
            if per_k > 0 and items:
                fee += per_k * items / 1000.0
        # PPE 计费结算后 usageTotalUsd 已含事件费(实测 IG 50 条结算 $0.0950=50×$0.0019 分毫不差)——
        # usage 有值时绝不再叠价目表费(旧口径会双记 ~2x);usage 缺/为 0(.call() 返回瞬间事件费未结算)
        # 才用表估先记,留待 reconcile_apify_costs 用 API 结算现值覆盖。
        if usage_usd is not None and usage_usd > 0:
            cost_usd = usage_usd
            pricing_basis = "usage_settled"
        elif pricing:
            cost_usd = max(0.0, fee)
            pricing_basis = "priced_table_estimate"
        elif usage_usd is not None:
            cost_usd = 0.0
            pricing_basis = "usage_zero_pending"
        else:
            cost_usd = 0.0
            pricing_basis = "no_run_object"
        estimated = pricing_basis != "usage_settled"
        ensure_budget_schema()
        reservation_settlement: dict[str, Any] = {}
        if reservation_key:
            from app.platform.apify_budget import settle_apify_reservation

            reservation_settlement = settle_apify_reservation(reservation_key, cost_usd)
        if run_id and _apify_run_already_recorded(get_conn(), run_id):
            return {"recorded": False, "reason": "duplicate_run", "apify_run_id": run_id}
        return record_cost(
            scope="provider:apify",
            ai_provider="apify",
            model_name=actor_key,
            cost_usd=cost_usd,
            extra_scopes=["monthly_total"],
            # Atomic reservation settlement already moved actual spend into
            # cumulative scopes.  Legacy unreserved runs keep old accounting.
            update_budget_scopes=not bool(reservation_key),
            metadata={
                "platform": str(platform or ""),
                "actor_id": actor_key,
                "operation": str(operation or ""),
                "source": str(source or ""),
                "apify_run_id": run_id,
                "run_status": run_status,
                "usage_total_usd": usage_usd if usage_usd is not None else 0.0,
                "estimated_fee_usd": round(max(0.0, fee), 6),
                "dataset_item_count": items,
                "estimated": estimated,
                "pricing_basis": pricing_basis,
                "unified_entry": True,
                "budget_reservation_key": reservation_key,
                "budget_reservation_settlement": reservation_settlement,
            },
        )
    except Exception:
        # 记账失败绝不打断业务调用(与旧散点埋点同约定)。
        logger.warning("record_apify_run failed (accounting must never break scraping)", exc_info=True)
        return {"recorded": False, "reason": "error"}


def reconcile_apify_costs(hours: int = 48, max_rows: int = 300) -> dict[str, Any]:
    """Apify 记账对账:PPE 事件费在 .call() 返回瞬间常未结算(实测低记 ~6x),
    用 API 结算现值覆盖近 N 小时的 apify 台账行,并把差额补进预算 scope 的 current_spend。
    只对账不拦截;单行失败跳过不断批;无 token/网络失败整体温和返回。"""
    import os as _os

    token = str(_os.environ.get("APIFY_TOKEN") or "").strip()
    if not token:
        return {"reconciled": 0, "checked": 0, "reason": "no_token"}
    ensure_budget_schema()
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT id, cost_usd, metadata_json FROM vkpi_ai_cost_ledger
        WHERE ai_provider = 'apify' AND occurred_at >= ?
        ORDER BY id DESC LIMIT ?
        """,
        (cutoff, max(1, int(max_rows))),
    ).fetchall()
    import httpx

    checked = 0
    reconciled = 0
    delta_total = 0.0
    with httpx.Client(timeout=15.0) as client:
        for row in rows:
            data = dict(row)
            try:
                meta = json.loads(data.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                continue
            run_id = str(meta.get("apify_run_id") or "").strip()
            if not run_id or meta.get("reconciled"):
                continue
            checked += 1
            try:
                resp = client.get(
                    f"https://api.apify.com/v2/actor-runs/{run_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    # 2026-07-18 审计修:对账失败必须可见,否则结算漂移永不收敛。
                    logger.warning("apify reconcile non-200 run_id=%s status=%s", run_id, resp.status_code)
                    continue
                settled = float(((resp.json().get("data") or {}).get("usageTotalUsd")) or 0.0)
            except Exception:
                logger.warning("apify reconcile fetch failed run_id=%s", run_id, exc_info=True)
                continue
            recorded = float(data.get("cost_usd") or 0.0)
            delta = settled - recorded
            if settled <= 0 or abs(delta) < 0.001:
                continue
            meta.update({"reconciled": True, "settled_usd": round(settled, 6), "recorded_before_reconcile": round(recorded, 6)})
            conn.execute(
                "UPDATE vkpi_ai_cost_ledger SET cost_usd = ?, metadata_json = ? WHERE id = ?",
                (round(settled, 6), json.dumps(meta, ensure_ascii=False), int(data["id"])),
            )
            for scope_key in ("provider:apify", "monthly_total"):
                conn.execute(
                    "UPDATE vkpi_provider_budget_caps SET current_spend = current_spend + ? WHERE scope = ?",
                    (round(delta, 6), scope_key),
                )
            reconciled += 1
            delta_total += delta
    conn.commit()
    if reconciled:
        logger.info("apify cost reconcile | rows=%s delta_usd=%.4f", reconciled, delta_total)
    return {"reconciled": reconciled, "checked": checked, "delta_usd": round(delta_total, 4)}


def _cost_bucket_rows(conn: Any, since_iso: str, until_iso: str | None = None) -> dict[str, Any]:
    """按 apify / llm 两桶聚合一个时间窗(llm 桶=非 apify 的全部 provider)。"""
    clause = "occurred_at >= ?"
    params: list[Any] = [since_iso]
    if until_iso:
        clause += " AND occurred_at < ?"
        params.append(until_iso)
    rows = conn.execute(
        f"""
        SELECT CASE WHEN COALESCE(ai_provider, '')='apify' THEN 'apify' ELSE 'llm' END AS bucket,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd
        FROM vkpi_ai_cost_ledger
        WHERE {clause}
        GROUP BY CASE WHEN COALESCE(ai_provider, '')='apify' THEN 'apify' ELSE 'llm' END
        """,
        tuple(params),
    ).fetchall()
    out = {"apify_usd": 0.0, "apify_calls": 0, "llm_usd": 0.0, "llm_calls": 0}
    for raw in rows:
        row = _clean_row(raw)
        bucket = str(row.get("bucket") or "")
        if bucket == "apify":
            out["apify_usd"] = round(_float(row.get("cost_usd")), 4)
            out["apify_calls"] = int(row.get("calls") or 0)
        else:
            out["llm_usd"] = round(_float(row.get("cost_usd")), 4)
            out["llm_calls"] = int(row.get("calls") or 0)
    out["total_usd"] = round(out["apify_usd"] + out["llm_usd"], 4)
    return out


def _top_apify_actors(conn: Any, since_iso: str, until_iso: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    clause = "ai_provider='apify' AND occurred_at >= ?"
    params: list[Any] = [since_iso]
    if until_iso:
        clause += " AND occurred_at < ?"
        params.append(until_iso)
    params.append(max(1, min(20, int(limit or 5))))
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(model_name, ''), 'unknown') AS actor_id,
               COUNT(*) AS runs,
               COALESCE(SUM(cost_usd), 0) AS cost_usd
        FROM vkpi_ai_cost_ledger
        WHERE {clause}
        GROUP BY COALESCE(NULLIF(model_name, ''), 'unknown')
        ORDER BY cost_usd DESC, runs DESC
        LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    return [
        {
            "actor_id": str(row.get("actor_id") or "unknown"),
            "runs": int(row.get("runs") or 0),
            "cost_usd": round(_float(row.get("cost_usd")), 4),
        }
        for row in (_clean_row(r) for r in rows)
    ]


def _apify_coverage(conn: Any, since_iso: str, until_iso: str | None = None) -> dict[str, Any]:
    """记账覆盖口径:总 run 数 / 收口(unified)笔数 / 估算笔数 / 零成本笔数。

    Apify console 无法程序化比对 → 覆盖率只能是内部口径:统一记账口经手的比例,
    以及仍然记 0 成本(既无 usage 又无价目表)的盲区笔数,如实标注。
    """
    clause = "ai_provider='apify' AND occurred_at >= ?"
    params: list[Any] = [since_iso]
    if until_iso:
        clause += " AND occurred_at < ?"
        params.append(until_iso)

    def _count(extra_sql: str, extra_params: tuple = ()) -> int:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM vkpi_ai_cost_ledger WHERE {clause}{extra_sql}",
            tuple(params) + extra_params,
        ).fetchone()
        return int((dict(row) if row else {}).get("n") or 0)

    total = _count("")
    unified = _count(" AND metadata_json LIKE ?", ('%"unified_entry": true%',))
    estimated = _count(" AND metadata_json LIKE ?", ('%"estimated": true%',))
    zero_cost = _count(" AND COALESCE(cost_usd, 0) <= 0")
    return {
        "apify_runs": total,
        "unified_entries": unified,
        "estimated_entries": estimated,
        "zero_cost_entries": zero_cost,
        "unified_ratio": round(unified / total, 4) if total else None,
    }


_COST_NOTE = (
    "内部记账口径:精确部分=Apify usageTotalUsd(平台用量);付费 actor 费按内部价目表估算"
    "(estimated 标记)。Apify console 无法程序化比对,权威账单以 console 为准。"
)


def cost_overview(tz_offset_minutes: int = 0) -> dict[str, Any]:
    """成本记账总览(设置页成本卡):今日/本月 Apify+LLM 消耗、top actor、覆盖口径。只读。

    「今日/本月」按观看者本地零点划界(时间机制红线:显示按浏览器时区)——
    tz_offset_minutes 为浏览器相对 UTC 的偏移(东八区 +480,美东夏令 -240),
    缺省 0 保持旧 UTC 口径。此前固定 UTC 零点导致美东晚间「今日」被清零。
    """
    ensure_budget_schema()
    conn = get_conn()
    offset = max(-840, min(840, int(tz_offset_minutes or 0)))
    now_local = datetime.now(timezone.utc) + timedelta(minutes=offset)
    day_start = (
        now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(minutes=offset)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    month_start = (
        now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        - timedelta(minutes=offset)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    overview: dict[str, Any] = {
        "generated_at": _utcnow(),
        "today": _cost_bucket_rows(conn, day_start),
        "month": _cost_bucket_rows(conn, month_start),
        "top_actors_today": _top_apify_actors(conn, day_start),
        "top_actors_month": _top_apify_actors(conn, month_start),
        "coverage": _apify_coverage(conn, month_start),
        "note": _COST_NOTE,
    }
    try:
        overview["budgets"] = {
            "provider_apify": get_budget_status("provider:apify"),
            "monthly_total": get_budget_status("monthly_total"),
        }
    except Exception:
        logger.warning("cost_overview budget status failed", exc_info=True)
    return overview


def snapshot_daily_costs(day: str | None = None) -> dict[str, Any]:
    """每日成本快照:把指定 UTC 日(默认昨日)的 vkpi_ai_cost_ledger 按 provider/actor
    聚合写入 persistent_cache(day 键 62 天过期 + latest 键;同日重跑 delete+insert 幂等)。

    复用 003 baseline 的 persistent_cache(health_sentinel 同款模式)→ 零新表零迁移。
    只读账本 + 写缓存;绝不改预算闸/调用行为。
    """
    ensure_budget_schema()
    conn = get_conn()
    now = datetime.now(timezone.utc)
    if day:
        target = str(day).strip()[:10]
        target_dt = datetime.strptime(target, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        target_dt = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        target = target_dt.strftime("%Y-%m-%d")
    since_iso = target + "T00:00:00Z"
    until_iso = (target_dt + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

    provider_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(ai_provider, ''), 'unknown') AS ai_provider,
               COUNT(*) AS calls,
               COALESCE(SUM(cost_usd), 0) AS cost_usd
        FROM vkpi_ai_cost_ledger
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY COALESCE(NULLIF(ai_provider, ''), 'unknown')
        ORDER BY cost_usd DESC, calls DESC
        """,
        (since_iso, until_iso),
    ).fetchall()
    providers = [
        {
            "ai_provider": str(row.get("ai_provider") or "unknown"),
            "calls": int(row.get("calls") or 0),
            "cost_usd": round(_float(row.get("cost_usd")), 4),
        }
        for row in (_clean_row(r) for r in provider_rows)
    ]
    payload: dict[str, Any] = {
        "date": target,
        "generated_at": _utcnow(),
        "providers": providers,
        "apify_actors": _top_apify_actors(conn, since_iso, until_iso, limit=20),
        "totals": _cost_bucket_rows(conn, since_iso, until_iso),
        "coverage": _apify_coverage(conn, since_iso, until_iso),
        "note": _COST_NOTE,
    }
    if not table_exists("persistent_cache"):
        logger.warning("cost snapshot: persistent_cache table missing, snapshot not persisted")
        return {**payload, "persisted": False}
    try:
        expires = (now + timedelta(days=_COST_SNAPSHOT_KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = json.dumps(payload, ensure_ascii=False, default=str)
        day_key = _COST_SNAPSHOT_DAY_PREFIX + target
        for cache_key in (day_key, _COST_SNAPSHOT_LATEST_KEY):
            conn.execute("DELETE FROM persistent_cache WHERE cache_key=?", (cache_key,))
            conn.execute(
                "INSERT INTO persistent_cache (cache_key, value_json, expires_at, created_at) VALUES (?,?,?,?)",
                (cache_key, text, expires, _utcnow()),
            )
        conn.commit()
    except Exception:
        logger.warning("cost snapshot persist failed", exc_info=True)
        return {**payload, "persisted": False}
    return {**payload, "persisted": True}
