"""AI/provider budget guard foundation for V-KPI automation."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn, is_postgres_runtime
from app.services.vkpi.workflow import staff_id as resolve_staff_id

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


def check_budget(scope: str, estimated_cost: float, *, require_configured: bool = False) -> bool:
    """Return whether a call can proceed for the given budget scope."""
    scope_key = _normalize_scope(scope)
    if not scope_key:
        return not require_configured
    ensure_budget_schema()
    row = get_conn().execute("SELECT * FROM vkpi_provider_budget_caps WHERE scope=?", (scope_key,)).fetchone()
    if not row:
        return not require_configured
    return bool(_budget_payload(_clean_row(row), estimated_cost=float(estimated_cost or 0)).get("allowed"))


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
        return {"configured": True, **_budget_payload(_clean_row(row), estimated_cost=float(estimated_cost or 0))}

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

    conn.execute(
        """
        INSERT INTO vkpi_provider_budget_caps
            (scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, metadata_json)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(scope) DO UPDATE SET
            cap_usd=excluded.cap_usd,
            current_spend=excluded.current_spend,
            warning_at=excluded.warning_at,
            hard_stop_at=excluded.hard_stop_at,
            reset_at=excluded.reset_at,
            fallback_action=excluded.fallback_action,
            metadata_json=excluded.metadata_json
        """,
        (scope_key, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action, _json(metadata)),
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
) -> dict[str, Any]:
    ensure_budget_schema()
    scope_key = _normalize_scope(scope)
    actor_staff_id = staff_id or _resolve_staff(triggered_by)
    cost = max(0.0, float(cost_usd or 0))
    provider = str(ai_provider or "unknown").strip().lower() or "unknown"
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
    scopes_to_update = [scope for scope in [scope_key, *(_normalize_scope(scope) for scope in (extra_scopes or []))] if scope]
    for budget_scope in dict.fromkeys(scopes_to_update):
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
