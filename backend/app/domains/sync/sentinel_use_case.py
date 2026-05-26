"""P7.80 read-only Sync Sentinel Agent v0.

This is an agent-shaped report, not an autonomous worker. It reads existing
sync, budget, alert, and P6.79 acceptance state, then returns prioritized
signals for an operator. It never writes alerts, acknowledges guards, starts
sync jobs, calls providers, or enqueues tasks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.db.connection import get_conn
from app.domains.sync.sentinel import (
    SENTINEL_VERSION,
    as_dict,
    as_float,
    build_sync_sentinel_report,
    signals_from_budgets,
    signals_from_open_alerts,
    signals_from_overview,
    signals_from_p6_79,
    sort_signals,
    text,
)
from app.domains.sync import sync_status


DEFAULT_OPS_DIR = "runtime/ops"
P6_79_PATTERN = "*p6-79-brain-layer-acceptance-v0.json"
logger = get_logger(__name__)


def _table_exists(table_name: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table_name,)).fetchone()
        if row:
            return True
    except Exception:
        logger.debug("SQLite table lookup failed for %s; trying information_schema fallback", table_name, exc_info=True)
    try:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _latest_artifact(ops_dir: str, pattern: str) -> Path | None:
    root = Path(ops_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates = [path for path in root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)[0]


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.debug("Failed to load sync sentinel artifact JSON from %s", path, exc_info=True)
        return {}


def _latest_p6_79(ops_dir: str) -> dict[str, Any]:
    path = _latest_artifact(ops_dir, P6_79_PATTERN)
    payload = _load_json(path)
    return {
        "loaded": bool(path and payload),
        "artifact_path": str(path) if path else "",
        "artifact_name": path.name if path else "",
        "report": payload,
        "summary": as_dict(payload.get("summary")),
    }


def _budget_caps() -> dict[str, Any]:
    if not _table_exists("vkpi_provider_budget_caps"):
        return {"configured": False, "budgets": [], "summary": {"scopes": 0, "warnings": 0, "hard_stopped": 0}}
    rows = get_conn().execute(
        """
        SELECT scope, cap_usd, current_spend, warning_at, hard_stop_at, reset_at, fallback_action
        FROM vkpi_provider_budget_caps
        ORDER BY scope
        """
    ).fetchall()
    budgets: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        cap = as_float(item.get("cap_usd"))
        spend = as_float(item.get("current_spend"))
        warning_at = as_float(item.get("warning_at"), 0.8)
        hard_stop_at = as_float(item.get("hard_stop_at"), 1.0)
        usage_ratio = (spend / cap) if cap > 0 else 0.0
        budgets.append(
            {
                "scope": item.get("scope"),
                "cap_usd": cap,
                "current_spend": spend,
                "usage_ratio": round(usage_ratio, 4),
                "warning_at": warning_at,
                "hard_stop_at": hard_stop_at,
                "warning": cap > 0 and usage_ratio >= warning_at,
                "hard_stopped": cap > 0 and usage_ratio >= hard_stop_at,
                "reset_at": item.get("reset_at"),
                "fallback_action": item.get("fallback_action") or "",
            }
        )
    return {
        "configured": True,
        "budgets": budgets,
        "summary": {
            "scopes": len(budgets),
            "warnings": sum(1 for item in budgets if item.get("warning")),
            "hard_stopped": sum(1 for item in budgets if item.get("hard_stopped")),
            "current_spend_usd": round(sum(as_float(item.get("current_spend")) for item in budgets), 4),
        },
    }


def _open_alerts(limit: int) -> dict[str, Any]:
    safe_limit = max(1, min(200, int(limit or 50)))
    if not _table_exists("vkpi_alerts"):
        return {"configured": False, "alerts": [], "summary": {"open_total": 0, "critical_open": 0, "warning_open": 0}}
    rows = get_conn().execute(
        """
        SELECT id, alert_key, rule_key, severity, status, target_type, target_id,
               title, created_at, updated_at
        FROM vkpi_alerts
        WHERE status='open'
        ORDER BY
          CASE severity WHEN 'danger' THEN 0 WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
          updated_at DESC,
          id DESC
        LIMIT ?
        """,
        (safe_limit,),
    ).fetchall()
    alerts = [dict(row) for row in rows]
    return {
        "configured": True,
        "alerts": alerts,
        "summary": {
            "open_total": len(alerts),
            "critical_open": sum(1 for row in alerts if text(row.get("severity")).lower() in {"danger", "critical"}),
            "warning_open": sum(1 for row in alerts if text(row.get("severity")).lower() == "warning"),
        },
    }


def build_sync_sentinel_agent_v0(*, ops_dir: str = DEFAULT_OPS_DIR, limit: int = 50) -> dict[str, Any]:
    safe_limit = max(1, min(200, int(limit or 50)))
    overview = sync_status.get_overview()
    budgets = _budget_caps()
    open_alerts = _open_alerts(safe_limit)
    p6_79 = _latest_p6_79(ops_dir)
    signals = sort_signals(
        signals_from_overview(overview)
        + signals_from_budgets(budgets)
        + signals_from_open_alerts(open_alerts)
        + signals_from_p6_79(p6_79),
        safe_limit,
    )
    return build_sync_sentinel_report(
        overview=overview,
        budgets=budgets,
        open_alerts=open_alerts,
        p6_79=p6_79,
        signals=signals,
        ops_dir=ops_dir,
        limit=safe_limit,
        p6_79_pattern=P6_79_PATTERN,
    )


__all__ = [
    "DEFAULT_OPS_DIR",
    "P6_79_PATTERN",
    "SENTINEL_VERSION",
    "_budget_caps",
    "_latest_p6_79",
    "_open_alerts",
    "build_sync_sentinel_agent_v0",
    "sync_status",
]
